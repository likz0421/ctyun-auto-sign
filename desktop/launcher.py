"""
天翼云电脑自动化 - Windows 桌面版启动器

等价于 Docker 版 entrypoint.sh 的 Python 重写，职责：
1. 设备码（DEVICECODE）生成与持久化（对应 entrypoint 的 generate_devicecode 逻辑）
2. 启动 Web 管理面板（web_server/app.py），并以后台线程秒级守护拉起（web-watch）
3. 保活主循环：连接窗口 300s 跑 CtYun.dll（.NET 便携运行时），断开窗口 keepalive_seconds；
   期间响应三种信号：RELOAD_FLAG（保活间隔变更）、RESTART_AT（兑换后计划重启）、
   keepalive_enabled 开关（关闭即停）
4. 将 Web 面板设置同步为 CtYun.dll 读取的 accounts.json（对应 sync_accounts_json）

设计要点：
- 所有跨进程信号走数据目录 .tmp 下的文件（与 web_server/app.py 的 tmp_path 一致），
  替代容器内的 /tmp 与 bash 全局哨兵变量。
- 保活日志写 data/ctyun_keepalive.log（与 Web 面板判定"保活运行中"的路径一致）。
- 控制台窗口：无参启动时保留控制台便于观察；--no-console 静默模式（计划任务/开机自启用）。
"""

import json
import os
import random
import string
import subprocess
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# 公共路径模块（与 app/ 脚本、web_server 同级）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import (  # noqa: E402
    BASE_DIR,
    CTYUN_CORE_DIR,
    DATA_DIR,
    DOTNET_DIR,
    WEB_SERVER_DIR,
    data_path,
    tmp_path,
)
# 【清理说明】移除未使用的 WEB_DIR / app_path 导入（web 面板服务端已改用
# WEB_SERVER_DIR，web 前端目录由 web_server/app.py 自行定位，启动器无需引用）

SETTINGS_FILE = data_path("web_settings.json")
ACCOUNTS_FILE = data_path("accounts.json")
KEEPALIVE_LOG = data_path("ctyun_keepalive.log")
WEB_PANEL_LOG = data_path("web_panel_server.log")
RESTART_AT_FILE = tmp_path("ctyun_restart_at")
RELOAD_FLAG = tmp_path("ctyun_reload")
WEB_PORT = int(os.environ.get("WEB_PORT", "8090"))

# 连接窗口时长（秒）：单轮 CtYun.dll 运行时间，时间到整体结束进入断开窗口
CONNECT_WINDOW = 300
# dotnet 启动器路径（随软件分发的便携 .NET 运行时）
DOTNET_EXE = os.path.join(DOTNET_DIR, "dotnet.exe")
CTYUN_DLL = os.path.join(CTYUN_CORE_DIR, "CtYun.dll")

# Python 解释器路径：
# - 源码运行：直接用当前解释器（sys.executable）
# - PyInstaller 打包后：sys.executable 是 CtYun.exe 自身，不能再用它运行 .py 子脚本；
#   改用随包分发的 runtime/python.exe（完整 Python 运行时，与开发环境同版本 3.12），
#   这样 Web 面板进程内 sys.executable 也是它，面板拉起任务脚本时无需任何适配
if getattr(sys, "frozen", False):
    PYTHON_EXE = os.path.join(BASE_DIR, "runtime", "python.exe")
else:
    PYTHON_EXE = sys.executable

# 全局停止标记（主进程收到退出信号后置位，各循环线程感知后收尾）
_SHUTDOWN = threading.Event()


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)


# ============================================
# 设置读取（对应 entrypoint.sh 的 get_keepalive_seconds / get_keepalive_enabled）
# ============================================
def load_web_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_keepalive_seconds() -> int:
    """整体保活周期（秒），默认 900。"""
    try:
        val = int(load_web_settings().get("keepalive_seconds", 900) or 900)
    except Exception:
        val = 900
    # 保活间隔下限保护（程序内部也限制 >=10）
    return max(10, val)


def is_keepalive_enabled() -> bool:
    """保活开关（缺省视为开启）。"""
    return bool(load_web_settings().get("keepalive_enabled", True))


