"""
天翼云电脑 Web 管理面板 - 后端服务
提供设置、设备配置、定时任务、积分兑换等 API
"""

import json
import os
import random
import re
import secrets
import string
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# Try Flask, fall back to http.server if not available
try:
    from flask import Flask, jsonify, request, send_from_directory, Response
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

# ============================================
# Constants
# ============================================
# 桌面版适配：统一从公共路径模块取数据目录与临时目录，替代 Docker 版的
# /app/data、/tmp、/etc/cron.d 硬编码；paths.py 位于 web_server 的上一级。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import DATA_DIR as _PATHS_DATA_DIR, tmp_path, APP_DIR  # noqa: E402

# 优先级：环境变量 DATA_DIR > 公共路径模块（exe 同级 data/）。
# 注：原 Docker 版默认值 "/app/data" 仅在容器内成立，桌面版改由 paths 模块决定。
DATA_DIR = os.environ.get("DATA_DIR") or _PATHS_DATA_DIR
SETTINGS_FILE = os.path.join(DATA_DIR, "web_settings.json")
LOG_FILE = os.path.join(DATA_DIR, "web_panel.log")
KEEPALIVE_LOG_FILE = os.path.join(DATA_DIR, "ctyun_keepalive.log")
REWARDS_JSON = os.path.join(DATA_DIR, "rewards.json")
DEVICECODE_FILE_PREFIX = os.path.join(DATA_DIR, ".devicecode_")
REDEEM_CONFIG_FILE = os.path.join(DATA_DIR, "redeem_config.json")
# 桌面版适配：容器内曾用 /etc/cron.d，桌面版改存数据目录（仅作历史兼容，调度已由线程驱动）
CRON_FILE = os.path.join(DATA_DIR, "ctyun-cron.txt")
# 保活重载标记：保存保活配置后写入，entrypoint/桌面启动器守护循环检测到后立即应用新配置。
# 补全：备用 HTTP 服务器保存路径此前漏写该标记，保活配置变更不及时生效。
# 桌面版适配：/tmp 改为数据目录下 .tmp（与 pc_login 的 tmp_path 一致）。
KEEPALIVE_RELOAD_FLAG = tmp_path("ctyun_reload")

# 确保数据目录存在，并统一为公共路径模块的数据目录（与 pc_login.py 的 HANG_STATUS_FILE 一致）
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    pass

# 桌面版适配：本进程启动时刻（Windows 下 psutil 不可用时用于近似计算 uptime）
_PROCESS_START_TS = time.time()

# ============================================
# Settings Management
# ============================================
DEFAULT_SETTINGS = {
    "username": "",
    "password": "",
    "preset_messages": [
        "今天北京天气怎么样？（简短回答）",
        "给我讲一个冷笑话。（简短回答）",
        "来一首古诗。（简短回答）",
        "空腹可以吃饭吗？（简短回答）",
        "推荐一部人生必看电影。（简短回答）",
    ],
    "device_code": "",
    "ai_cron": "0 3,20 * * *",
    "hang_cron": "0 4,6 * * *",
    "last_login": "",
    "updated_at": ""
}


