#!/bin/bash
set -e

DEVICECODE_FILE="/app/data/.devicecode_${APP_USER}"
RESTART_AT_FILE="/tmp/ctyun_restart_at"
SETTINGS_FILE="/app/data/web_settings.json"
DATA_DIR="/app/data"

mkdir -p "$DATA_DIR"

# ============================================
# DEVICECODE 生成与管理
# ============================================
generate_devicecode() {
    cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 32 | head -n 1
}

# 从 Web 面板设置读取 DEVICECODE
get_devicecode_from_settings() {
    if [ -f "$SETTINGS_FILE" ]; then
        local code
        code=$(python3 -c "
import json, sys
try:
    with open('$SETTINGS_FILE', 'r') as f:
        data = json.load(f)
    print(data.get('device_code', ''))
except:
    print('')
" 2>/dev/null)
        echo "$code"
    fi
}

# 保存 DEVICECODE 到设置文件
save_devicecode_to_settings() {
    local code="$1"
    if [ -f "$SETTINGS_FILE" ] || [ -d "$(dirname "$SETTINGS_FILE")" ]; then
        python3 -c "
import json, os
settings_file = '$SETTINGS_FILE'
data = {}
if os.path.exists(settings_file):
    try:
        with open(settings_file, 'r') as f:
            data = json.load(f)
    except:
        pass
data['device_code'] = '$code'
os.makedirs(os.path.dirname(settings_file), exist_ok=True)
with open(settings_file, 'w') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
" 2>/dev/null || true
    fi
}

# 从环境变量获取 APP_USER 和 APP_PASSWORD（如果 Web 面板已配置）
load_settings_from_web() {
    if [ -f "$SETTINGS_FILE" ]; then
        local username password preset
        username=$(python3 -c "
import json
try:
    with open('$SETTINGS_FILE', 'r') as f:
        data = json.load(f)
    print(data.get('username', ''))
except:
    print('')
" 2>/dev/null)
        password=$(python3 -c "
import json
try:
    with open('$SETTINGS_FILE', 'r') as f:
        data = json.load(f)
    print(data.get('password', ''))
except:
    print('')
" 2>/dev/null)

        if [ -n "$username" ] && [ -z "$APP_USER" ]; then
            export APP_USER="$username"
            echo "[*] 从 Web 面板设置读取账号: $APP_USER"
        fi
        if [ -n "$password" ] && [ -z "$APP_PASSWORD" ]; then
            export APP_PASSWORD="$password"
            echo "[*] 从 Web 面板设置读取密码"
        fi
    fi
}

# 读取 Web 面板设置的 Cron
# 注：定时任务已改为由 Web 后端 Python 调度线程驱动（见 app.py 的 _scheduler_loop），
# 不再依赖容器内系统 cron 守护进程，因此这里不再加载/启动 cron。

# ============================================
# 启动流程
# ============================================

# 1. 从 Web 面板设置加载账号密码
load_settings_from_web

# 2. 处理 DEVICECODE
if [ -z "$DEVICECODE" ]; then
    # 先尝试从 Web 面板设置获取
    DEVICECODE=$(get_devicecode_from_settings)

    if [ -z "$DEVICECODE" ] && [ -f "$DEVICECODE_FILE" ]; then
        DEVICECODE=$(cat "$DEVICECODE_FILE")
        echo "[*] 读取到已保存的 DEVICECODE: ${DEVICECODE:0:16}..."
    fi

    if [ -z "$DEVICECODE" ]; then
        DEVICECODE="web_$(generate_devicecode)"
        echo "[*] 首次启动，已生成 DEVICECODE: ${DEVICECODE:0:16}..."
    fi

    # 持久化 DEVICECODE
    echo "$DEVICECODE" > "$DEVICECODE_FILE"
    save_devicecode_to_settings "$DEVICECODE"
    export DEVICECODE
    echo "[*] DEVICECODE 已持久化到: $DEVICECODE_FILE"
else
    echo "[*] 检测到手动传入的 DEVICECODE: ${DEVICECODE:0:16}..."
    echo "$DEVICECODE" > "$DEVICECODE_FILE"
    save_devicecode_to_settings "$DEVICECODE"
fi

# 3. 写入环境变量
# 修复：原 "env >> /etc/environment" 每次容器重启都无条件追加全部环境变量，
# /etc/environment 无限膨胀；且同名变量后行覆盖前行，依赖解析顺序不可靠。
# 改为逐行去重：已存在同名 KEY= 的行先剔除再追加，保持文件恒定增长。
while IFS='=' read -r _key _rest; do
    [ -n "$_key" ] || continue
    case "$_key" in \#*) continue ;; esac
    sed -i "/^${_key}=/d" /etc/environment 2>/dev/null || true
done < <(env)
env >> /etc/environment

# 注：定时任务改由 Web 后端调度线程驱动，不再启动系统 cron 服务。
# 6. 启动 Web 管理面板
echo "[*] 启动 Web 管理面板..."
# 确保数据目录存在（与 pc_login.py 的 HANG_STATUS_FILE 一致，统一使用 /app/data）
mkdir -p /app/data

if [ -f /app/web_server/app.py ]; then
    # 启动 Web 面板在后台
    PYTHONUNBUFFERED=1 python3 /app/web_server/app.py > /app/data/web_panel_server.log 2>&1 &
    WEB_PID=$!
    echo "[*] Web 管理面板已启动 (PID: $WEB_PID, 端口: ${WEB_PORT:-8080})"
    echo "[*] 请访问: http://<容器IP>:${WEB_PORT:-8080}"
else
    echo "[!] 未找到 Web 面板服务 (/app/web_server/app.py)，跳过启动"
fi

set +e

# ============================================
# 独立 Web 面板守护进程（不受主保活循环阻塞，web 退出即秒级拉起）
# ============================================
(
    while true; do
        if ! pgrep -f "/app/web_server/app.py" > /dev/null 2>&1; then
            echo "[web-watch] 检测到 Web 面板未运行，重新启动..."
            PYTHONUNBUFFERED=1 python3 /app/web_server/app.py >> /app/data/web_panel_server.log 2>&1 &
        fi
        sleep 2
    done
) &
WEB_WATCH_PID=$!
echo "[*] Web 面板守护进程已启动 (PID: $WEB_WATCH_PID)"

echo "[*] 启动进程守护模式..."

should_restart_ctyun_now() {
    if [ ! -f "$RESTART_AT_FILE" ]; then
        return 1
    fi

    local restart_at
    restart_at=$(tr -d '[:space:]' < "$RESTART_AT_FILE" 2>/dev/null)
    if ! [[ "$restart_at" =~ ^[0-9]+$ ]]; then
        echo "[!] 检测到无效的重启计划文件，已忽略并清理。"
        rm -f "$RESTART_AT_FILE"
        return 1
    fi

    local now
    now=$(date +%s)
    [ "$now" -ge "$restart_at" ]
}

run_ctyun_with_watch() {
    local duration="$1"
    # 修复：原实现用 return 200/201 传信号，但 bash 函数返回值上限 255，
    # 与 dotnet 真实退出码（如 200/201）冲突，导致"计划重启"被误判。
    # 改为全局哨兵变量传信号（200=计划重启 201=开关停用，正常时置空），
    # 真实退出码走 return/exit_code 原通道不受影响。
    _CTYUN_WATCH_SIGNAL=""
    local scheduled_restart=0
    local disabled_stop=0
    local keepalive_log="/app/data/ctyun_keepalive.log"

    # 将 CtYun.dll 保活日志追加到日志文件，供 Web 面板读取
    timeout --foreground "$duration" dotnet CtYun.dll >> "$keepalive_log" 2>&1 &
    local timeout_pid=$!

    while kill -0 "$timeout_pid" 2>/dev/null; do
        if should_restart_ctyun_now; then
            echo "[*] 检测到兑换成功后的重启计划已到时，准备重启 CtYun.dll。"
            scheduled_restart=1
            rm -f "$RESTART_AT_FILE"
            kill "$timeout_pid" 2>/dev/null || true
            sleep 1
            pkill -f "dotnet CtYun.dll" 2>/dev/null || true
            break
        fi
        # 补全「启用保活心跳」开关：连接窗口内每 2s 检测面板开关，
        # 关闭时立即终止当前连接窗口（否则最长要等 5 分钟才停）
        if ! is_keepalive_enabled; then
            echo "[*] 保活开关已关闭，立即停止当前保活进程。"
            kill "$timeout_pid" 2>/dev/null || true
            sleep 1
            pkill -f "dotnet CtYun.dll" 2>/dev/null || true
            disabled_stop=1
            break
        fi
        sleep 2
    done

    wait "$timeout_pid"
    local exit_code=$?
    # 修复：改为设置全局哨兵变量而非 return 200/201（避免与真实退出码冲突）
    if [ "$scheduled_restart" -eq 1 ]; then
        _CTYUN_WATCH_SIGNAL="200"
        return 0
    fi
    # 201 = 因面板关闭保活开关而中止，主循环收到后直接进入停用状态
    if [ "$disabled_stop" -eq 1 ]; then
        _CTYUN_WATCH_SIGNAL="201"
        return 0
    fi
    _CTYUN_WATCH_SIGNAL=""
    return "$exit_code"
}

# 读取"整体保活周期"：每 N 秒完整启动一次 CtYun.dll（含中间的断开窗口）
# 默认 900 秒（15 分钟，与 Web 面板 get_cron_config 默认值保持一致）。
# 期间被天翼踢掉不秒重连，等下一个周期整体重启。
get_keepalive_seconds() {
    local val
    val=$(python3 -c "
import json,os
f='/app/data/web_settings.json'
if os.path.exists(f):
    with open(f) as fp:
        print(json.load(fp).get('keepalive_seconds', 900))
else:
    print(900)
" 2>/dev/null)
    echo "${val:-900}"
}

# 读取面板「启用保活心跳」开关（缺省视为开启）。
# 补全：此前该开关仅存储不生效——保活主循环无条件执行，关了开关保活照跑。
# 关闭时主循环不再启动 CtYun.dll，实现真正的「停用保活」。
get_keepalive_enabled() {
    local val
    val=$(python3 -c "
import json,os
f='/app/data/web_settings.json'
if os.path.exists(f):
    with open(f) as fp:
        print('True' if json.load(fp).get('keepalive_enabled', True) else 'False')
else:
    print('True')
" 2>/dev/null)
    echo "${val:-True}"
}

is_keepalive_enabled() {
    [ "$(get_keepalive_enabled)" = "True" ]
}

# ============================================
# 将 Web 面板设置同步为 CtYun.dll 的 accounts.json
# 这样保活间隔(keepAliveSeconds)由 Web 面板控制，而非写死在程序里
# ============================================
RELOAD_FLAG="/tmp/ctyun_reload"

sync_accounts_json() {
    python3 - <<'PYEOF' 2>/dev/null || true
import json, os

settings_file = '/app/data/web_settings.json'
accounts_file = '/app/data/accounts.json'

username = ''
password = ''
device_code = ''
keepalive = 900

if os.path.exists(settings_file):
    try:
        with open(settings_file) as f:
            s = json.load(f)
        username = s.get('username', '') or ''
        password = s.get('password', '') or ''
        device_code = s.get('device_code', '') or ''
        keepalive = int(s.get('keepalive_seconds', 900) or 900)
    except Exception:
        pass

# 保活间隔下限保护（程序内部也限制 >=10）
if keepalive < 10:
    keepalive = 10

accounts = {
    "accounts": [
        {
            "name": username or "default",
            "user": username,
            "password": password,
            "deviceCode": device_code
        }
    ],
    "keepAliveSeconds": keepalive
}

with open(accounts_file, 'w', encoding='utf-8') as f:
    json.dump(accounts, f, ensure_ascii=False, indent=2)

print(f"[*] 已同步 accounts.json (账号={username}, keepAliveSeconds={keepalive})")
PYEOF
}

# 检查 Web 面板是否写入了"重载"标记，若有则重启 CtYun.dll 以加载新保活间隔
should_reload_ctyun() {
    if [ -f "$RELOAD_FLAG" ]; then
        rm -f "$RELOAD_FLAG"
        return 0
    fi
    return 1
}

while true; do
    # 补全「启用保活心跳」开关：关闭时不启动保活程序，每 30s 轮询开关状态。
    # 已在运行的进程也要兜底杀掉（防止从开启切到关闭时残留连接窗口进程）。
    # 修复：删除了原有的"Web 面板退出则重启"分支——web-watch 守护子进程
    # （每 2s 检测拉起）已兑底，主循环再拉起会与 web-watch 双重启动、
    # 端口冲突死循环重启。
    if ! is_keepalive_enabled; then
        pkill -f "dotnet CtYun.dll" 2>/dev/null || true
        rm -f "$RELOAD_FLAG" 2>/dev/null || true
        echo "[*] 保活心跳已停用（面板开关关闭），30 秒后重新检查..."
        sleep 30
        continue
    fi

    # 将 Web 面板保活间隔同步到 accounts.json（供 CtYun.dll 读取）
    sync_accounts_json

    # 读取整体保活周期（默认 900 秒 = 15 分钟）
    KEEPALIVE_SEC=$(get_keepalive_seconds)

    echo "======================================================"
    echo "[*] 启动 CtYun.dll（单轮连接窗口，之后整体断开 ${KEEPALIVE_SEC} 秒）..."
    # 单轮只跑 CONNECT_WINDOW 秒：让 dll 连上并保持，被踢不秒重连，时间到整体杀掉
    CONNECT_WINDOW=300
    run_ctyun_with_watch "${CONNECT_WINDOW}"
    EXIT_CODE=$?
    # timeout 到期会自动杀掉 CtYun.dll，进入断开窗口
    pkill -f "dotnet CtYun.dll" 2>/dev/null || true
    sleep 2

    # 修复：改读全局哨兵变量判断信号（原 return 200/201 与真实退出码冲突）。
    # 另外：200（计划重启）此前未 continue，会误入断开窗口挂起 N 小时，
    # 导致兑换成功后的计划重启被拖延；现在两种信号都立即进入下一轮。
    if [ "$_CTYUN_WATCH_SIGNAL" = "201" ]; then
        echo "[*] 保活已被面板开关停用，进入停用状态。"
        continue
    fi
    if [ "$_CTYUN_WATCH_SIGNAL" = "200" ]; then
        echo "[*] 已按兑换计划完成重启，立即开始下一轮连接窗口。"
        continue
    fi
    echo "[*] 本轮连接窗口结束，进入断开窗口（${KEEPALIVE_SEC} 秒）。"

    # Web 面板保存保活间隔后写入重载标记，立即重启以应用新间隔
    if should_reload_ctyun; then
        echo "[*] 检测到保活间隔变更，立即重启 CtYun.dll 以应用新间隔。"
        pkill -f "dotnet CtYun.dll" 2>/dev/null || true
        sleep 2
        continue
    fi

    echo "[*] 容器挂起中，将在 ${KEEPALIVE_SEC} 秒后重新启动程序，请等待..."
    # 修复：原 sleep "$KEEPALIVE_SEC" 一次性阻塞整个断开窗口（最长 6 小时），
    # 期间面板改间隔/关开关都无法打断。改为 10 秒分段睡眠，
    # 开关关闭或间隔变更均可在 ≤10 秒内响应。
    remaining=$KEEPALIVE_SEC
    while [ "$remaining" -gt 0 ]; do
        if ! is_keepalive_enabled; then
            echo "[*] 保活开关已关闭，提前结束断开窗口。"
            break
        fi
        if [ -f "$RELOAD_FLAG" ]; then
            rm -f "$RELOAD_FLAG"
            echo "[*] 保活间隔变更，提前结束断开窗口以应用新间隔。"
            break
        fi
        if [ "$remaining" -gt 10 ]; then
            sleep 10
            remaining=$((remaining - 10))
        else
            sleep "$remaining"
            remaining=0
        fi
    done

done