def sync_accounts_json() -> None:
    """将 Web 面板设置同步为 CtYun.dll 的 accounts.json（对应 entrypoint 的同名函数）。"""
    settings = load_web_settings()
    username = settings.get("username", "") or ""
    password = settings.get("password", "") or ""
    device_code = settings.get("device_code", "") or ""
    keepalive = max(10, int(settings.get("keepalive_seconds", 900) or 900))

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
    try:
        # 原子写：tmp + os.replace，避免写入中途崩溃留下半截 JSON
        tmp_file = ACCOUNTS_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, ACCOUNTS_FILE)
        log(f"已同步 accounts.json（账号={username or 'default'}，keepAliveSeconds={keepalive}）")
    except Exception as e:
        log(f"[!] 同步 accounts.json 失败: {e}")


# ============================================
# 设备码管理（对应 entrypoint.sh 的 generate_devicecode）
# ============================================
def ensure_devicecode() -> str:
    """确保 DEVICECODE 存在并持久化：设置文件 > 数据目录文件 > 新生成。"""
    settings = load_web_settings()
    code = (os.environ.get("DEVICECODE") or "").strip()
    if not code:
        code = (settings.get("device_code") or "").strip()
    if not code:
        env_file = data_path(".devicecode_default")
        try:
            if os.path.exists(env_file):
                with open(env_file, "r", encoding="utf-8") as f:
                    code = f.read().strip()
        except Exception:
            pass
    if not code:
        code = "web_" + "".join(random.choices(string.ascii_letters + string.digits, k=32))
        log(f"首次启动，已生成 DEVICECODE: {code[:16]}...")
    # 持久化到数据目录与设置文件（无论来源）
    try:
        with open(data_path(".devicecode_default"), "w", encoding="utf-8") as f:
            f.write(code)
    except Exception:
        pass
    try:
        settings["device_code"] = code
        tmp_file = SETTINGS_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, SETTINGS_FILE)
    except Exception:
        pass
    os.environ["DEVICECODE"] = code
    return code


# ============================================
# Web 面板启动与守护（对应 entrypoint.sh 的 web-watch 子进程）
# ============================================
def start_web_panel() -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["DATA_DIR"] = DATA_DIR
    env["WEB_PORT"] = str(WEB_PORT)
    env["CTYUN_DESKTOP"] = "1"  # 标记桌面模式（预留）
    log_file = open(WEB_PANEL_LOG, "a", encoding="utf-8")
    # 【修复说明】web_server 目录与 paths.py 同级、不在 app/ 下，此前误用
    # app_path("web_server", "app.py") 拼成 desktop\app\web_server\app.py 导致
    # 面板启动即 FileNotFoundError（web-watch 每 2s 无限拉起失败）；
    # 改用 paths.WEB_SERVER_DIR 正确指向 desktop\web_server
    app_entry = os.path.join(WEB_SERVER_DIR, "app.py")
    # 【打包适配】frozen 后改用随包 runtime/python.exe 启动面板（见 PYTHON_EXE 注释）
    proc = subprocess.Popen(
        [PYTHON_EXE, "-u", app_entry],
        stdout=log_file,
        stderr=log_file,
        env=env,
        cwd=WEB_SERVER_DIR,
        # 桌面版适配：Windows 下 CREATE_NEW_PROCESS_GROUP 避免子进程随控制台 CTRL_C 连带退出
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
    )
    return proc


def web_watch_loop() -> None:
    """后台守护线程：每 2s 检测 Web 面板进程，退出即拉起（对应 web-watch）。"""
    web_proc = None
    while not _SHUTDOWN.is_set():
        try:
            if web_proc is None or web_proc.poll() is not None:
                log("[web-watch] 检测到 Web 面板未运行，重新启动...")
                web_proc = start_web_panel()
        except Exception as e:
            log(f"[web-watch] 拉起 Web 面板失败: {e}")
        _SHUTDOWN.wait(2)
    # 收尾：结束 Web 面板子进程
    if web_proc is not None and web_proc.poll() is None:
        try:
            web_proc.terminate()
        except Exception:
            pass


# ============================================
# CtYun.dll 运行与监视（对应 run_ctyun_with_watch / should_restart_ctyun_now）
# ============================================
def _read_restart_at() -> int:
    """读取计划重启时间戳；无效/缺失返回 0。"""
    try:
        with open(RESTART_AT_FILE, "r", encoding="utf-8") as f:
            val = f.read().strip()
        if val.isdigit():
            return int(val)
    except Exception:
        pass
    return 0