def load_settings() -> dict:
    """Load settings from file."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults
            settings = DEFAULT_SETTINGS.copy()
            settings.update(data)
            return settings
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> None:
    """Save settings to file."""
    settings["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(DATA_DIR, exist_ok=True)
    # 修复：非原子写在写入中途崩溃/断电会留下半截 JSON，下次 load 容错回默认值
    # 导致账号/定时配置全部静默丢失。改为 tmp + os.replace 原子替换（与 save_web_settings 一致）。
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_FILE)


# ============================================
# DEVICECODE Management
# ============================================
def generate_device_code() -> str:
    """Generate a random device code."""
    chars = string.ascii_letters + string.digits
    random_part = ''.join(secrets.choice(chars) for _ in range(32))
    return f"web_{random_part}"


def get_device_code_file() -> str:
    """Get device code file path."""
    settings = load_settings()
    username = settings.get("username", "")
    return f"{DEVICECODE_FILE_PREFIX}{username}"


def load_device_code() -> dict:
    """Load current device code."""
    settings = load_settings()
    env_code = os.environ.get("DEVICECODE", "")
    file_path = get_device_code_file()
    file_code = ""
    source = "unknown"

    # Check settings first
    if settings.get("device_code"):
        return {
            "device_code": settings["device_code"],
            "source": "settings",
            "file_path": file_path,
            "persisted": True
        }

    # Check environment variable
    if env_code:
        return {
            "device_code": env_code,
            "source": "environment",
            "file_path": file_path,
            "persisted": True
        }

    # Check file
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                file_code = f.read().strip()
        except Exception:
            pass
        if file_code:
            return {
                "device_code": file_code,
                "source": "file",
                "file_path": file_path,
                "persisted": True
            }

    return {
        "device_code": "",
        "source": "none",
        "file_path": file_path,
        "persisted": False
    }


def save_device_code(code: str) -> None:
    """Save device code to settings and file."""
    # 修复：读-改-写（load→改→save）在并发下可能互相覆盖丢更新，套进程锁串行化。
    # 后续设备码文件/env 写入不涉及读-改-写，保持锁外执行以缩小临界区。
    with _SETTINGS_LOCK:
        settings = load_settings()
        settings["device_code"] = code
        save_settings(settings)

    # Also save to the device code file
    file_path = get_device_code_file()
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)

    # Update environment
    os.environ["DEVICECODE"] = code

    # Write to /etc/environment for cron processes
    # 桌面版适配：Windows 无 /etc/environment，仅 Linux 且文件存在时才写（保留容器兼容）
    try:
        env_file = "/etc/environment"
        if os.name != "nt" and os.path.exists(env_file):
            content = ""
            with open(env_file, "r") as f:
                content = f.read()
            # Remove old DEVICECODE line
            lines = [l for l in content.splitlines() if not l.startswith("DEVICECODE=")]
            lines.append(f"DEVICECODE={code}")
            with open(env_file, "w") as f:
                f.write("\n".join(lines) + "\n")
    except Exception:
        pass

    log_event("system", f"DEVICECODE 已保存: {code[:16]}...")


# ============================================
# Scheduler (傻瓜式时间点，由后端线程驱动，不依赖系统 cron)
# ============================================
def _cron_to_times(cron_expr: str):
    """把简单 cron 表达式 '0 3,20 * * *' 转换为 ['03:00','20:00']。"""
    times = []
    parts = (cron_expr or "").strip().split()
    if len(parts) != 5:
        return times
    minute, hour = parts[0], parts[1]
    minute = minute.split("/")[-1] if "/" in minute else minute
    minute = "0" if minute in ("*", "") else minute
    for h in hour.replace("*", "").split(","):
        h = h.strip()
        if h.isdigit():
            times.append(f"{int(h):02d}:{int(minute):02d}")
    return times


def get_cron_config() -> dict:
    """读取傻瓜式定时任务配置（兼容旧 cron 字段）。

    关键修复：新字段（ai_chat_time / pc_hang_time）若存在则**无条件优先**，
    旧字段（ai_cron / hang_cron）仅当新字段完全缺失时才作为兜底，
    避免旧 cron 默认值（0 3,20 / 0 4,6）覆盖用户在 Web 面板设置的时间点。
    """
    settings = load_settings()
    # 新字段优先；仅在字段完全缺失时回退到兜底
    ai_time = settings.get("ai_chat_time")
    if not isinstance(ai_time, list) or not ai_time:
        ai_time = _cron_to_times(settings.get("ai_cron", "")) if settings.get("ai_cron") else ["03:00", "20:00"]
    hang_time = settings.get("pc_hang_time")
    if not isinstance(hang_time, list) or not hang_time:
        hang_time = _cron_to_times(settings.get("hang_cron", "")) if settings.get("hang_cron") else ["04:00", "06:00"]
    cfg = {
        "ai_chat_enabled": settings.get("ai_chat_enabled", True),
        "ai_chat_time": ai_time,
        "pc_hang_enabled": settings.get("pc_hang_enabled", True),
        "pc_hang_time": hang_time,
        "hang_minutes": settings.get("hang_minutes", 80),
        "keepalive_seconds": settings.get("keepalive_seconds", 900),
        "keepalive_enabled": settings.get("keepalive_enabled", True),
        "silent_mode": settings.get("silent_mode", False),
        "browser_watch": settings.get("browser_watch", False),
        "points_refresh_hours": settings.get("points_refresh_hours", 8)
    }
    return cfg


def save_cron_config(ai_enabled: bool, ai_time: list, hang_enabled: bool, hang_time: list,
                     hang_minutes: int = 80, keepalive_seconds: int = 900,
                     keepalive_enabled: bool = True, silent_mode: bool = False,
                     browser_watch: bool = False, points_refresh_hours: int = 8) -> None:
    """保存傻瓜式定时任务配置（不再写系统 cron 文件，由后端调度线程驱动）。"""
    # 修复：读-改-写（load→改→save）在并发下可能互相覆盖丢更新（Flask 多线程
    # + 调度线程同时写 settings），套进程锁串行化。锁内仅调用无锁函数，
    # _load/_save_scheduler_state 用独立 _SCHED_LOCK、_sync_accounts_json 只读，均无死锁风险。
    with _SETTINGS_LOCK:
        settings = load_settings()
        # 修复：旧值必须在修改 settings 前读取（原实现在修改后再读"旧值"是死代码，
        # "时间变更→重置今日标记"从未真正生效）。进入锁后、任何写入前先快照旧值。
        old_ai = settings.get("ai_chat_time")
        if not isinstance(old_ai, list):
            old_ai = []
        old_hang = settings.get("pc_hang_time")
        if not isinstance(old_hang, list):
            old_hang = []
        settings["ai_chat_enabled"] = bool(ai_enabled)
        settings["ai_chat_time"] = ai_time or []
        settings["pc_hang_enabled"] = bool(hang_enabled)
        settings["pc_hang_time"] = hang_time or []
        settings["hang_minutes"] = int(hang_minutes) if hang_minutes else 80
        settings["keepalive_seconds"] = int(keepalive_seconds) if keepalive_seconds else 900
        settings["keepalive_enabled"] = bool(keepalive_enabled)
        settings["silent_mode"] = bool(silent_mode)
        settings["browser_watch"] = bool(browser_watch)
        settings["points_refresh_hours"] = int(points_refresh_hours) if points_refresh_hours else 8
        # 清理旧 cron 字段
        settings.pop("ai_cron", None)
        settings.pop("hang_cron", None)
        ai_time_changed = set(old_ai) != set(ai_time or [])
        hang_time_changed = set(old_hang) != set(hang_time or [])
        if ai_time_changed or hang_time_changed:
            try:
                _load_scheduler_state()
                # 修复：时间变更后重置对应任务的今日已触发标记（时间点级）。
                # 原 bug：只重置 AI 侧且被死代码挡住从未生效；挂机侧完全没有重置，
                # 用户白天改了时间，当天新时间点到点也不会执行。
                if ai_time_changed:
                    _scheduler_state["ai_hits"] = []
                    _scheduler_state["last_ai_day"] = ""
                if hang_time_changed:
                    _scheduler_state["hang_hits"] = []
                    _scheduler_state["last_hang_day"] = ""
                _save_scheduler_state()
                log_event("scheduler", f"定时时间已变更（AI 旧={old_ai} 新={ai_time}，挂机 旧={old_hang} 新={hang_time}），已重置当日执行标记")
            except Exception:
                pass
        save_settings(settings)
        # 同步保活间隔到 accounts.json（CtYun.dll 实际读取的文件），让保活时间立即生效
        _sync_accounts_json(settings)
    log_event("system", f"定时任务已更新(调度线程): AI={ai_time}, 挂机={hang_time}, 挂机时长={settings['hang_minutes']}分钟")


def _sync_accounts_json(settings: dict = None) -> None:
    """把保活间隔同步写入 accounts.json（供 CtYun.dll 读取），与 entrypoint 的 sync_accounts_json 等价。

    修复：保活时间在前端修改后，除了写 /tmp/ctyun_reload 等 entrypoint 下一轮同步外，
    这里直接同步一次，避免依赖 entrypoint 循环周期导致"保活时间改了不立即生效"。
    """
    try:
        if settings is None:
            settings = load_settings()
        username = settings.get("username", "") or "default"
        password = settings.get("password", "") or ""
        device_code = settings.get("device_code", "") or ""
        keepalive = int(settings.get("keepalive_seconds", 900) or 900)
        if keepalive < 10:
            keepalive = 10
        accounts = {
            "accounts": [
                {
                    "name": username or "default",
                    "user": username,
                    "password": password,
                    "deviceCode": device_code,
                }
            ],
            "keepAliveSeconds": keepalive,
        }
        accounts_file = os.path.join(DATA_DIR, "accounts.json")
        # 修复：非原子写在写入中途崩溃会留下半截 JSON，CtYun.dll 读到损坏配置。
        # 改为 tmp + os.replace 原子替换。
        tmp_accounts = accounts_file + ".tmp"
        with open(tmp_accounts, "w", encoding="utf-8") as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)
        os.replace(tmp_accounts, accounts_file)
    except Exception:
        pass


# ============================================
# Redeem Configuration
# ============================================
def _normalize_schedule_type(raw) -> tuple:
    """统一兑换周期类型取值。

    历史遗留：前端旧版发送 daily/interval/weekly/monthly，脚本端只认
    daily/interval_days/monthly_days。这里把两套取值归一化：
      daily         -> daily
      interval      -> interval_days
      weekly        -> weekly_days（按间隔 7 天实现）
      monthly       -> monthly_days
    返回 (归一化类型, 前端展示类型)。
    """
    raw = str(raw or "daily").strip().lower()
    mapping = {
        "daily": "daily",
        "interval": "interval_days",
        "interval_days": "interval_days",
        "weekly": "weekly_days",
        "weekly_days": "weekly_days",
        "monthly": "monthly_days",
        "monthly_days": "monthly_days",
    }
    backend_type = mapping.get(raw, "daily")
    display_type = {
        "daily": "daily",
        "interval_days": "interval",
        "weekly_days": "weekly",
        "monthly_days": "monthly",
    }[backend_type]
    return backend_type, display_type


def get_redeem_config() -> dict:
    """Get current redeem configuration."""
    if os.path.exists(REDEEM_CONFIG_FILE):
        try:
            with open(REDEEM_CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            return config
        except Exception:
            pass
    return {}


def save_redeem_config(config: dict) -> None:
    """Save redeem configuration."""
    os.makedirs(os.path.dirname(REDEEM_CONFIG_FILE), exist_ok=True)
    # 修复：非原子写在写入中途崩溃/断电会留下半截 JSON，兑换计划静默丢失。
    # 改为 tmp + os.replace 原子替换。
    tmp = REDEEM_CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    os.replace(tmp, REDEEM_CONFIG_FILE)
    log_event("system", "积分兑换配置已保存")


def _build_redeem_config_from_request(data: dict, keep_last_date: bool = True) -> dict:
    """根据前端请求构造标准化的兑换配置（Flask 与 Fallback 两种服务器共用）。

    统一修复：
    1. scheduleType 取值归一化（前端 daily/interval/weekly/monthly -> 后端标准取值）。
    2. weekly 周期真实支持（按每 7 天间隔执行），不再是后端不认识的值。
    3. 保留 lastRedeemDate：编辑配置不应重置兑换计划进度，
       否则"每天兑换"会被误判为今天还没兑换、当天再次下单。
    """
    backend_type, _ = _normalize_schedule_type(data.get("schedule_type"))
    old_config = get_redeem_config() if keep_last_date else {}
    config = {
        "enabled": bool(data.get("enabled", False)),
        "scheduleType": backend_type,
        "prodId": int(data.get("prod_id", 0) or 0),
        "prodName": str(data.get("prod_name", "") or ""),
        "prodType": str(data.get("prod_type", "") or ""),
        "costPoints": int(data.get("cost_points", 0) or 0),
        "maxRedeemTimes": int(data.get("max_redeem_times", 0) or 0),
        "desktopId": str(data.get("desktop_id", "") or ""),
        "lastRedeemDate": (old_config.get("lastRedeemDate", "") if keep_last_date else ""),
    }
    if backend_type == "interval_days":
        config["intervalDays"] = max(1, int(data.get("interval_days", 1) or 1))
    elif backend_type == "weekly_days":
        # 每周 = 每 7 天间隔执行（首次立即允许，之后间隔满 7 天）
        config["intervalDays"] = 7
    elif backend_type == "monthly_days":
        days = data.get("monthly_days", [])
        if isinstance(days, list):
            days = [int(d) for d in days if str(d).strip().lstrip("-").isdigit()]
        else:
            days = []
        config["monthlyDays"] = days
    return config


# ============================================
# Log Management
# ============================================
def log_event(task_type: str, message: str) -> None:
    """Append log event to log file."""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{task_type}] {message}\n")
    except Exception:
        pass


def get_logs(log_type: str = "all", limit: int = 200) -> list:
    """Read logs from file. Supports keepalive log type."""
    if log_type in ("keepalive", "keepalive_log"):
        file_path = KEEPALIVE_LOG_FILE
        if not os.path.exists(file_path):
            return ["暂无保活日志（CtYun.dll 尚未输出日志）"]
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            filtered = [l.rstrip("\n") for l in lines if l.strip()]
            return filtered[-limit:] if filtered else ["暂无保活日志"]
        except Exception:
            return ["读取保活日志失败"]
    if log_type == "redeem_log":
        file_path = os.path.join(DATA_DIR, "redeem_log.log")
        if not os.path.exists(file_path):
            return ["暂无兑换日志（尚未执行过手动兑换）"]
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            filtered = [l.rstrip("\n") for l in lines if l.strip()]
            return filtered[-limit:] if filtered else ["暂无兑换日志"]
        except Exception:
            return ["读取兑换日志失败"]
    # 独立任务日志文件（AI 对话 / 挂机 / 登录 / 退出等）
    standalone_log_map = {
        "ai_chat": "ai_chat.log",
        "pc_hang": "pc_hang.log",
        "login": "login_task.log",
        "logout": "logout.log",
    }
    if log_type in standalone_log_map:
        file_path = os.path.join(DATA_DIR, standalone_log_map[log_type])
        if not os.path.exists(file_path):
            return [f"暂无{log_type}日志（尚未执行过该任务）"]
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            filtered = [l.rstrip("\n") for l in lines if l.strip()]
            return filtered[-limit:] if filtered else [f"暂无{log_type}日志"]
        except Exception:
            return [f"读取{log_type}日志失败"]
    if not os.path.exists(LOG_FILE):
        return ["暂无日志"]
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if log_type != "all":
            filtered = [l.strip() for l in lines if f"[{log_type}]" in l]
        else:
            filtered = [l.strip() for l in lines]
        return filtered[-limit:] if filtered else ["暂无日志"]
    except Exception:
        return ["读取日志失败"]


def clear_logs() -> None:
    """Clear all logs (web panel log + keepalive log + all standalone task logs)."""
    try:
        # 所有需要清空的日志文件
        log_files = [
            LOG_FILE,
            KEEPALIVE_LOG_FILE,
            os.path.join(DATA_DIR, "ai_chat.log"),
            os.path.join(DATA_DIR, "pc_hang.log"),
            os.path.join(DATA_DIR, "login_task.log"),
            os.path.join(DATA_DIR, "logout.log"),
            os.path.join(DATA_DIR, "redeem_log.log"),
        ]
        for fpath in log_files:
            try:
                if os.path.exists(fpath):
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write("")
            except Exception:
                pass
        log_event("system", "全部日志已清空")
    except Exception:
        pass


# ============================================
# Rewards data (redeemable items list)
# ============================================
def load_rewards_summary() -> dict:
    """Return lightweight rewards summary for dashboard (points only)."""
    data = load_rewards_data()
    return {
        "exists": data.get("exists", False),
        "points": data.get("points", 0),
        "count": len(data.get("rewards", [])),
        "timestamp": data.get("timestamp", "")
    }


def load_points_history() -> list:
    """返回积分历史（供趋势图），按时间正序，最多最近 300 条。

    兼容旧数据：entry 缺少 delta 字段时根据前后记录补算，保证前端涨跌明细可用。
    """
    history_file = os.path.join(DATA_DIR, "points_history.json")
    if not os.path.exists(history_file):
        return []
    try:
        with open(history_file, "r", encoding="utf-8") as f:
            history = json.load(f)
        if not isinstance(history, list):
            return []
        history = [h for h in history if isinstance(h, dict) and "points" in h]
        history.sort(key=lambda h: h.get("ts", ""))
        prev = None
        for h in history:
            pts = int(h.get("points") or 0)
            if h.get("delta") is None:
                h["delta"] = (pts - prev) if prev is not None else 0
            prev = pts
        return history[-300:]
    except Exception:
        return []


# 修复：删除死代码 _cron_field_matches——已确认全项目无调用方（调度线程改用
# 时间点列表 _next_run_from_times，deploy_cron.sh 为独立 shell 脚本不受影响）。


def _next_run_from_times(times: list, enabled: bool) -> str:
    """根据时间点列表（['03:00','20:00']）计算下一次执行时间，返回 'YYYY-MM-DD HH:MM:SS' 或空串。"""
    if not enabled or not times:
        return ""
    now = datetime.now()
    candidates = []
    for t in times:
        try:
            hh, mm = str(t).split(":")
            target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            continue
        if target <= now:
            target = target + timedelta(days=1)
        candidates.append(target)
    if not candidates:
        return ""
    nxt = min(candidates)
    return nxt.strftime("%Y-%m-%d %H:%M:%S")


# ============================================
# 后端调度线程（替代系统 cron，复用 execute_task）
# ============================================
# 每日执行标记持久化到磁盘，避免进程/容器重启后内存状态丢失导致"补触发错时"。
_SCHEDULER_STATE_FILE = os.path.join(DATA_DIR, "scheduler_state.json")
_scheduler_state = {
    "running": False,
    "last_ai_day": "",
    "last_hang_day": "",
    # 修复：原实现仅用日级标记（last_ai_day/last_hang_day）防重复，一天内
    # 执行过一次后，同一天的其余时间点全部被吞掉（用户设 04:00+06:00 只跑
    # 04:00；AI 设 03:00+20:00 只跑 03:00）。改为「日期+时间点」级标记：
    # *_hits 列表存放今日已触发的时间点，每个时间点独立计次。
    "ai_hits": [],
    "hang_hits": [],
    "_loaded": False,
}
# 修复：调度器状态读写此前无锁，与 get_system_status 的并发访问存在竞争，统一串行化
_SCHED_LOCK = threading.Lock()


def _migrate_scheduler_state(data: dict) -> None:
    """兼容迁移旧日级状态到新的时间点级格式。

    旧状态只有 last_ai_day/last_hang_day（日期字符串）。升级后读取时若发现
    旧键，把它转换成对应日期的“全部时间点已执行”语义会引入误判，因此仅
    保留原日级键做展示兼容，不参与新的去重判断。
    """
    # 空列表防御：非 list 或缺失一律视为 []
    if not isinstance(data.get("ai_hits"), list):
        data["ai_hits"] = []
    if not isinstance(data.get("hang_hits"), list):
        data["hang_hits"] = []


def _load_scheduler_state():
    """加载调度器状态并返回状态 dict。

    修复：原实现漏写 return，永远返回 None，导致 get_system_status 里
    cookie 失效通知的 state.get(...) 全部落空、标志位从未生效（死代码）。
    同时恢复 cookie_expired_notified 标志（重启后不重复推送）。
    """
    global _scheduler_state
    with _SCHED_LOCK:
        try:
            if os.path.exists(_SCHEDULER_STATE_FILE):
                with open(_SCHEDULER_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                _scheduler_state["last_ai_day"] = data.get("last_ai_day", "")
                _scheduler_state["last_hang_day"] = data.get("last_hang_day", "")
                _scheduler_state["cookie_expired_notified"] = bool(data.get("cookie_expired_notified", False))
                # 兼容读取时间点级新键；非 list/缺失时迁移为空列表
                _migrate_scheduler_state(data)
                _scheduler_state["ai_hits"] = data["ai_hits"]
                _scheduler_state["hang_hits"] = data["hang_hits"]
        except Exception:
            pass
        _scheduler_state["_loaded"] = True
        return _scheduler_state  # 修复：补上漏掉的 return（此前调用方永远拿到 None）


def _save_scheduler_state(state=None):
    """持久化调度器状态。state 缺省时保存全局状态。

    修复：原实现不接收参数，get_system_status 里 _save_scheduler_state(state)
    传参调用必然 TypeError 且被 except 吞掉，cookie_expired_notified 标志
    从未持久化。现接收可选参数并持久化该标志。
    """
    global _scheduler_state
    with _SCHED_LOCK:
        st = state if isinstance(state, dict) else _scheduler_state
        try:
            tmp = _SCHEDULER_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "last_ai_day": st.get("last_ai_day", ""),
                    "last_hang_day": st.get("last_hang_day", ""),
                    # 新增：时间点级标记（今日已触发的时间点列表），跨天自动清理
                    "ai_hits": st.get("ai_hits", []),
                    "hang_hits": st.get("hang_hits", []),
                    "cookie_expired_notified": bool(st.get("cookie_expired_notified", False)),
                }, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _SCHEDULER_STATE_FILE)
        except Exception:
            pass


def _now_hhmm(now: datetime) -> str:
    return now.strftime("%H:%M")


def _is_within_trigger_window(t: str, now: datetime, window_min: int = 5) -> bool:
    """判断 now 是否落在设定时间点 t 的触发窗口内（t 之后 0 ~ window_min 分钟）。

    配合 30s 轮询：只要在设定分钟及其后 5 分钟内任一次 tick 命中即触发，
    既精确按设定时间，又不会因为单次轮询偏移而漏掉。
    """
    try:
        hh, mm = str(t).split(":")
        target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except Exception:
        return False
    delta = (now - target).total_seconds()
    return 0 <= delta <= window_min * 60


def _scheduler_tick(startup_grace: bool = False):
    """每 30 秒检查：精确命中某个设定时间点（窗口内）且今日尚未执行，则触发。

    效率优化（合并 AI 对话 + 挂机）：
    - 当 AI 对话与云电脑挂机**同时启用**时，改在「挂机设定时间」触发一次 combined 任务
      （单浏览器会话内先 AI 对话、后挂机），避免两个任务各起一个 Chromium 的重复开销。
    - AI 单独设定时间不再单独起 Chromium（合并已在挂机时刻完成对话与积分领取）。
    - 仅启用其一（如只开 AI 不开挂机）时，仍按原逻辑单独触发对应任务。

    修复要点：
    1. 只在设定分钟窗口内触发，真正按用户设置的时间执行。
    2. 每日执行标记持久化到磁盘（scheduler_state.json），进程重启不丢失。
    3. startup_grace=True（进程刚启动那次）：对今天已过的设定时间中、最近一个且距现在
       ≤ 30 分钟、且今日未执行的，补触发一次。
    """
    if not _scheduler_state.get("_loaded"):
        _load_scheduler_state()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    try:
        cfg = get_cron_config()
        ai_enabled = bool(cfg.get("ai_chat_enabled"))
        hang_enabled = bool(cfg.get("pc_hang_enabled"))
        ai_times = cfg.get("ai_chat_time") or []
        hang_times = cfg.get("pc_hang_time") or []
        hang_min = int(cfg.get("hang_minutes", 80) or 80)

        # 修复：原 _hit 用日级标记（last_ai_day/last_hang_day）判断“今日是否
        # 已执行”，一天内首个时间点触发后其余时间点全被吞掉。改为时间点级：
        # _hit(t) 判断该具体时间点是否落在触发窗口内且今日尚未执行；
        # _mark_hit(t) 只标记该时间点，不影响其他时间点。跨天由日期前缀
        # 自动区分（今晨的标记到明天自然失效）。
        def _today_hits(hits_key):
            """从带日期前缀的记录中解析出「今日」已触发的时间点集合。
            记录格式为 'YYYY-MM-DD|HH:MM'，跨天记录自然被排除。"""
            prefix = today + "|"
            return {x[len(prefix):] for x in _scheduler_state.get(hits_key, []) if isinstance(x, str) and x.startswith(prefix)}

        def _hit(times, hits_key):
            """返回当前可触发的时间点（落在窗口内且今日未执行），无则返回 None。"""
            done_today = _today_hits(hits_key)
            for t in times:
                if t in done_today:
                    continue
                if _is_within_trigger_window(t, now):
                    return t
            if startup_grace:
                for t in times:
                    if t in done_today:
                        continue
                    try:
                        hh, mm = str(t).split(":")
                        target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                        late = (now - target).total_seconds()
                        if 0 < late <= 30 * 60:
                            return t
                    except Exception:
                        continue
            return None

        def _mark_hit(hits_key, t):
            """把时间点 t 记入今日已触发列表（跨天记录自动清理）。"""
            prefix = today + "|"
            # 只保留今天的记录，追加本次触发的时间点
            _scheduler_state[hits_key] = [x for x in _scheduler_state.get(hits_key, []) if isinstance(x, str) and x.startswith(prefix)]
            _scheduler_state[hits_key].append(f"{prefix}{t}")
            # 同步维护旧日级键（仅展示兼容，不参与去重）
            _scheduler_state["last_ai_day" if hits_key == "ai_hits" else "last_hang_day"] = today
            _save_scheduler_state()

        # 计算各自当前可触发的时间点（None 表示暂无可触发项）
        ai_hit_time = _hit(ai_times, "ai_hits") if ai_enabled else None
        hang_hit_time = _hit(hang_times, "hang_hits") if hang_enabled else None

        # 合并触发仅在 AI 与挂机「时间点完全一致」时才进行（同一时刻单会话完成对话+挂机）。
        # 若两者时间不同，则各自按自己的设定时间单独触发，避免互相污染执行标记
        # 修复：合并触发条件应为「AI 与挂机时间点完全一致」；旧写法 set 交集非空即触发，
        # 会把 AI 设 03:00/21:00、挂机设 04:00 时也判为可合并，导致 AI 的 21:00 被吞掉不执行。
        same_time = bool(set(ai_times) == set(hang_times) and ai_times)

        if hang_hit_time is not None and ai_enabled and same_time:
            # 合并触发：单会话完成 AI 对话 + 挂机，省去一次 Chromium 冷启动
            # 修复：只标记被合并触发的那个时间点，AI/挂机其余时间点照常各自触发
            _mark_hit("hang_hits", hang_hit_time)
            _mark_hit("ai_hits", hang_hit_time)  # 同时间合并，确实执行了对话
            log_event("scheduler", f"触发合并任务（AI对话+挂机，设定时间 {hang_hit_time}，时长 {hang_min} 分钟）")
            try:
                execute_task("combined", {"minutes": hang_min})
            except Exception as e:
                log_event("scheduler", f"合并任务执行失败: {e}")
                notify_event("hang_fail", "云电脑挂机失败", f"合并任务（AI对话+挂机）执行失败：{e}")
        else:
            # 分别按各自时间触发，互不标记
            if ai_hit_time is not None:
                _mark_hit("ai_hits", ai_hit_time)
                log_event("scheduler", f"触发 AI 对话任务（设定时间 {ai_hit_time}）")
                try:
                    execute_task("ai_chat")
                except Exception as e:
                    log_event("scheduler", f"AI 对话任务执行失败: {e}")
            if hang_hit_time is not None:
                _mark_hit("hang_hits", hang_hit_time)
                log_event("scheduler", f"触发云电脑挂机任务（设定时间 {hang_hit_time}，时长 {hang_min} 分钟）")
                try:
                    execute_task("pc_hang", {"minutes": hang_min})
                except Exception as e:
                    log_event("scheduler", f"云电脑挂机任务执行失败: {e}")
                    notify_event("hang_fail", "云电脑挂机失败", f"挂机任务执行失败：{e}")
    except Exception as e:
        log_event("scheduler", f"调度检查异常: {e}")
        notify_event("sys_error", "系统调度异常", f"调度线程异常：{e}")


def _scheduler_loop():
    _scheduler_state["running"] = True
    _load_scheduler_state()
    log_event("scheduler", f"调度线程已启动（替代系统 cron），AI={get_cron_config().get('ai_chat_time')}，挂机={get_cron_config().get('pc_hang_time')}")
    # 启动后首次 tick 带宽限补触发
    try:
        _scheduler_tick(startup_grace=True)
    except Exception:
        pass
    while True:
        try:
            _scheduler_tick()
        except Exception:
            pass
        time.sleep(30)


def load_rewards_data() -> dict:
    """Load rewards.json content produced by pc_login.py --fetch-rewards."""
    if not os.path.exists(REWARDS_JSON):
        return {
            "exists": False,
            "timestamp": "",
            "mobile": "",
            "points": 0,
            "rewards": [],
            "desktops": [],
            "message": "尚未抓取可兑换物品，请点击「加载可兑换物品」",
        }
    try:
        with open(REWARDS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["exists"] = True
        return data
    except Exception as e:
        return {
            "exists": False,
            "timestamp": "",
            "mobile": "",
            "points": 0,
            "rewards": [],
            "desktops": [],
            "message": f"读取可兑换物品失败: {e}",
        }


# ============================================
# System Status
# ============================================
def _read_hang_status() -> dict:
    """读取挂机状态文件（由 pc_login.py 写入）。"""
    hang_file = os.path.join(DATA_DIR, "hang_status.json")
    if not os.path.exists(hang_file):
        return {"running": False, "status": "未挂机", "message": "尚未开始挂机"}
    try:
        with open(hang_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {"running": False, "status": "未知", "message": "挂机状态读取失败"}


def _write_hang_status_safe(status: dict) -> None:
    """安全写回挂机状态文件（供状态自愈时修正）。"""
    try:
        hang_file = os.path.join(DATA_DIR, "hang_status.json")
        with open(hang_file, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_system_status() -> dict:
    """Get system and container status."""
    settings = load_settings()
    device_info = load_device_code()
    redeem_config = get_redeem_config()
    cron_config = get_cron_config()

    # Check running state.
    # 保活由 .NET(CtYun.dll) 在宿主机运行并写入 ctyun_keepalive.log，
    # Flask 运行在容器内，无法跨命名空间 pgrep 宿主机进程，
    # 因此改用「保活日志近期是否被写入」来判定保活是否在运行。
    # keepalive_seconds 默认值统一为 900（与 get_cron_config / 前端预设保持一致）
    keepalive_seconds = int(cron_config.get("keepalive_seconds", 900) or 900)
    keepalive_enabled = bool(cron_config.get("keepalive_enabled", True))
    ctyun_running = False
    # 修复：保活开关关闭时（最长 6 小时断开窗口内）日志仍是新的，
    # 仅凭"日志近期被写入"会误报"运行中"。开关关闭时直接判定未运行。
    if not keepalive_enabled:
        ctyun_running = False
    else:
        try:
            if os.path.exists(KEEPALIVE_LOG_FILE):
                mtime = os.path.getmtime(KEEPALIVE_LOG_FILE)
                # 超过 keepalive 间隔 + 600s 无写入则视为未运行。
                # 注：一轮完整保活周期 = 连接窗口 300s + 断开窗口 keepalive_seconds，
                # 判定阈值需覆盖完整周期再加缓冲，否则误判「未运行」。
                threshold = 300 + keepalive_seconds + 600
                ctyun_running = (time.time() - mtime) <= threshold
        except Exception:
            pass

    # 调度器线程是否在运行（替代系统 cron 守护进程）
    scheduler_running = bool(_scheduler_state.get("running", False))

    # Check for auth/cookie files
    username = settings.get("username", "")
    cookie_file = os.path.join(DATA_DIR, f"ctyun_cookies_{username}_.json")
    auth_file = os.path.join(DATA_DIR, f"ctyun_authData_{username}_.json")

    # Uptime（进程启动至今的时长）。
    # 原 Docker 版：从 /proc/uptime + /proc/1/stat 推算容器主进程启动时间（仅 Linux）。
    # 桌面版适配：Windows 无 /proc，改用 psutil 的 create_time()；
    # psutil 不可用时回退到本进程解释器启动时刻近似值。
    uptime = "未知"
    try:
        if os.name == "nt":
            try:
                import psutil  # PyInstaller 打包时随包分发

                uptime_seconds = max(0, time.time() - psutil.Process().create_time())
            except Exception:
                uptime_seconds = time.time() - _PROCESS_START_TS
        else:
            with open("/proc/uptime", "r") as f:
                system_uptime = float(f.read().split()[0])
            with open("/proc/1/stat", "r") as f:
                stat = f.read().split()
            # Field 22 (0-indexed 21) = start_time in clock ticks since boot
            clk_ticks = os.sysconf("SC_CLK_TCK") or 100
            pid1_start_since_boot = int(stat[21]) / clk_ticks
            boot_epoch = time.time() - system_uptime
            container_start_epoch = boot_epoch + pid1_start_since_boot
            uptime_seconds = max(0, time.time() - container_start_epoch)
        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        uptime = f"{hours}h {minutes}m"
    except Exception:
        pass

    # Hang status (progress of current hang task)
    hang_status = _read_hang_status()
    hang_running = hang_status.get("running", False)
    # 自愈：正常挂机时状态文件每 ~10 秒刷新一次 updated。
    # 若标记 running:true 但 updated 距今已超过「真实配置时长 + 10 分钟」，
    # 且至少为 15 分钟（避免任何正常刷新间隙误判），说明进程已异常退出、
    # 未走收尾逻辑，状态文件停留在「挂机中」——此时强制判定已结束并清零，
    # 避免前端一直误导显示「挂机中」。
    # total_minutes 一律以用户真实配置（hang_minutes）为准，不用残留文件里的旧值。
    if hang_running:
        try:
            real_total = int(cron_config.get("hang_minutes", 80) or 80)
            updated_str = hang_status.get("updated", "")
            # updated 缺失或无法解析 -> 直接判定陈旧
            try:
                updated_ts = datetime.strptime(updated_str, "%Y-%m-%d %H:%M:%S").timestamp()
                stale_minutes = (time.time() - updated_ts) / 60.0
            except Exception:
                stale_minutes = 999
            # 正常挂机时状态文件每 ~10 秒刷新一次 updated。
            # 只要 running:true 但 updated 距今 > 10 分钟（或缺失），
            # 即可确定进程已异常退出、未走收尾，立即清零，避免误导显示「挂机中」。
            if stale_minutes > 10:
                log_event("scheduler", f"挂机状态自愈：状态停留 {stale_minutes:.0f} 分钟未更新（配置 {real_total} 分钟），判定进程已退出并清零")
                hang_status = {
                    "running": False,
                    "status": "挂机已结束",
                    "message": "挂机进程已退出，状态已自动恢复",
                    "elapsed_minutes": real_total,
                    "total_minutes": real_total,
                    "remaining_minutes": 0,
                    "current_progress": hang_status.get("current_progress", 0),
                    "updated": hang_status.get("updated", ""),
                }
                _write_hang_status_safe(hang_status)
                hang_running = False
        except Exception:
            pass
    else:
        # 自愈（历史失败残留）：挂机未运行，但状态文件停留在「挂机失败」。
        # 失败状态只在收尾时写一次（无 10 秒刷新），若 updated 距今已超过 10 分钟，
        # 说明是上一轮遗留（例如昨天的失败、今天挂机还没到时间），
        # 应重置为「未挂机」，避免看板在挂机开始前一直显示旧的失败状态误导用户。
        try:
            if hang_status.get("status") == "挂机失败":
                updated_str = hang_status.get("updated", "")
                try:
                    updated_ts = datetime.strptime(updated_str, "%Y-%m-%d %H:%M:%S").timestamp()
                    stale_minutes = (time.time() - updated_ts) / 60.0
                except Exception:
                    stale_minutes = 999
                if stale_minutes > 10:
                    log_event("scheduler", f"挂机状态自愈：历史失败状态停留 {stale_minutes:.0f} 分钟未更新，已重置为未挂机")
                    hang_status = {
                        "running": False,
                        "status": "未挂机",
                        "message": "",
                        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    _write_hang_status_safe(hang_status)
        except Exception:
            pass

    status = {
        "container_running": True,
        "account_configured": bool(settings.get("username")),
        "account_user": settings.get("username", ""),
        "device_code": device_info.get("device_code", ""),
        "ai_task_enabled": bool(cron_config.get("ai_chat_enabled")),
        "ai_chat_enabled": bool(cron_config.get("ai_chat_enabled")),
        "hang_task_enabled": bool(cron_config.get("pc_hang_enabled")),
        "ai_chat_time": cron_config.get("ai_chat_time", []),
        "pc_hang_time": cron_config.get("pc_hang_time", []),
        "hang_minutes": cron_config.get("hang_minutes", 80),
        "keepalive_seconds": cron_config.get("keepalive_seconds", 900),
        "redeem_enabled": bool(redeem_config.get("enabled")),
        "redeem_name": redeem_config.get("prod_name", ""),
        "ctyun_running": ctyun_running,
        "scheduler_running": scheduler_running,
        "last_login": settings.get("last_login", "") or (datetime.fromtimestamp(os.path.getmtime(cookie_file)).strftime("%Y-%m-%d %H:%M") if os.path.exists(cookie_file) else ""),
        "cookie_exists": os.path.exists(cookie_file),
        "auth_data_exists": os.path.exists(auth_file),
        "cookie_expired": (not os.path.exists(cookie_file)) or (
            time.time() - os.path.getmtime(cookie_file) > 24 * 3600
        ),
        "uptime": uptime,
        "hang_status": hang_status,
        "hang_running": hang_running,
        "keepalive_enabled": cron_config.get("keepalive_enabled", True),
        # 补全：keepalive_enabled 为开关配置，keepalive_effective 为实际生效状态。
        # 开关关闭时 ctyun_running 因日志停止更新会误报 false，前端用本字段区分
        # 「已停用」与「异常未运行」。
        "keepalive_effective": bool(cron_config.get("keepalive_enabled", True)) and ctyun_running,
        "points_refresh_hours": cron_config.get("points_refresh_hours", 8),
        "next_ai_run": _next_run_from_times(cron_config.get("ai_chat_time", []), cron_config.get("ai_chat_enabled")),
        "next_hang_run": _next_run_from_times(cron_config.get("pc_hang_time", []), cron_config.get("pc_hang_enabled")),
        "rewards": load_rewards_summary(),
        "points_history": load_points_history(),
        "recent_logs": get_logs("all", 20)
    }

    # cookie 失效增量通知：仅在「有效→失效」翻转时推送一次，避免轮询重复打扰
    try:
        state = _load_scheduler_state()
        expired_now = status["cookie_expired"]
        if expired_now and not state.get("cookie_expired_notified"):
            notify_event("login_fail", "登录状态已失效", "检测到登录 Cookie 超过有效期或已不存在，请重新登录")
            state["cookie_expired_notified"] = True
            _save_scheduler_state(state)
        elif not expired_now and state.get("cookie_expired_notified"):
            state["cookie_expired_notified"] = False
            _save_scheduler_state(state)
    except Exception:
        pass

    return status


# ============================================
# Task Execution
# ============================================
def _monitor_task_result(proc, task_type, minutes=None, _task_lock=None):
    """监控子进程退出，按结果推送通知（避免"触发即发"的误导通知）。

    修复：新增 _task_lock 参数——浏览器任务互斥锁由本线程在子进程真正
    退出后释放（Popen 异步启动，execute_task 返回不代表任务结束）。
    """
    try:
        code = proc.wait()
    except Exception as e:
        # wait 失败极罕见；保持原有行为：仅记日志、不发通知（finally 已保证锁释放）
        log_event(task_type, f"任务监控失败: {e}")
        return
    finally:
        # 无论 wait 成功与否，任务都已结束，释放互斥锁（锁可能为 None：
        # 非浏览器任务或历史调用方未传）
        if _task_lock is not None:
            try:
                _task_lock.release()
            except Exception:
                pass
    if task_type == "pc_hang":
        # 挂机结束后自动兑换的结果一并推送（pc_login 写在临时目录 ctyun_redeem_auto）
        redeem_note = ""
        try:
            with open(tmp_path("ctyun_redeem_auto"), "r", encoding="utf-8") as f:
                r = json.load(f)
            if r.get("ok"):
                redeem_note = f"\n\n已自动兑换 {r.get('prod')} × {r.get('times')} 次（消耗 {r.get('cost')} 积分，当前 {r.get('points')} 积分）"
            else:
                redeem_note = f"\n\n自动兑换 {r.get('prod')} 未成功：{r.get('reason', '请查看日志')}"
        except Exception:
            pass
        if code == 0:
            notify_event("task_done", "云电脑挂机完成", f"挂机任务执行成功，时长约 {minutes or 80} 分钟{redeem_note}")
        else:
            notify_event("hang_fail", "云电脑挂机失败", f"挂机任务执行失败（退出码 {code}），请查看挂机日志{redeem_note}")
    elif task_type == "ai_chat":
        if code == 0:
            notify_event("ai_done", "AI 对话任务完成", "AI 对话任务执行成功，积分已领取")
        else:
            notify_event("ai_fail", "AI 对话任务失败", f"AI 对话任务执行失败（退出码 {code}），请查看 AI 对话日志")
    elif task_type == "combined":
        if code == 0:
            notify_event("task_done", "合并任务完成", "AI对话 + 挂机合并任务执行成功")
        else:
            notify_event("hang_fail", "合并任务失败", f"AI对话 + 挂机合并任务执行失败（退出码 {code}）")
    elif task_type == "login":
        # 修复：登录成功通知原先在 api_test_login 触发时就立即推送，导致
        # 「先报成功后报失败」的矛盾通知。改为与失败通知对称：
        # 由子进程真实退出码决定——成功(0)推 login_ok，失败推 login_fail。
        if code == 0:
            notify_event("login_ok", "账号登录成功", "账号已成功登录并抓取积分")
        else:
            notify_event("login_fail", "账号登录失败", f"账号登录并抓取积分任务执行失败（退出码 {code}）")
    elif task_type == "redeem":
        # 手动兑换：读取 pc_login.py 写的结果文件决定推送内容
        try:
            with open(tmp_path("ctyun_redeem_result"), "r", encoding="utf-8") as f:
                r = json.load(f)
            if r.get("ok"):
                notify_event("redeem_ok", "兑换物品成功",
                             f"已兑换 {r.get('prod')} × {r.get('times')} 次（消耗 {r.get('cost')} 积分，当前 {r.get('points')} 积分）")
            else:
                notify_event("redeem_fail", "兑换物品失败",
                             f"兑换 {r.get('prod')} 失败：{r.get('reason', '请查看日志')}")
        except Exception:
            # 结果文件缺失/损坏：按退出码兜底
            if code == 0:
                notify_event("redeem_ok", "兑换物品成功", "兑换任务执行成功")
            else:
                notify_event("redeem_fail", "兑换物品失败", f"兑换任务执行失败（退出码 {code}）")


# 修复：浏览器任务（AI 对话/挂机/合并/兑换/登录）共用一个浏览器会话，
# 此前可被同时触发（如挂机进行中又点"立即挂机"、定时与手动同时命中），
# 两个 DrissionPage 实例互踢会话导致双双失败。增加进程内互斥：占用期间
# 再触发浏览器类任务直接拒绝。锁的释放时机见 execute_task/_monitor_task_result。
_BROWSER_TASK_TYPES = frozenset({"ai_chat", "pc_hang", "combined", "redeem", "login"})
_BROWSER_TASK_LOCK = threading.Lock()


def execute_task(task_type: str, params: dict = None) -> dict:
    """Execute a task. params 可携带额外参数（如挂机时长 minutes）。"""
    # 桌面版适配：脚本路径由容器内 /app/*.py 改为应用目录（exe 同级 app/），
    # PyInstaller 打包后脚本随包分发在 app/ 目录下，由主进程 python 解释器执行。
    tasks = {
        "ai_chat": {
            "script": os.path.join(APP_DIR, "login_script.py"),
            "name": "AI 对话任务"
        },
        "pc_hang": {
            "script": os.path.join(APP_DIR, "pc_login.py"),
            "name": "挂机任务"
        },
        "combined": {
            "script": os.path.join(APP_DIR, "combined_task.py"),
            "name": "合并任务(AI对话+挂机)"
        },
        "redeem": {
            "script": os.path.join(APP_DIR, "pc_login.py"),
            "args": ["--redeem-now"],
            "name": "手动兑换"
        },
        "redeem_config": {
            "script": os.path.join(APP_DIR, "pc_login.py"),
            "args": ["--config-redeem"],
            "name": "积分兑换配置"
        },
        "logout": {
            "script": os.path.join(APP_DIR, "login_script.py"),
            "args": ["--logout"],
            "name": "安全退出登录"
        },
        "login": {
            "script": os.path.join(APP_DIR, "pc_login.py"),
            "args": ["--fetch-rewards"],
            "name": "账号登录并抓取积分"
        }
    }

    task = tasks.get(task_type)
    if not task:
        return {"success": False, "error": f"未知任务类型: {task_type}"}

    script = task.get("script", "")
    if not os.path.exists(script):
        return {"success": False, "error": f"脚本不存在: {script}"}

    # 修复：浏览器任务并发互斥。非阻塞获取锁，占用时直接拒绝而不是排队，
    # 避免重复任务叠加执行。获取成功后：
    # - 会启动监控线程的任务（pc_hang/ai_chat/combined/login/redeem）→
    #   锁交给监控线程在子进程真正退出后释放（_task_lock 参数）
    # - 不会启动监控线程的任务（redeem_config/logout）→ 由下方 finally 立即释放
    # - Popen 抛异常的同步失败路径（未启动监控线程）→ 同样由 finally 兜底释放
    _task_lock = None
    if task_type in _BROWSER_TASK_TYPES:
        if not _BROWSER_TASK_LOCK.acquire(blocking=False):
            return {"success": False, "error": "已有浏览器任务在执行中，请稍后再试"}
        _task_lock = _BROWSER_TASK_LOCK

    try:
        args = [sys.executable, script]
        if task.get("args"):
            args.extend(task["args"])
        # 挂机任务传入时长环境变量
        task_env = os.environ.copy()
        # 从 web_settings.json 注入账号到环境变量（脚本优先读环境变量，缺失时 fallback 到文件）
        try:
            settings = load_settings()
            if settings.get("username"):
                task_env.setdefault("APP_USER", settings["username"])
            if settings.get("password"):
                task_env.setdefault("APP_PASSWORD", settings["password"])
            # 桌面版适配：不再伪标 RUNNING_IN_DOCKER（Windows 下脚本会据此误入 Docker 分支；
            # 路径已由 paths 模块统一，无需该标记）
            task_env.pop("RUNNING_IN_DOCKER", None)
            task_env["SETTINGS_FILE"] = SETTINGS_FILE
        except Exception:
            pass
        if task_type in ("pc_hang", "redeem", "combined"):
            cron_config = get_cron_config()
            minutes = (params or {}).get("minutes") or cron_config.get("hang_minutes", 80)
            task_env["HANG_MINUTES"] = str(minutes)
        # Run in background
        stdout_target = subprocess.DEVNULL
        stderr_target = subprocess.DEVNULL
        # 任务输出写入独立日志文件，便于前端查看执行过程
        log_file_map = {
            "redeem": "redeem_log.log",
            "ai_chat": "ai_chat.log",
            "pc_hang": "pc_hang.log",
            "combined": "pc_hang.log",  # 合并任务含挂机，日志并入挂机日志
            "login": "login_task.log",
            "logout": "logout.log",
        }
        if task_type in log_file_map:
            try:
                task_log = open(os.path.join(DATA_DIR, log_file_map[task_type]), "a", encoding="utf-8")
                stdout_target = task_log
                stderr_target = task_log
            except Exception:
                pass
        proc = subprocess.Popen(
            args,
            stdout=stdout_target,
            stderr=stderr_target,
            env=task_env,
            # 桌面版适配：Linux start_new_session 改为 Windows 兼容的 DETACHED_PROCESS，
            # 目的相同——子任务不随控制台退出/CTRL_C 被连带终止
            creationflags=(subprocess.DETACHED_PROCESS if os.name == "nt" else 0),
            start_new_session=(os.name != "nt"),
        )
        # 挂机/合并任务启动前清空旧的自动兑换结果文件，避免挂机完成通知附加过期兑换信息
        if task_type in ("pc_hang", "combined"):
            try:
                with open(tmp_path("ctyun_redeem_auto"), "w", encoding="utf-8") as f:
                    f.write("")
            except Exception:
                pass
        # 后台监控子进程退出码：真正完成/失败时才推送通知，避免"触发即发"误导。
        # 并传互斥锁，由监控线程在任务结束后释放（见 _monitor_task_result）。
        if task_type in ("pc_hang", "ai_chat", "combined", "login", "redeem"):
            threading.Thread(
                target=_monitor_task_result,
                args=(proc, task_type, minutes if task_type == "pc_hang" else None, _task_lock),
                daemon=True
            ).start()
            _task_lock = None  # 释放责任已移交监控线程，finally 不再重复释放
        try:
            if task_type == "pc_hang":
                notify_event("hang_start", "开始挂机",
                             f"云电脑挂机任务已开始，时长约 {minutes or 80} 分钟")
            elif task_type == "ai_chat":
                notify_event("ai_start", "开始 AI 对话",
                             "AI 对话任务已开始执行")
            elif task_type == "combined":
                notify_event("hang_start", "开始挂机",
                             f"合并任务已开始（AI对话+挂机），时长约 {minutes or 80} 分钟")
        except Exception as e:
            log_event(task_type, f"开始事件通知失败: {e}")
        detail = f"{task['name']}已触发（脚本={script}"
        if task.get("args"):
            detail += f" 参数={task['args']}"
        if task_type == "pc_hang":
            detail += f" 挂机时长={minutes}分钟"
        detail += f" 用户={settings.get('username','')}）"
        log_event(task_type, detail)
        return {"success": True, "message": f"{task['name']}已触发执行"}
    except Exception as e:
        log_event(task_type, f"{task['name']}触发失败: {e}")
        return {"success": False, "error": str(e)}
    finally:
        # 修复：兜底释放互斥锁。正常路径下监控线程类任务已在上方把 _task_lock
        # 置 None（由监控线程释放）；走到这里还持有锁的只剩两种情况：
        # ① 非监控类任务（redeem_config/logout）正常完成；② Popen 抛异常的同步失败。
        if _task_lock is not None:
            try:
                _task_lock.release()
            except Exception:
                pass


def trigger_rewards_fetch() -> dict:
    """Launch pc_login.py --fetch-rewards in background to refresh rewards.json."""
    script = os.path.join(APP_DIR, "pc_login.py")
    if not os.path.exists(script):
        return {"success": False, "error": f"脚本不存在: {script}"}
    try:
        fetch_env = os.environ.copy()
        try:
            settings = load_settings()
            if settings.get("username"):
                fetch_env.setdefault("APP_USER", settings["username"])
            if settings.get("password"):
                fetch_env.setdefault("APP_PASSWORD", settings["password"])
            # 桌面版适配：同 execute_task，不再伪标 RUNNING_IN_DOCKER
            fetch_env.pop("RUNNING_IN_DOCKER", None)
            fetch_env["SETTINGS_FILE"] = SETTINGS_FILE
        except Exception:
            pass
        subprocess.Popen(
            [sys.executable, script, "--fetch-rewards"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=fetch_env,
            # 桌面版适配：同 execute_task，Windows 用 DETACHED_PROCESS 替代 start_new_session
            creationflags=(subprocess.DETACHED_PROCESS if os.name == "nt" else 0),
            start_new_session=(os.name != "nt"),
        )
        log_event("redeem", "已触发抓取可兑换物品")
        return {"success": True, "message": "正在后台抓取可兑换物品，请稍候刷新"}
    except Exception as e:
        log_event("redeem", f"触发抓取可兑换物品失败: {e}")
        return {"success": False, "error": str(e)}


def _points_refresh_loop() -> None:
    """后台线程：按 cron 配置的积分刷新间隔，定期触发 pc_login.py --fetch-rewards。

    修复：启动后先等 90 秒做一次「启动刷新」（拿到当前积分基线），
    再进入固定间隔循环。原实现先 sleep N 小时再触发，容器重启后
    首次积分刷新要等 N 小时，趋势图长时间空白。
    """
    # 启动缓冲：等 Web/调度线程就绪 + 可能的登录任务完成，先刷新一次积分基线
    time.sleep(90)
    # 修复：积分刷新通知去重——记录上次推送的积分数，只有变化时才推送，
    # 避免"积分没变也每 N 小时推送一条无意义通知"。
    _last_notified_points = None
    while True:
        try:
            hours = int(get_cron_config().get("points_refresh_hours", 8) or 8)
            if hours < 1:
                hours = 1
            log_event("redeem", f"定时积分刷新触发（间隔 {hours} 小时）")
            trigger_rewards_fetch()
            # 抓取是异步的，稍等后读取当前积分推送（附变化量，有变化才有意义）
            time.sleep(90)
            now_points = int(load_rewards_summary().get("points") or 0)
            if now_points != _last_notified_points:
                notify_event("points_update", "积分刷新完成",
                             f"当前积分 {now_points}")
                _last_notified_points = now_points
        except Exception as e:
            log_event("redeem", f"定时积分刷新异常: {e}")
            time.sleep(600)
            continue
        # 本轮完成后按配置间隔休眠（每轮重新读取，用户改配置能及时生效）
        while True:
            try:
                hours = int(get_cron_config().get("points_refresh_hours", 8) or 8)
            except Exception:
                hours = 8
            if hours < 1:
                hours = 1
            time.sleep(hours * 3600)
            break


def _start_background_threads() -> None:
    """启动后台线程（调度线程 + 积分定时刷新）。"""
    try:
        st = threading.Thread(target=_scheduler_loop, daemon=True)
        st.start()
        log_event("system", "定时任务调度线程已启动")
    except Exception as e:
        log_event("system", f"启动调度线程失败: {e}")
    try:
        t = threading.Thread(target=_points_refresh_loop, daemon=True)
        t.start()
        log_event("system", "积分定时刷新线程已启动")
    except Exception as e:
        log_event("system", f"启动积分刷新线程失败: {e}")


# ============================================
# Web Server (Flask)
# ============================================
def create_flask_app():
    app = Flask(__name__, static_folder=None)
    app.config["JSON_AS_ASCII"] = False

    # Serve frontend
    # Project root is parent of web_server dir
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "web", "templates")
    STATIC_DIR = os.path.join(PROJECT_ROOT, "web")

    @app.route("/")
    def index():
        # 修复：首页 index.html 原本未加 no-cache 头，浏览器会缓存旧版首页，
        # 导致引用旧版 icons.js/index.html 造成"图标显示不全"等缓存假故障
        # （static_files 路由已有 no-cache，此处补齐保持一致）。
        resp = send_from_directory(TEMPLATE_DIR, "index.html")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    @app.route("/<path:path>")
    def static_files(path):
        # Serve static files；强制 no-cache 避免浏览器缓存旧版前端
        resp = send_from_directory(STATIC_DIR, path)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    # API: Get status
    @app.route("/api/status")
    def api_status():
        return jsonify(get_system_status())

    # API: Get settings
    @app.route("/api/settings", methods=["GET"])
    def api_get_settings():
        settings = load_settings()
        cookie_file = os.path.join(DATA_DIR, f"ctyun_cookies_{settings.get('username','')}_.json")
        last_login = settings.get("last_login", "")
        if not last_login and os.path.exists(cookie_file):
            last_login = datetime.fromtimestamp(os.path.getmtime(cookie_file)).strftime("%Y-%m-%d %H:%M")
        return jsonify({
            "username": settings.get("username", ""),
            "password": settings.get("password", ""),
            "preset_messages": settings.get("preset_messages", DEFAULT_SETTINGS["preset_messages"]),
            "last_login": last_login,
            "cookie_exists": os.path.exists(cookie_file),
            "auth_data_exists": os.path.exists(os.path.join(DATA_DIR, f"ctyun_authData_{settings.get('username','')}_.json"))
        })

    # API: Save settings
    @app.route("/api/settings", methods=["POST"])
    def api_save_settings():
        data = request.json or {}
        # 修复：读-改-写（load→改→save）在并发下可能互相覆盖丢更新
        # （如与调度线程 save_cron_config 并发时丢掉定时配置），套进程锁串行化。
        with _SETTINGS_LOCK:
            settings = load_settings()

            if "username" in data:
                settings["username"] = data["username"].strip()
            if "password" in data:
                settings["password"] = data["password"]
            if "preset_messages" in data and isinstance(data["preset_messages"], list):
                settings["preset_messages"] = data["preset_messages"]

            save_settings(settings)
            # 同步保活账号信息到 accounts.json（补全：账号/密码变更后
            # CtYun.dll 读取的 accounts.json 需要同步更新，原实现遗漏）
            _sync_accounts_json(settings)

        # Update environment
        os.environ["APP_USER"] = settings["username"]
        os.environ["APP_PASSWORD"] = settings["password"]

        # Update /etc/environment
        # 桌面版适配：Windows 无 /etc/environment，仅 Linux 且文件存在时才写（保留容器兼容）
        try:
            env_file = "/etc/environment"
            if os.name != "nt" and os.path.exists(env_file):
                content = ""
                with open(env_file, "r") as f:
                    content = f.read()
                lines = [l for l in content.splitlines() if not l.startswith("APP_USER=") and not l.startswith("APP_PASSWORD=")]
                lines.append(f"APP_USER={settings['username']}")
                lines.append(f"APP_PASSWORD={settings['password']}")
                with open(env_file, "w") as f:
                    f.write("\n".join(lines) + "\n")
        except Exception:
            pass

        log_event("system", f"账号设置已保存: {settings['username']}")
        return jsonify({"success": True, "message": "设置已保存"})

    # API: Test login (actually triggers a real login + rewards fetch)
    @app.route("/api/test-login", methods=["POST"])
    def api_test_login():
        data = request.json or {}
        username = data.get("username", "")
        password = data.get("password", "")

        if not username or not password:
            return jsonify({"success": False, "error": "请填写账号和密码"}), 400

        # Save account first
        # 修复：读-改-写套进程锁，避免与并发保存互相覆盖丢更新（同 api_save_settings）
        with _SETTINGS_LOCK:
            settings = load_settings()
            settings["username"] = username
            settings["password"] = password
            save_settings(settings)
            _sync_accounts_json(settings)
        log_event("system", f"账号已保存，开始登录: {username}")

        # Trigger a real login task (pc_login.py --fetch-rewards will login + fetch)
        task_result = execute_task("login")
        if not task_result.get("success"):
            notify_event("login_fail", "账号登录失败", f"账号 {username} 登录触发失败：{task_result.get('error', '未知错误')}")
            return jsonify({"success": False, "error": task_result.get("error", "登录触发失败")}), 500

        # 修复：此处仅表示"登录任务已触发"，若立即推送 login_ok，会出现
        # 「先报登录成功、几秒后又报登录失败」的自相矛盾通知（真实结果要等
        # 子进程退出才知道）。成功通知改由 _monitor_task_result 的 login 分支
        # 在退出码为 0 时推送。
        return jsonify({"success": True, "message": "已触发登录，正在验证账号…（如需要短信验证码会在页面提示）"})

    # API: Clear session
    @app.route("/api/clear-session", methods=["POST"])
    def api_clear_session():
        settings = load_settings()
        username = settings.get("username", "")

        # Remove cookie and auth files
        for pattern in [f"ctyun_cookies_{username}_.json", f"ctyun_authData_{username}_.json"]:
            file_path = os.path.join(DATA_DIR, pattern)
            if os.path.exists(file_path):
                os.remove(file_path)
                log_event("system", f"已删除 {pattern}")

        # 修复：读-改-写套进程锁（同 api_save_settings）
        with _SETTINGS_LOCK:
            settings = load_settings()
            settings["last_login"] = ""
            save_settings(settings)
        notify_event("login_fail", "登录状态已失效", "用户解绑/清除登录状态，需重新登录")
        return jsonify({"success": True, "message": "登录状态已清除"})

    # API: Submit SMS code (written to file that pc_login.py reads during login)
    @app.route("/api/sms-code", methods=["POST"])
    def api_sms_code():
        data = request.json or {}
        code = (data.get("code") or "").strip()
        if not code:
            return jsonify({"success": False, "error": "验证码不能为空"}), 400
        settings = load_settings()
        username = settings.get("username", "") or os.environ.get("APP_USER", "")
        if not username:
            return jsonify({"success": False, "error": "未找到账号，请先保存账号设置"}), 400
        # 桌面版适配：验证码交接文件改存公共临时目录（原 /tmp，与 pc_login 读取处保持一致）
        sms_file = tmp_path(f"ctyun_sms_code_{username}")
        try:
            with open(sms_file, "w", encoding="utf-8") as f:
                f.write(code)
            log_event("system", f"已写入短信验证码文件: {sms_file}")
            return jsonify({"success": True, "message": "验证码已提交，登录将继续"})
        except Exception as e:
            return jsonify({"success": False, "error": f"写入验证码失败: {e}"}), 500

    # API: Save preset messages
    @app.route("/api/presets", methods=["POST"])
    def api_save_presets():
        data = request.json or {}
        messages = data.get("messages", [])
        if not isinstance(messages, list) or len(messages) == 0:
            return jsonify({"success": False, "error": "预设消息不能为空"}), 400

        # 修复：读-改-写套进程锁（同 api_save_settings）
        with _SETTINGS_LOCK:
            settings = load_settings()
            settings["preset_messages"] = messages
            save_settings(settings)
        log_event("system", "AI 对话预设消息已更新")
        return jsonify({"success": True, "message": "预设消息已保存"})

    # API: Get device code
    @app.route("/api/device-code", methods=["GET"])
    def api_get_device_code():
        return jsonify(load_device_code())

    # API: Regenerate device code
    @app.route("/api/device-code/regenerate", methods=["POST"])
    def api_regenerate_device_code():
        code = generate_device_code()
        save_device_code(code)
        return jsonify({"success": True, "device_code": code, "message": "设备代码已重新生成"})

    # API: Save device code
    @app.route("/api/device-code", methods=["POST"])
    def api_save_device_code():
        data = request.json or {}
        code = data.get("device_code", "").strip()
        if not code:
            return jsonify({"success": False, "error": "设备代码不能为空"}), 400
        save_device_code(code)
        return jsonify({"success": True, "message": "设备代码已保存"})

    # API: Get cron
    @app.route("/api/cron", methods=["GET"])
    def api_get_cron():
        return jsonify(get_cron_config())

    # API: Save cron (傻瓜式时间点)
    @app.route("/api/cron", methods=["POST"])
    def api_save_cron():
        data = request.json or {}
        ai_enabled = bool(data.get("ai_chat_enabled", True))
        ai_time = data.get("ai_chat_time", ["03:00", "20:00"])
        hang_enabled = bool(data.get("pc_hang_enabled", True))
        hang_time = data.get("pc_hang_time", ["04:00", "06:00"])

        # 校验时间点格式
        def _valid_times(lst):
            if not isinstance(lst, list):
                return False
            for t in lst:
                if not re.match(r"^\d{1,2}:\d{2}$", str(t)):
                    return False
                hh, mm = str(t).split(":")
                if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                    return False
            return True
        if ai_enabled and not _valid_times(ai_time):
            return jsonify({"success": False, "error": "AI 对话时间格式无效（应为 HH:MM）"}), 400
        if hang_enabled and not _valid_times(hang_time):
            return jsonify({"success": False, "error": "云电脑挂机时间格式无效（应为 HH:MM）"}), 400

        # keepalive_seconds 默认值与 get_cron_config 统一为 900
        keepalive_seconds = data.get("keepalive_seconds", 900)
        try:
            keepalive_seconds = int(keepalive_seconds)
            if keepalive_seconds < 10 or keepalive_seconds > 21600:
                return jsonify({"success": False, "error": "保活间隔需在 10-21600 秒之间"}), 400
        except Exception:
            return jsonify({"success": False, "error": "保活间隔格式错误"}), 400

        # 修复：hang_minutes 此前未定义就在下方 save_cron_config(...) 中引用，
        # Flask 路由保存定时配置必然抛 NameError → 500，导致「保存」按钮全部失败，
        # 配置只能靠 30s 后端自动保存兜底路径生效。与 fallback 路由（2079 行）对齐。
        hang_minutes = data.get("hang_minutes", 80)
        try:
            hang_minutes = int(hang_minutes) if str(hang_minutes).isdigit() else 80
            if hang_minutes < 1 or hang_minutes > 720:
                return jsonify({"success": False, "error": "挂机时长需在 1-720 分钟之间"}), 400
        except Exception:
            hang_minutes = 80

        points_refresh_hours = data.get("points_refresh_hours", 8)
        try:
            points_refresh_hours = int(points_refresh_hours)
            if points_refresh_hours < 1 or points_refresh_hours > 168:
                return jsonify({"success": False, "error": "积分刷新间隔需在 1-168 小时之间"}), 400
        except Exception:
            return jsonify({"success": False, "error": "积分刷新间隔格式错误"}), 400

        ka_enabled = data.get("keepalive_enabled", True)
        silent = data.get("silent_mode", False)
        browser_w = data.get("browser_watch", False)
        # 修复：写重载标记前先对比旧保活配置，仅当 keepalive_seconds 或开关
        # 状态变化时才写。原实现无条件写，导致用户只改 AI 对话时间等无关配置
        # 也会触发 entrypoint 重启 CtYun.dll（打断正在进行的保活连接窗口）。
        old_cron = get_cron_config()
        ka_changed = (int(keepalive_seconds) != int(old_cron.get("keepalive_seconds", 900) or 900)) \
            or (bool(ka_enabled) != bool(old_cron.get("keepalive_enabled", True)))
        save_cron_config(ai_enabled, ai_time, hang_enabled, hang_time, hang_minutes,
                         keepalive_seconds, keepalive_enabled=ka_enabled,
                         silent_mode=silent, browser_watch=browser_w,
                         points_refresh_hours=points_refresh_hours)
        # 写入重载标记：entrypoint 守护循环检测到后重启 CtYun.dll，
        # 使其读取新的 accounts.json（keepAliveSeconds）生效，不再写死 60 秒
        if ka_changed:
            try:
                with open(KEEPALIVE_RELOAD_FLAG, "w") as f:
                    f.write(str(keepalive_seconds))
            except Exception:
                pass
        return jsonify({"success": True, "message": "定时任务已保存（含保活间隔），保活即将按新间隔重启"})

    # API: Get redeem config
    @app.route("/api/redeem", methods=["GET"])
    def api_get_redeem():
        config = get_redeem_config()
        # scheduleType 归一化后可能为 daily/interval_days/weekly_days/monthly_days，
        # 前端下拉使用 daily/interval/weekly/monthly，这里转成前端取值回显。
        _, display_type = _normalize_schedule_type(config.get("scheduleType"))
        return jsonify({
            "enabled": config.get("enabled", False),
            "schedule_type": display_type,
            "interval_days": config.get("intervalDays", 1),
            "monthly_days": config.get("monthlyDays", []),
            "prod_id": config.get("prodId", ""),
            "prod_name": config.get("prodName", ""),
            "prod_type": config.get("prodType", ""),
            "cost_points": config.get("costPoints", ""),
            "max_redeem_times": config.get("maxRedeemTimes", 0),
            "desktop_id": config.get("desktopId", ""),
            "config": config
        })

    # API: Save redeem config
    @app.route("/api/redeem", methods=["POST"])
    def api_save_redeem():
        data = request.json or {}
        # 统一构造标准化配置：scheduleType 归一化 + 保留 lastRedeemDate 兑换进度
        config = _build_redeem_config_from_request(data)
        save_redeem_config(config)
        return jsonify({"success": True, "message": "兑换配置已保存"})

    # API: Disable redeem
    @app.route("/api/redeem/disable", methods=["POST"])
    def api_disable_redeem():
        save_redeem_config({"enabled": False})
        return jsonify({"success": True, "message": "自动兑换已禁用"})

    # API: Get rewards (redeemable items list) from rewards.json
    @app.route("/api/rewards", methods=["GET"])
    def api_get_rewards():
        return jsonify(load_rewards_data())

    # API: Trigger background fetch of rewards
    @app.route("/api/rewards/fetch", methods=["POST"])
    def api_fetch_rewards():
        result = trigger_rewards_fetch()
        if result.get("success"):
            return jsonify(result)
        return jsonify(result), 400

    # API: Execute task
    @app.route("/api/task", methods=["POST"])
    def api_execute_task():
        data = request.json or {}
        task_type = data.get("task", "")
        result = execute_task(task_type)
        if result.get("success"):
            return jsonify(result)
        return jsonify(result), 400

    # API: Get logs
    @app.route("/api/logs", methods=["GET"])
    def api_get_logs():
        log_type = request.args.get("type", "all")
        logs = get_logs(log_type)
        return jsonify({"logs": logs})

    # API: Clear logs
    @app.route("/api/logs/clear", methods=["POST"])
    def api_clear_logs():
        clear_logs()
        return jsonify({"success": True, "message": "日志已清空"})

    # API: Points history (for trend chart)
    @app.route("/api/points-history")
    def api_points_history():
        return jsonify({"history": load_points_history()})

    # API: Download logs as text
    @app.route("/api/logs/download")
    def api_download_logs():
        try:
            logs = get_logs("all", 1000)
            # get_logs 返回的是字符串行列表（非 dict）
            lines = [l if isinstance(l, str) else str(l) for l in logs]
            text = "\n".join(lines)
            return Response(text, mimetype="text/plain", headers={
                "Content-Disposition": "attachment; filename=ctyun_logs.txt"
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # API: Restart container (web service only; entrypoint will restart it)
    @app.route("/api/restart", methods=["POST"])
    def api_restart():
        def _delayed_exit():
            time.sleep(1)
            os._exit(0)
        threading.Thread(target=_delayed_exit, daemon=True).start()
        return jsonify({"success": True, "message": "Web 服务正在重启"})

    # ---- Robot / Webhook notification ----
    @app.route("/api/web-settings", methods=["GET"])
    def api_get_web_settings():
        ws = load_web_settings()
        # 修复：此前把完整配置（含 smtp 明文密码）直接回给前端。登录密码等
        # 敏感字段不应随 GET 返回；smtp 各字段仅回结构占位，保存时仅在
        # 用户重新填写时才覆盖（见 api_save_web_settings 合并逻辑）。
        notify = ws.get("notify") if isinstance(ws.get("notify"), dict) else {}
        if isinstance(notify.get("smtp"), dict) and notify["smtp"]:
            smtp = notify["smtp"]
            notify["smtp"] = {
                "host": smtp.get("host", ""),
                "port": smtp.get("port", ""),
                "user": smtp.get("user", ""),
                "pass": "******" if smtp.get("pass") else "",
                "to": smtp.get("to", ""),
            }
        return jsonify(ws)

    @app.route("/api/web-settings", methods=["POST"])
    def api_save_web_settings():
        data = request.json or {}
        # 修复：读-改-写（load→合并→save）在并发下可能互相覆盖丢更新
        # （前端自动保存 + 多页面同时保存），改用 locked 版本串行化关键段。
        ws = load_web_settings_locked()
        if "notify" in data and isinstance(data["notify"], dict):
            n = data["notify"]
            # 修复：此前重建 dict 只保留 enabled/type/url/events 四个字段，
            # uids/template/smtp 被静默丢弃——导致邮件和 WxPusher(AT_) 的正式通知
            # 永远失效（测试通知直接取请求体所以能通过，掩盖了问题）。
            # 改为在旧配置基础上合并更新，未传字段保留旧值。
            old = ws.get("notify", {}) if isinstance(ws.get("notify"), dict) else {}
            merged = {
                "enabled": bool(n.get("enabled", old.get("enabled", False))),
                "type": n.get("type", old.get("type", "webhook")),
                "url": (n.get("url") if n.get("url") is not None else old.get("url", "")) or "",
                "events": n.get("events", old.get("events", [])) if isinstance(n.get("events", old.get("events")), list) else [],
                "uids": n.get("uids", old.get("uids", "")) or "",
                "template": n.get("template", old.get("template", "")) or "",
            }
            # smtp 仅在传入时更新（测试通知的 smtp 字段不覆盖已保存配置）
            if isinstance(n.get("smtp"), dict) and n.get("smtp"):
                new_smtp = dict(n["smtp"])
                # 修复：GET /api/web-settings 现在只回 smtp.pass 掩码（不回明文），
                # 前端原样传回保存时若不识别掩码，真实密码会被 "******" 覆盖。
                # 检测到掩码占位时保留旧密码。
                if new_smtp.get("pass") == "******":
                    new_smtp["pass"] = old.get("smtp", {}).get("pass", "") if isinstance(old.get("smtp"), dict) else ""
                merged["smtp"] = new_smtp
            elif "smtp" in old:
                merged["smtp"] = old["smtp"]
            ws["notify"] = merged
        save_web_settings_locked(ws)
        return jsonify({"success": True, "message": "Web 设置已保存"})

    @app.route("/api/test-notify", methods=["POST"])
    def api_test_notify():
        data = request.json or {}
        url = (data.get("url") or "").strip()
        ntype = data.get("type", "webhook")
        title = data.get("title") or "天翼云签到面板"
        message = data.get("message") or "这是一条测试通知消息 ✅"
        tpl = data.get("template") or ""
        if ntype == "browser":
            return jsonify({"ok": True, "msg": "浏览器通知由前端直接弹出，请在网页中点击测试"})
        if ntype == "wxpusher":
            uids = data.get("uids") or ""
            if not url:
                return jsonify({"ok": False, "msg": "缺少 WxPusher 推送码（SPT_ 或 AT_）"})
            # 标准 appToken（AT_ 开头）需 UID；极简 SPT 推送码无需 UID
            if url.upper().startswith("AT_") and not uids:
                return jsonify({"ok": False, "msg": "标准 appToken（AT_ 开头）需填写接收 UID"})
            ok, msg = send_webhook_notify(url, "wxpusher", title, message, tpl, uids)
            return jsonify({"ok": ok, "msg": msg})
        if ntype == "email":
            smtp = data.get("smtp") or {}
            if not (smtp.get("host") and smtp.get("user") and smtp.get("to")):
                return jsonify({"ok": False, "msg": "缺少 SMTP 服务器/账号/收件人"})
            ok, msg = send_webhook_notify(smtp, "email", title, message, tpl)
            return jsonify({"ok": ok, "msg": msg})
        if not url:
            return jsonify({"ok": False, "msg": "缺少 webhook 地址"})
        ok, msg = send_webhook_notify(url, ntype, title, message, tpl)
        return jsonify({"ok": ok, "msg": msg})

    @app.route("/api/pending-notifies", methods=["GET"])
    def api_pending_notifies():
        return jsonify({"items": _pop_notify_queue()})

    @app.route("/api/wallpaper/random", methods=["GET"])
    def api_wallpaper_random():
        """后端代理取壁纸：服务器请求壁纸源返回图片 URL，避开浏览器 CORS。
        source=bing|spotlight|picsum|unsplash，query=可选关键词。"""
        source = request.args.get("source", "bing")
        query = request.args.get("query", "")
        timeout = 10
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            if source == "bing":
                url = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=zh-CN"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    j = json.loads(resp.read().decode("utf-8", "ignore"))
                u = j["images"][0]["url"]
                # 4K UHD 原图
                u = re.sub(r"_\d+x\d+\.jpg", "_UHD.jpg", u).replace("&amp;", "&")
                if u.startswith("//"):
                    u = "https:" + u
                elif u.startswith("/"):
                    u = "https://www.bing.com" + u
                return jsonify({"ok": True, "source": "bing", "url": u,
                                "title": j["images"][0].get("title", "")})
            if source == "spotlight":
                url = "https://api.peapix.com/v2/random?type=spotlight&count=1"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    j = json.loads(resp.read().decode("utf-8", "ignore"))
                return jsonify({"ok": True, "source": "spotlight", "url": j["images"][0]["imageUrl"]})
            if source == "picsum":
                return jsonify({"ok": True, "source": "picsum",
                                "url": "https://picsum.photos/1920/1080?random=" + str(int(time.time() * 1000))})
            if source == "unsplash":
                q = urllib.parse.quote(query or "nature")
                return jsonify({"ok": True, "source": "unsplash",
                                "url": "https://source.unsplash.com/featured/1920x1080/?" + q + "&sig=" + str(int(time.time() * 1000))})
            return jsonify({"ok": False, "msg": "未知壁纸源"})
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)})

    return app


# ============================================
# Webhook 推送（标准库 urllib，无第三方依赖）
# ============================================
def _render_template(tpl, title, text, event):
    """用自定义模板渲染消息，支持 {title}{message}{time}{event}。"""
    import datetime
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not tpl:
        return f"{title}\n{text}"
    return tpl.replace("{title}", title).replace("{message}", text) \
              .replace("{time}", now).replace("{event}", event or "")


def send_webhook_notify(url, ntype, title, text, template="", uids=None):
    """推送消息到 webhook（企业微信/飞书/钉钉/通用）、WxPusher、邮件。
    返回 (ok:bool, msg:str)。失败时仅告警，不影响主流程。"""
    try:
        if ntype == "email":
            return _send_email_notify(url, title, text, template)
        if ntype == "wxpusher":
            return _send_wxpusher(url, title, text, uids, template)
        payload = _build_webhook_payload(ntype, title, text, template)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", "ignore")
        return True, f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)


def _send_wxpusher(app_token, title, text, uids, template=""):
    """通过 WxPusher (https://wxpusher.zjiecode.com) 推送微信消息。
    支持两种模式：
      - 极简推送 SPT（app_token 以 SPT_ 开头）：GET 接口直接发给扫码者本人，无需 uid。
      - 标准推送（app_token 以 AT_ 开头）：POST 接口，需指定 uids。
    uids: 标准模式下接收用户 uid 列表（逗号分隔字符串或列表），极简模式忽略。"""
    try:
        if not app_token:
            return False, "缺少 WxPusher appToken / SPT 推送码"
        content = _render_template(template, title, text, "wxpusher").strip()
        # 极简推送 SPT：GET /api/send/message/{spt}/{content}
        if app_token.upper().startswith("SPT_"):
            url = "https://wxpusher.zjiecode.com/api/send/message/" + \
                  urllib.parse.quote(app_token, safe="") + "/" + urllib.parse.quote(content)
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_body = resp.read().decode("utf-8", "ignore")
            return True, f"WxPusher(SPT) HTTP {resp.status}"
        # 标准推送：POST /api/send/message
        if not uids:
            return False, "缺少接收用户 UID"
        if isinstance(uids, str):
            uid_list = [u.strip() for u in uids.split(",") if u.strip()]
        else:
            uid_list = list(uids)
        if not uid_list:
            return False, "缺少接收用户 UID"
        body = {
            "appToken": app_token,
            "content": content,
            "summary": (title or "")[:40],
            "contentType": 2,  # 2=HTML，支持换行
            "uids": uid_list,
            "verifyPay": False,
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "https://wxpusher.zjiecode.com/api/send/message",
            data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode("utf-8", "ignore")
        return True, f"WxPusher HTTP {resp.status}"
    except Exception as e:
        return False, str(e)


def _send_email_notify(smtp_cfg, title, text, template=""):
    """通过 SMTP 发送邮件通知。smtp_cfg: dict(host,port,user,pass,to)。"""
    try:
        import smtplib
        from email.mime.text import MIMEText
        host = smtp_cfg.get("host")
        port = int(smtp_cfg.get("port") or 465)
        user = smtp_cfg.get("user")
        pwd = smtp_cfg.get("pass")
        to = smtp_cfg.get("to")
        if not (host and user and to):
            return False, "SMTP 配置不完整"
        msg = MIMEText(_render_template(template, title, text, "email"), "plain", "utf-8")
        msg["Subject"] = title
        msg["From"] = user
        msg["To"] = to
        with smtplib.SMTP_SSL(host, port, timeout=10) as s:
            s.login(user, pwd)
            s.sendmail(user, [to], msg.as_string())
        return True, "邮件已发送"
    except Exception as e:
        return False, str(e)


def _build_webhook_payload(ntype, title, text, template=""):
    content = _render_template(template, title, text, ntype)
    if ntype == "wecom":
        return {"msgtype": "text", "text": {"content": content}}
    if ntype == "feishu":
        return {"msg_type": "text", "content": {"text": content}}
    if ntype == "dingtalk":
        return {"msgtype": "text", "text": {"content": content}}
    # 通用：尝试企业微信/飞书/钉钉都接受的宽松结构
    return {"msgtype": "text", "text": {"content": content}, "msg_type": "text", "content": {"text": content}}


def load_web_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"notify": {"enabled": False, "type": "webhook", "url": "", "events": [], "template": ""}}


def save_web_settings(ws):
    # 修复：非原子写（直接覆盖目标文件）在写入中途崩溃/断电会留下半截 JSON，
    # 下次 load 容错回默认值导致全部通知配置静默丢失。改为 tmp + os.replace 原子替换。
    try:
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(ws, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    except Exception:
        pass


# 修复：settings/scheduler_state/notify_queue 的读-改-写存在并发竞争（Flask threaded=True
# + 前端自动保存 + 调度线程同时写），后写覆盖先写丢更新。统一用进程内锁串行化关键段。
_SETTINGS_LOCK = threading.Lock()


def load_web_settings_locked():
    """加锁读取 web_settings（与持锁的读-改-写配对使用）。"""
    with _SETTINGS_LOCK:
        return load_web_settings()


def save_web_settings_locked(ws):
    with _SETTINGS_LOCK:
        save_web_settings(ws)


NOTIFY_QUEUE_FILE = os.path.join(DATA_DIR, "notify_queue.json")

# 修复：通知队列 push/pop 与前端轮询 /api/pending-notifies 存在并发竞争
# （调度线程/任务监控线程 push vs Flask 线程 pop），套进程锁串行化。
# 锁内仅做文件读写，无重入风险。
_NOTIFY_LOCK = threading.Lock()


def _push_notify_queue(event_key, title, text, level):
    """将通知追加到队列，供前端轮询弹出浏览器通知/横幅。"""
    try:
        with _NOTIFY_LOCK:
            q = []
            if os.path.exists(NOTIFY_QUEUE_FILE):
                with open(NOTIFY_QUEUE_FILE, "r", encoding="utf-8") as f:
                    q = json.load(f) or []
            q.append({"event": event_key, "title": title, "text": text, "level": level, "ts": time.time()})
            # 只保留最近 50 条，避免无限增长
            q = q[-50:]
            # 修复：改为 tmp + os.replace 原子写，避免写入中途被读到半截 JSON
            tmp = NOTIFY_QUEUE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(q, f, ensure_ascii=False, indent=2)
            os.replace(tmp, NOTIFY_QUEUE_FILE)
    except Exception:
        pass


def _pop_notify_queue():
    """读取并清空通知队列。"""
    try:
        with _NOTIFY_LOCK:
            if not os.path.exists(NOTIFY_QUEUE_FILE):
                return []
            with open(NOTIFY_QUEUE_FILE, "r", encoding="utf-8") as f:
                q = json.load(f) or []
            # 修复：原实现读出后直接 os.remove 删文件；若删后又有新通知 push
            # 会重建文件，时序上虽已加锁无碍，但 remove 后短暂窗口内文件不存在、
            # push 走"文件不存在"分支与重建逻辑不统一。改为统一重写空列表，
            # 语义更清晰且避免多次创建/删除 inode。
            tmp = NOTIFY_QUEUE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False)
            os.replace(tmp, NOTIFY_QUEUE_FILE)
            return q
    except Exception:
        return []


def notify_event(event_key, title, text, smtp=None):
    """按 web_settings 配置触发机器人通知（若事件已启用）。
    支持 webhook / 企业微信 / 飞书 / 钉钉 / 邮件 / 浏览器 / WxPusher。"""
    try:
        ws = load_web_settings()
        n = ws.get("notify", {})
        if not n.get("enabled"):
            return
        if event_key not in (n.get("events") or []):
            return
        ntype = n.get("type", "webhook")
        tpl = n.get("template", "")
        level = "error" if event_key in ("hang_fail", "login_fail", "ai_fail", "redeem_fail", "sys_error") else "success"
        # 写入队列（无论哪种类型，前端都能弹浏览器通知/横幅）
        _push_notify_queue(event_key, title, text, level)
        if ntype == "browser":
            # 浏览器通知由前端轮询触发，后端仅记录
            log_event("system", f"[通知] {title} - {text}")
            return
        if ntype == "email":
            if not smtp:
                smtp = n.get("smtp", {})
            ok, msg = send_webhook_notify(smtp, "email", title, text, tpl)
        elif ntype == "wxpusher":
            uids = n.get("uids", "")
            ok, msg = send_webhook_notify(n.get("url"), "wxpusher", title, text, tpl, uids)
        else:
            url = n.get("url")
            if not url:
                return
            ok, msg = send_webhook_notify(url, ntype, title, text, tpl)
        if not ok:
            log_event("system", f"[通知] 推送失败({ntype}): {msg}")
    except Exception:
        pass



# 修复：删除死代码 validate_cron_expr——已确认全项目无调用方
# （保存走傻瓜式时间点校验 _valid_times，不用 cron 表达式校验）。


# ============================================
# Fallback HTTP Server (no Flask)
# ============================================
if not HAS_FLASK:
    class FallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            # Project root is parent of web_server dir
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

            if path == "/":
                path = "/templates/index.html"
                file_path = os.path.join(project_root, "web", "templates", "index.html")
            elif path.startswith("/static/"):
                # 修复：os.path.join 遇到绝对段（如 "/etc/passwd"）会丢弃前缀，
                # 请求 /static//etc/passwd 会读到任意文件；且 ".." 可目录穿越。
                # 改为：去掉 /static/ 前缀后 normpath 归一化，拒绝含 .. 和绝对路径的段。
                rel = os.path.normpath(path[len("/static/"):])
                if rel.startswith("..") or os.path.isabs(rel):
                    self.send_error(403, "Forbidden")
                    return
                file_path = os.path.join(project_root, "web", "static", rel)
            elif path == "/api/status":
                self.send_json_response(get_system_status())
                return
            elif path == "/api/settings":
                settings = load_settings()
                self.send_json_response({
                    "username": settings.get("username", ""),
                    "password": settings.get("password", ""),
                    "preset_messages": settings.get("preset_messages", DEFAULT_SETTINGS["preset_messages"]),
                    "last_login": settings.get("last_login", ""),
                    "cookie_exists": os.path.exists(os.path.join(DATA_DIR, f"ctyun_cookies_{settings.get('username','')}_.json")),
                    "auth_data_exists": os.path.exists(os.path.join(DATA_DIR, f"ctyun_authData_{settings.get('username','')}_.json"))
                })
                return
            elif path == "/api/device-code":
                self.send_json_response(load_device_code())
                return
            elif path == "/api/cron":
                self.send_json_response(get_cron_config())
                return
            elif path == "/api/redeem":
                config = get_redeem_config()
                _, display_type = _normalize_schedule_type(config.get("scheduleType"))
                self.send_json_response({
                    "enabled": config.get("enabled", False),
                    "schedule_type": display_type,
                    "interval_days": config.get("intervalDays", 1),
                    "monthly_days": config.get("monthlyDays", []),
                    "prod_id": config.get("prodId", ""),
                    "prod_name": config.get("prodName", ""),
                    "prod_type": config.get("prodType", ""),
                    "cost_points": config.get("costPoints", ""),
                    "max_redeem_times": config.get("maxRedeemTimes", 0),
                    "desktop_id": config.get("desktopId", ""),
                    "config": config
                })
                return
            elif path.startswith("/api/logs"):
                params = parse_qs(urlparse(self.path).query)
                log_type = params.get("type", ["all"])[0]
                self.send_json_response({"logs": get_logs(log_type)})
                return
            elif path == "/api/rewards":
                self.send_json_response(load_rewards_data())
                return
            else:
                self.send_error(404, "Not Found")
                return

            if os.path.exists(file_path):
                self.send_response(200)
                if file_path.endswith(".css"):
                    self.send_header("Content-Type", "text/css")
                elif file_path.endswith(".js"):
                    self.send_header("Content-Type", "application/javascript")
                elif file_path.endswith(".html"):
                    self.send_header("Content-Type", "text/html")
                elif file_path.endswith(".png"):
                    self.send_header("Content-Type", "image/png")
                elif file_path.endswith(".svg"):
                    self.send_header("Content-Type", "image/svg+xml")
                else:
                    self.send_header("Content-Type", "text/plain")
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "File Not Found")

        def do_POST(self):
            path = urlparse(self.path).path
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {}

            if path == "/api/settings":
                # 修复：读-改-写套进程锁（与 Flask 版 api_save_settings 一致）
                with _SETTINGS_LOCK:
                    settings = load_settings()
                    if "username" in data:
                        settings["username"] = data["username"].strip()
                    if "password" in data:
                        settings["password"] = data["password"]
                    if "preset_messages" in data and isinstance(data["preset_messages"], list):
                        settings["preset_messages"] = data["preset_messages"]
                    save_settings(settings)
                    _sync_accounts_json(settings)
                os.environ["APP_USER"] = settings["username"]
                os.environ["APP_PASSWORD"] = settings["password"]
                log_event("system", f"账号设置已保存: {settings['username']}")
                self.send_json_response({"success": True, "message": "设置已保存"})

            elif path == "/api/test-login":
                username = data.get("username", "")
                password = data.get("password", "")
                if not username or not password:
                    self.send_json_response({"success": False, "error": "请填写账号和密码"}, 400)
                    return
                # 修复：读-改-写套进程锁（与 Flask 版一致）
                with _SETTINGS_LOCK:
                    settings = load_settings()
                    settings["username"] = username
                    settings["password"] = password
                    save_settings(settings)
                    _sync_accounts_json(settings)
                log_event("system", f"登录测试: 账号 {username}")
                self.send_json_response({"success": True, "message": "配置验证通过"})

            elif path == "/api/clear-session":
                settings = load_settings()
                username = settings.get("username", "")
                for pattern in [f"ctyun_cookies_{username}_.json", f"ctyun_authData_{username}_.json"]:
                    file_path = os.path.join(DATA_DIR, pattern)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                # 修复：读-改-写套进程锁（与 Flask 版一致）
                with _SETTINGS_LOCK:
                    settings = load_settings()
                    settings["last_login"] = ""
                    save_settings(settings)
                self.send_json_response({"success": True, "message": "登录状态已清除"})

            elif path == "/api/presets":
                messages = data.get("messages", [])
                if not isinstance(messages, list) or len(messages) == 0:
                    self.send_json_response({"success": False, "error": "预设消息不能为空"}, 400)
                    return
                # 修复：读-改-写套进程锁（与 Flask 版一致）
                with _SETTINGS_LOCK:
                    settings = load_settings()
                    settings["preset_messages"] = messages
                    save_settings(settings)
                self.send_json_response({"success": True, "message": "预设消息已保存"})

            elif path == "/api/device-code/regenerate":
                code = generate_device_code()
                save_device_code(code)
                self.send_json_response({"success": True, "device_code": code})

            elif path == "/api/device-code":
                code = data.get("device_code", "").strip()
                if not code:
                    self.send_json_response({"success": False, "error": "设备代码不能为空"}, 400)
                    return
                save_device_code(code)
                self.send_json_response({"success": True, "message": "设备代码已保存"})

            elif path == "/api/cron":
                ai_enabled = bool(data.get("ai_chat_enabled", True))
                ai_time = data.get("ai_chat_time", ["03:00", "20:00"])
                hang_enabled = bool(data.get("pc_hang_enabled", True))
                hang_time = data.get("pc_hang_time", ["04:00", "06:00"])
                # 修复：必须读取并传回 hang_minutes，否则保存时被重置为默认 80
                hang_minutes = data.get("hang_minutes", 80)
                ka_sec = data.get("keepalive_seconds", 900)
                ka_enabled = data.get("keepalive_enabled", True)
                silent = data.get("silent_mode", False)
                browser_w = data.get("browser_watch", False)
                pr_hours = data.get("points_refresh_hours", 8)
                try:
                    hang_minutes = int(hang_minutes) if str(hang_minutes).isdigit() else 80
                except Exception:
                    hang_minutes = 80
                save_cron_config(ai_enabled, ai_time, hang_enabled, hang_time,
                                 hang_minutes=hang_minutes,
                                 keepalive_seconds=int(ka_sec) if str(ka_sec).isdigit() else 900,
                                 keepalive_enabled=ka_enabled,
                                 silent_mode=silent, browser_watch=browser_w,
                                 points_refresh_hours=int(pr_hours) if str(pr_hours).isdigit() else 8)
                # 补全：与 Flask 保存路径对齐，写入重载标记使 entrypoint 立即
                # 应用新的保活间隔/开关状态（缺了这条改配置不即时生效）。
                # 修复：与 Flask 版一致，仅在保活配置变化时才写标记，
                # 避免保存无关配置也打断 CtYun.dll 连接窗口。
                old_cron = get_cron_config()
                ka_changed = (int(ka_sec) if str(ka_sec).isdigit() else 900) != int(old_cron.get("keepalive_seconds", 900) or 900) \
                    or bool(ka_enabled) != bool(old_cron.get("keepalive_enabled", True))
                if ka_changed:
                    try:
                        with open(KEEPALIVE_RELOAD_FLAG, "w") as f:
                            f.write(str(int(ka_sec) if str(ka_sec).isdigit() else 900))
                    except Exception:
                        pass
                self.send_json_response({"success": True, "message": "定时任务已保存"})

            elif path == "/api/redeem":
                # 与 Flask 版一致：统一构造标准化配置（scheduleType 归一化 + 保留兑换进度）
                config = _build_redeem_config_from_request(data)
                save_redeem_config(config)
                self.send_json_response({"success": True, "message": "兑换配置已保存"})

            elif path == "/api/redeem/disable":
                save_redeem_config({"enabled": False})
                self.send_json_response({"success": True, "message": "自动兑换已禁用"})

            elif path == "/api/task":
                task_type = data.get("task", "")
                result = execute_task(task_type)
                if result.get("success"):
                    self.send_json_response(result)
                else:
                    self.send_json_response(result, 400)

            elif path == "/api/rewards/fetch":
                result = trigger_rewards_fetch()
                if result.get("success"):
                    self.send_json_response(result)
                else:
                    self.send_json_response(result, 400)

            elif path == "/api/logs/clear":
                clear_logs()
                self.send_json_response({"success": True, "message": "日志已清空"})

            else:
                self.send_error(404, "Not Found")

        def send_json_response(self, data, status=200):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    def run_fallback_server(port):
        server = HTTPServer(("0.0.0.0", port), FallbackHandler)
        print(f"[*] Web 面板服务启动 (fallback mode): http://0.0.0.0:{port}")
        server.serve_forever()


# ============================================
# Main
# ============================================
def main():
    port = int(os.environ.get("WEB_PORT", "8080"))

    # Ensure default device code is generated
    device_info = load_device_code()
    if not device_info.get("device_code"):
        code = generate_device_code()
        save_device_code(code)
        print(f"[*] 首次启动，已自动生成 DEVICECODE: {code[:24]}...")

    log_event("system", f"Web 面板服务启动，端口 {port}")

    if HAS_FLASK:
        app = create_flask_app()
        print(f"[*] Web 面板服务启动: http://0.0.0.0:{port}")
        # 启动后台线程（积分定时刷新）
        _start_background_threads()
        # Use werkzeug's threaded server
        from werkzeug.serving import run_simple
        run_simple("0.0.0.0", port, app, threaded=True)
    else:
        _start_background_threads()
        run_fallback_server(port)


if __name__ == "__main__":
    main()