def _build_ctyun_env() -> dict:
    env = os.environ.copy()
    # 便携 .NET 运行时查找路径
    env["DOTNET_ROOT"] = DOTNET_DIR
    # DOTNET_MULTILEVEL_LOOKUP=0：禁止 .NET 回退探测系统级安装，只用随包运行时
    env["DOTNET_MULTILEVEL_LOOKUP"] = "0"
    return env


def run_ctyun_with_watch(duration: int) -> str:
    """运行 CtYun.dll 一轮连接窗口，期间监视三种信号。

    返回哨兵字符串（替代 bash 版的全局哨兵变量，避免与真实退出码冲突）：
    - "200"：检测到兑换后的计划重启到时，已终止本窗口
    - "201"：保活开关被面板关闭，已终止本窗口
    - ""：正常跑完窗口或进程自行退出
    """
    if not os.path.isfile(DOTNET_EXE):
        log(f"[!] 未找到便携 .NET 运行时: {DOTNET_EXE}，保活无法启动")
        _SHUTDOWN.wait(30)
        return ""
    if not os.path.isfile(CTYUN_DLL):
        log(f"[!] 未找到保活核心 CtYun.dll: {CTYUN_DLL}，保活无法启动")
        _SHUTDOWN.wait(30)
        return ""

    scheduled_restart = False
    disabled_stop = False
    keepalive_log = open(KEEPALIVE_LOG, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [DOTNET_EXE, "CtYun.dll"],
        cwd=CTYUN_CORE_DIR,
        stdout=keepalive_log,
        stderr=keepalive_log,
        env=_build_ctyun_env(),
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
    )

    deadline = time.time() + duration
    while time.time() < deadline and proc.poll() is None:
        if _SHUTDOWN.is_set():
            break
        # 计划重启：兑换成功后 pc_login 写入的时间戳到时
        restart_at = _read_restart_at()
        if restart_at and time.time() >= restart_at:
            log("检测到兑换成功后的重启计划已到时，准备重启 CtYun.dll。")
            scheduled_restart = True
            _remove(RESTART_AT_FILE)
            _kill_proc(proc)
            break
        # 保活开关：连接窗口内每 2s 检测面板开关，关闭立即终止当前窗口
        if not is_keepalive_enabled():
            log("保活开关已关闭，立即停止当前保活进程。")
            disabled_stop = True
            _kill_proc(proc)
            break
        # 2 秒粒度轮询（与 bash 版 sleep 2 一致）
        _SHUTDOWN.wait(2)

    # 窗口时间到：整体杀掉（不等待进程自然退出，被踢后不秒重连）
    _kill_proc(proc)
    if scheduled_restart:
        return "200"
    if disabled_stop:
        return "201"
    return ""


def _remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _kill_proc(proc: subprocess.Popen) -> None:
    """终止子进程并等待退出；Windows 下先发 taskkill /T 杀进程树（dotnet 可能有子进程）。"""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        else:
            proc.terminate()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


# ============================================
# 主循环（对应 entrypoint.sh 的 while true 主循环）
# ============================================
def keepalive_main_loop() -> None:
    while not _SHUTDOWN.is_set():
        # 开关关闭：不启动保活，每 30s 轮询（对应 bash 版停用分支）
        if not is_keepalive_enabled():
            _remove(RELOAD_FLAG)
            log("保活心跳已停用（面板开关关闭），30 秒后重新检查...")
            _SHUTDOWN.wait(30)
            continue

        # 同步面板设置到 accounts.json（供 CtYun.dll 读取新保活间隔/账号）
        sync_accounts_json()
        keepalive_sec = get_keepalive_seconds()

        log("=" * 54)
        log(f"启动 CtYun.dll（单轮连接窗口 {CONNECT_WINDOW} 秒，之后整体断开 {keepalive_sec} 秒）...")
        signal = run_ctyun_with_watch(CONNECT_WINDOW)

        # 201 = 因面板关闭保活开关而中止，回到循环顶部进入停用状态
        if signal == "201":
            log("保活已被面板开关停用，进入停用状态。")
            continue
        # 200 = 兑换计划重启：跳过断开窗口，立即开始下一轮连接窗口
        if signal == "200":
            log("已按兑换计划完成重启，立即开始下一轮连接窗口。")
            continue

        # Web 面板保存保活间隔后写入重载标记 → 立即重启以应用新间隔
        if os.path.exists(RELOAD_FLAG):
            _remove(RELOAD_FLAG)
            log("检测到保活间隔变更，立即重启 CtYun.dll 以应用新间隔。")
            continue

        # 断开窗口：10 秒分段睡眠，期间开关关闭/间隔变更均可在 ≤10s 内响应
        log(f"本轮连接窗口结束，进入断开窗口（{keepalive_sec} 秒），{int(keepalive_sec // 60)} 分 {int(keepalive_sec % 60)} 秒后重新连接...")
        remaining = keepalive_sec
        while remaining > 0 and not _SHUTDOWN.is_set():
            if not is_keepalive_enabled():
                log("保活开关已关闭，提前结束断开窗口。")
                break
            if os.path.exists(RELOAD_FLAG):
                _remove(RELOAD_FLAG)
                log("保活间隔变更，提前结束断开窗口以应用新间隔。")
                break
            step = min(10, remaining)
            _SHUTDOWN.wait(step)
            remaining -= step


# ============================================
# 单实例保护（防止用户双击多次导致多实例并存：面板抢 8090 端口、
# 多个保活循环同时跑 CtYun.dll 登录互相踢下线）
# ============================================
def acquire_single_instance_lock() -> bool:
    """用数据目录下锁文件的独占打开实现单实例检测（跨平台、无第三方依赖）。

    独占打开（sharing=None）后，第二个实例再次打开会抛 PermissionError；
    已有实例退出时锁文件句柄随之释放，不影响下次启动。锁文件常驻数据目录，
    属正常运行产物，不清理（保持简单，避免退出竞态）。
    """
    lock_path = data_path(".instance.lock")
    try:
        # O_RDWR|O_CREAT + 独占共享模式：Windows 下打开即锁死整个文件
        handle = os.open(lock_path, os.O_RDWR | os.O_CREAT)
        # msvcrt 仅 Windows 有；非 Windows 平台用 fcntl（当前产品只发 Windows，保底 try）
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # 句柄必须常驻不关（关了锁就没了），挂到全局变量上防止被 GC
        globals()["_LOCK_HANDLE"] = handle
        return True
    except OSError:
        return False


def main() -> None:
    log("=" * 54)
    log("天翼云电脑自动化 - Windows 桌面版启动器")
    log(f"应用目录: {BASE_DIR}")
    log(f"数据目录: {DATA_DIR}")
    log("=" * 54)

    # 0. 单实例保护：已有实例在跑时直接提示退出（对应面板/保活端口与登录互踢问题）
    if not acquire_single_instance_lock():
        log("[!] 检测到程序已在运行（请勿重复启动）。")
        log("    如需重启，请先退出已运行的实例（关闭其控制台窗口或任务管理器结束 CtYun.exe）。")
        _SHUTDOWN.wait(5)  # 给用户 5 秒看清提示后自动退出
        return

    # 1. 设备码初始化（对应 entrypoint.sh 步骤 2）
    code = ensure_devicecode()
    log(f"DEVICECODE: {code[:16]}...")

    # 2. 启动 Web 面板 + 守护线程（web-watch）
    web_thread = threading.Thread(target=web_watch_loop, daemon=True)
    web_thread.start()
    log(f"Web 管理面板已启动，访问: http://127.0.0.1:{WEB_PORT}")

    # 3. 首次启动即同步一次 accounts.json，确保 CtYun.dll 拿到账号
    sync_accounts_json()

    # 4. 注册退出处理：Ctrl+C / 窗口关闭时优雅收尾
    import atexit

    def _on_exit():
        _SHUTDOWN.set()
        log("启动器退出，各后台任务已收尾。")

    atexit.register(_on_exit)

    # 【修复说明】此前主线程 sleep(3600) 挂起、从未调用保活主循环，
    # 导致 CtYun.dll 保活完全不启动（keepalive_main_loop 成死代码，仅面板可用）；
    # 改为主线程运行保活主循环（内部以 _SHUTDOWN.wait 感知退出），Web 面板守护仍在后台线程
    try:
        keepalive_main_loop()
    except KeyboardInterrupt:
        _on_exit()


if __name__ == "__main__":
    main()
