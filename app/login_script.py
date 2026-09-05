"""
天翼云电脑
"""

import atexit
import datetime
import json
import os
import random
import sys
import threading
import time
from typing import Optional, Union

import ddddocr
from DrissionPage import ChromiumOptions, ChromiumPage

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

PRESET_MESSAGES = [
    "今天北京天气怎么样？（简短回答）",
    "给我讲一个冷笑话。（简短回答）",
    "来一首古诗。（简短回答）",
    "空腹可以吃饭吗？（简短回答）",
    "推荐一部人生必看电影。（简短回答）",
]


# ==========================================
# Cookie 持久化辅助函数
# ==========================================
def save_cookies(page: ChromiumPage, file_path: str) -> None:
    """获取当前页面的 Cookie 并持久化保存到本地文件。"""
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        cookies = page.cookies()
        if not cookies:
            print(f"[-] 抓取到的 Cookie 为空，已取消保存操作: {file_path}")
            return
        # 原因：确保获取到的 Cookie 具备业务层面的真实登录凭证，避免保存无用的访客 Cookie
        has_yl_token = False

        if isinstance(cookies, list):
            has_yl_token = any(cookie.get("name") == "YL-Token" for cookie in cookies)
        elif isinstance(cookies, dict):
            has_yl_token = "YL-Token" in cookies
        if not has_yl_token:
            print(f"[-] Cookie 中缺失关键凭证 'YL-Token'，已取消保存操作: {file_path}")
            return
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=4)
        print(f"[*] Cookie 已成功保存至: {file_path}")

    except Exception as e:
        print(f"[!] 保存 Cookie 失败: {e}")


def load_cookies(page: ChromiumPage, file_path: str) -> bool:
    """从本地文件读取 Cookie 并加载到浏览器中。"""
    if not os.path.exists(file_path):
        print(f"[-] 未发现本地 Cookie 缓存文件: {file_path}")
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        page.set.cookies(cookies)
        print(f"[*] 本地 Cookie 加载完成: {file_path}")
        return True
    except Exception as e:
        print(f"[!] 加载 Cookie 失败: {e}")
        return False


# ==========================================
# 浏览器初始化与核心功能函数
# ==========================================


def init_browser_options() -> ChromiumOptions:
    """初始化并配置 Chromium 浏览器的启动参数。"""
    options = ChromiumOptions()
    # 复用系统已安装的 chromium，避免 DrissionPage 再次下载自带 Chromium 导致镜像/容器体积翻倍
    try:
        options.set_paths(browser_path="/usr/bin/chromium")
    except TypeError:
        options.set_paths(chromium_path="/usr/bin/chromium")
    options.set_argument("--no-sandbox")
    options.set_argument("--disable-gpu")
    options.set_argument("--disable-dev-shm-usage")
    options.headless()
    return options


def fill_credentials(page: ChromiumPage, username: str, password: str) -> None:
    """在页面上填写账号与密码信息。"""
    print("正在输入账号信息...")
    # 修复：原 page.ele 未设 timeout，SPA 未渲染完会立刻失败；改为显式 15s 并补空值检查，报错信息更明确。
    account_input = page.ele('css:input[type="text"]', timeout=15)
    if not account_input:
        raise RuntimeError("未找到账号输入框（input[type=text]）")
    account_input.clear()
    account_input.input(username)

    print("正在输入密码信息...")
    password_input = page.ele('css:input[type="password"]', timeout=15)
    if not password_input:
        raise RuntimeError("未找到密码输入框（input[type=password]）")
    password_input.clear()
    try:
        page.run_js(
            "var el=document.querySelector('input[type=password]'); if(el){el.value=arguments[0]; el.dispatchEvent(new Event('input',{bubbles:true}));}",
            password,
        )
    except Exception:
        password_input.input(password)


def handle_captcha(page: ChromiumPage) -> None:
    """检测页面是否存在图形验证码容器，若存在则提取图片并填充识别结果。"""
    print("正在检测图形验证码容器...")
    captcha_container = page.ele("css:.fgt-capt-ct", timeout=2)

    if not captcha_container:
        print("当前无需处理图形验证码。")
        return

    print("检测到图形验证码，开始提取并识别...")
    # 修复：验证码图片查找增加 timeout 与空值检查，图片未加载完时不再抛低级异常导致整个任务重试。
    pic_ele = captcha_container.ele("css:img", timeout=5)
    if not pic_ele:
        print("检测到验证码容器但验证码图片未渲染，跳过验证码处理。")
        return
    pic_bytes = pic_ele.get_screenshot(as_bytes=True)

    ocr_result = get_bytes_numeric_captcha(pic_bytes)
    print(f"OCR 识别结果为: {ocr_result}")

    input_ele = captcha_container.ele('css:input[placeholder="输入图形验证码"]', timeout=5)
    if not input_ele:
        print("未找到验证码输入框，跳过验证码处理。")
        return
    input_ele.clear()
    input_ele.input(ocr_result)


def analyze_login_response(response_body: Union[dict, str, None]) -> int:
    """分析登录接口的返回体，提取并映射为内部状态码。"""
    if not response_body or not isinstance(response_body, dict):
        return 0

    code = response_body.get("code")
    msg = response_body.get("msg", "")

    if code == 51040 and "用户名或密码错误" in msg:
        return 1
    elif code == 51030:
        return 2
    elif code == 51040 and "图形验证码" in msg:
        return 3
    return -1


def save_screenshot(page: ChromiumPage) -> None:
    file_name = f"{os.getenv('APP_USER')}_{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    if os.getenv("RUNNING_IN_DOCKER") == "true":
        path = "/app/data"
    else:
        path = "./"
    page.get_screenshot(path=path, name=file_name, full_page=True)


def execute_login_with_listener(
    page: ChromiumPage,
    target_url: str,
    username: str,
    password: str,
) -> Optional[bool]:
    """执行完整的账号密码登录流程。"""
    print("\n--- 开始账密登录流程 ---")
    print(f"访问登录页面: {target_url}")
    page.get(target_url)
    page.wait.load_start()

    # 修复：登录页为 SPA，load_start 返回时 DOM 尚未渲染完成，原代码立即调用 fill_credentials
    # 导致 page.ele 找不到 input[type=text] 抛"没有找到元素"（今天 03:00 尝试 3 失败的直接死因）。
    # 改为显式等待账号输入框渲染可见（20s 上限），就绪后再继续填表。
    if not page.wait.ele_displayed('css:input[type="text"]', timeout=20):
        raise RuntimeError("登录页未渲染出账号输入框，可能页面加载异常")

    fill_credentials(page, username, password)

    handle_captcha(page)

    if not page.wait.ele_displayed("css:button.lgm-submit-ct", timeout=5):
        raise RuntimeError("页面未渲染出登录按钮")

    # 确认显示后，重新提取元素对象
    login_button = page.ele("css:button.lgm-submit-ct")

    page.listen.start("api/auth/iam/login")
    login_button.click()

    print("已点击登录，等待接口返回...")
    packet = page.listen.wait(timeout=5)
    page.listen.stop()

    if not packet:
        raise RuntimeError("未捕获到登录接口数据包，检查是否已重定向。")

    status_code = analyze_login_response(packet.response.body)

    if status_code == 0:
        print("登录成功")
        return True
    elif status_code == 1:
        print("登录失败：用户名或密码错误。")
        return False
    elif status_code in [2, 3]:
        print(f"登录受阻（状态码 {status_code}），准备重试...")
        time.sleep(1)
        raise RuntimeError("登录受阻，准备重试。")
    else:
        print(f"未知响应: {packet.response.body}")
        return False


def display_user_info(page: ChromiumPage) -> None:
    """
    提取并输出当前登录的用户信息（手机号掩码）。
    """
    user_selector = "css:div.username span.txt"

    if page.wait.ele_displayed(user_selector, timeout=5):
        username_text = page.ele(user_selector).text
        if username_text:
            print(f"[*] 登录成功，当前登录用户: {username_text}")
        else:
            raise RuntimeError("未能获取到当前用户信息，可能页面未完全渲染。")
    else:
        raise RuntimeError("[-] 未能获取到当前用户信息，可能页面未完全渲染。")


def _now() -> str:
    """返回带毫秒的本地时间戳，用于详细日志。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _send_preset_message(page: ChromiumPage, input_selector: str) -> None:
    """在没有历史对话时，发送一条预设消息触发真实对话，确保积分正常领取。

    优先使用用户在 web_settings.json 中配置的 preset_messages，
    否则回退到内置 PRESET_MESSAGES。
    """
    import random
    # 读取用户自定义预设（settings 模块已挂载到 app 目录）
    preset_pool = list(PRESET_MESSAGES)
    try:
        settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web_server", "data", "web_settings.json")
        if os.path.exists(settings_path):
            with open(settings_path, "r", encoding="utf-8") as f:
                s = json.load(f)
            user_msgs = s.get("preset_messages") or []
            if user_msgs:
                preset_pool = [str(m) for m in user_msgs if str(m).strip()]
    except Exception:
        pass

    msg = random.choice(preset_pool) if preset_pool else "你好"
    print(f"[{_now()}] [AI对话] 发送预设消息：{msg}")

    # 定位输入框。实测（探测脚本 probe）天翼云聊天输入框就是 div.input-box.input-wrap 容器本身
    # （contenteditable="true"，内部无任何子元素），因此**优先直接用容器自身作为输入目标**。
    # 修复：原逻辑先在容器内找 textarea/contenteditable 子元素（永远找不到，
    # 因为输入框就是容器本身），再全页兜底；且 DrissionPage 找不到元素时返回 NoneElement
    # 假对象（不是 None），原 `if ta is None` 检查全部失效，一路放行到 click 才抛
    # "没有找到元素"（今天 03:00 三次尝试全败的真正死因）。
    box = page.ele(input_selector, timeout=5)
    ta = None
    try:
        box_is_editable = (
            box.attr("contenteditable")
            and str(box.attr("contenteditable")).lower() != "false"
        ) or box.tag in ("textarea", "input")
    except Exception:
        box_is_editable = False

    if not box_is_editable:
        # 兼容旧版页面结构：容器内找子输入元素
        try:
            child = box.ele(
                "css:textarea, div[contenteditable='true'], div[role='textbox']",
                timeout=2,
            )
            if child and not isinstance(child, type(None)):
                # NoneElement 判定：DrissionPage 找不到时返回假对象，需用 bool() 显式判空
                if child and bool(child):
                    ta = child
        except Exception:
            ta = None
        if ta is None:
            # 全页兜底再找一次
            try:
                cand = page.ele(
                    "css:textarea, div[contenteditable='true'], div[role='textbox']",
                    timeout=3,
                )
                if cand and bool(cand):
                    ta = cand
            except Exception:
                ta = None
    if ta is None:
        # 兜底：容器本身是可输入元素则直接用（当前页面正是这种情况）
        ta = box
    if ta is None or not bool(ta):
        raise RuntimeError("未找到聊天输入框（textarea/contenteditable 均不可用）")

    # 修复：SPA 下 ta.input() 后前端可能重渲染输入区使旧元素引用失活，
    # 直接 ta.press("Enter") 会以原始定位器抛"没有找到元素"。
    # 策略：每次关键操作前都重新定位，获取最新元素。

    def _fresh_ta():
        """重新定位聊天输入框：优先页面级 contenteditable 容器（当前页面真实结构），
        再回退容器内子元素/全页查找；NoneElement 需 bool() 显式判空。"""
        # 1) 优先：input-box 容器本身是 contenteditable（当前真实结构）
        try:
            cand = page.ele(input_selector, timeout=2)
            if cand and bool(cand):
                ce = cand.attr("contenteditable")
                if ce and str(ce).lower() != "false":
                    return cand
        except Exception:
            pass
        # 2) 回退：原逻辑（容器内子元素 → 全页）
        for scope in (box, page):
            try:
                el = scope.ele(
                    "css:textarea, div[contenteditable='true'], div[role='textbox']",
                    timeout=2,
                )
                if el and bool(el):
                    return el
            except Exception:
                continue
        return None

    def _read_input_text(el) -> str:
        """读取输入框当前内容（textarea 读 value，contenteditable 读 textContent）。"""
        try:
            return str(
                page.run_js(
                    """
                    const el = arguments[0];
                    if (el.value !== undefined && el.value !== null) { return String(el.value); }
                    return el.textContent || '';
                    """,
                    el,
                )
                or ""
            )
        except Exception:
            return ""

    ta.click()
    try:
        ta.input(msg)
    except Exception:
        # JS 兜底输入：兼容 contenteditable 与 textarea，并模拟输入事件（先重定位，避免旧引用已失活）
        try:
            fresh = _fresh_ta() or ta
            page.run_js(
                """
                const el = arguments[0];
                const val = arguments[1];
                el.focus();
                if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                  const setter = (Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')
                                 || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')).set;
                  setter.call(el, val);
                } else {
                  el.textContent = val;
                }
                el.dispatchEvent(new InputEvent('input', { bubbles: true, data: val }));
                """,
                fresh,
                msg,
            )
            print(f"[{_now()}] [AI对话] 已通过 JS 兜底输入消息")
        except Exception as e:
            print(f"[!] 消息输入兜底失败: {e}")
            raise RuntimeError(f"消息输入失败: {e}")
    time.sleep(0.5)

    # 修复：发送动作重构为三层回退，不再依赖单一元素引用：
    #   1) 重定位输入框并聚焦，用 JS 派发 Enter 键事件（不受元素失活影响）；
    #   2) 回退到传统元素 press("Enter")（元素已重定位）；
    #   3) 最后回退点击发送按钮。
    sent = False
    try:
        fresh = _fresh_ta() or ta
        try:
            fresh.click()  # 确保焦点在输入框，Enter 事件才会派发到正确目标
        except Exception:
            pass
        page.run_js(
            """
            const el = arguments[0];
            const target = (document.activeElement && el.contains(document.activeElement)) ? document.activeElement : el;
            const opts = { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', keyCode: 13, which: 13 };
            ['keydown', 'keypress', 'keyup'].forEach(function(t){ target.dispatchEvent(new KeyboardEvent(t, opts)); });
            """,
            fresh,
        )
        sent = True
        print(f"[{_now()}] [AI对话] 已通过 JS 派发 Enter 发送")
    except Exception as e:
        print(f"[{_now()}] [AI对话] JS Enter 派发异常，尝试下一方式: {e}")
    if not sent:
        try:
            fresh = _fresh_ta() or ta
            fresh.press("Enter")
            sent = True
        except Exception:
            pass
    if not sent:
        try:
            send_btn = box.ele(
                "css:button.send-btn, button:has(svg), button[class*='send']",
                timeout=2,
            )
            if send_btn:
                send_btn.click()
                sent = True
        except Exception:
            pass

    # 修复：原逻辑只检查"发送动作是否报错"（弱断言），前端吞掉 Enter 但未抛异常时会误报成功。
    # 增加强校验：发送后输入框应被清空；若仍有内容先点发送按钮补救；仍未清空则不直接报错
    # （避免"前端不清空输入框"场景误杀），改为告警并交由后续"AI 回复检测"做最终判定。
    time.sleep(1.5)
    remain = _read_input_text(_fresh_ta() or ta)
    if remain.strip():
        print(f"[{_now()}] [AI对话] 发送后输入框未清空（残留 {len(remain)} 字），尝试发送按钮补救...")
        try:
            send_btn = box.ele(
                "css:button.send-btn, button:has(svg), button[class*='send']",
                timeout=2,
            )
            if send_btn:
                send_btn.click()
        except Exception:
            pass
        time.sleep(1.5)
        remain = _read_input_text(_fresh_ta() or ta)
        if remain.strip():
            print(f"[{_now()}] [AI对话] 警告：补救后输入框仍有内容（{len(remain)} 字），将以是否出现 AI 回复判定消息是否真正发出")
    print(f"[{_now()}] [AI对话] 预设消息发送流程完成，等待 AI 回复")


def chat_and_earn_points(page: ChromiumPage) -> None:
    """登录成功后跳转 AI 聊天页，**始终主动发送一条消息**并等待 AI 回复。

    实测天翼云 AI 对话积分（+100/天）必须产生真实对话交互才会到账，
    仅访问页面/沿用上次对话会漏领积分。因此本函数固定流程：
    等待聊天界面就绪 → 主动发送一条预设消息 → 等待新回复出现 → 确认完成。
    """
    chat_url = "https://eaichat.ctyun.cn/chat/#/aichat"
    print(f"[{_now()}] [AI对话] 开始：主动发送消息触发今日对话")

    if page.url != chat_url:
        print(f"[{_now()}] [AI对话] 跳转至聊天页面: {chat_url}")
        page.get(chat_url)

    print(f"[{_now()}] [AI对话] 等待聊天界面加载...")
    input_selector = "css:div.input-box.input-wrap"
    # 修复：15s 对聊天页 SPA 首次渲染可能不足（容器冷启动时更慢），放宽到 20s 减少误判。
    if not page.wait.ele_displayed(input_selector, timeout=20):
        raise RuntimeError("未找到聊天输入框（页面可能未加载完成）")
    print(f"[{_now()}] [AI对话] 聊天界面已加载，输入框可见")

    # 提取并输出用户信息
    try:
        display_user_info(page)
    except Exception as e:
        print(f"[{_now()}] [AI对话] 提取用户信息失败（不影响主流程）: {e}")

    # 等待历史对话渲染稳定，记录发送前快照（用于判断新回复是否出现）
    print(f"[{_now()}] [AI对话] 等待对话内容渲染稳定...")

    # 修复：改用 JS 快照读取历史回复（数量+最后一条文本）。
    # 原用 page.eles() 查询，页面上无历史元素时 DrissionPage 每次都等满默认 10 秒超时，
    # 12 次循环实测空耗约 2 分 20 秒（今天 03:00/12:44/12:54 三次任务日志均如此）。
    def _history_snapshot():
        """JS 直查历史回复：返回 (数量, 最后一条文本)，无查找等待开销。"""
        try:
            res = page.run_js(
                """
                const els = Array.from(document.querySelectorAll('div.markdown-content'));
                const last = els.length ? (els[els.length - 1].innerText || '') : '';
                return JSON.stringify({n: els.length, last: last});
                """
            )
            d = json.loads(res)
            return int(d.get("n", 0)), str(d.get("last", ""))
        except Exception:
            return 0, ""

    before_count, before_last = _history_snapshot()
    if before_count == 0:
        # 无历史元素：最多再等 8s 兼容历史接口加载慢的情况，确认无历史后直接放行（不再空耗 12 次循环）
        _stable_deadline = time.time() + 8
        while before_count == 0 and time.time() < _stable_deadline:
            time.sleep(1)
            before_count, before_last = _history_snapshot()
    if before_count > 0:
        # 有历史：最多 12s 等渲染稳定（避免把"还在渲染的旧回复"误判为新回复）
        for _ in range(12):
            time.sleep(1)
            n, last = _history_snapshot()
            if n and last == before_last and n >= before_count:
                break
            before_count, before_last = n, last
    print(f"[{_now()}] [AI对话] 发送前对话快照：{before_count} 条回复")

    # 始终主动发送一条消息（关键：确保产生今日真实对话，积分才会到账）
    _send_preset_message(page, input_selector)

    # 等待新回复出现：内容变化 或 数量增加，最多 50 秒
    # （同步改用 _history_snapshot 快照查询，避免无元素时空等）
    deadline = time.time() + 50
    got_reply = False
    latest_text = ""
    while time.time() < deadline:
        time.sleep(3)
        n, cur_last = _history_snapshot()
        if not n:
            continue
        cur_last = cur_last or ""
        if n > before_count and cur_last.strip():
            got_reply = True
            latest_text = cur_last
            break
        if cur_last.strip() and cur_last != before_last:
            got_reply = True
            latest_text = cur_last
            break

    if not got_reply:
        raise RuntimeError("发送消息后未收到 AI 回复，本次对话未完成")

    print(f"[{_now()}] [AI对话] === 收到新回复（共 {len(latest_text)} 字）===")
    preview = latest_text[:300] + ("..." if len(latest_text) > 300 else "")
    print(preview)
    print(f"[{_now()}] [AI对话] AI 对话完成，积分已领取")


# ==========================================
# 主流程控制
# ==========================================


def main() -> None:
    login_url = (
        "https://desk.ctyun.cn/cloudB/dy/iam/api/auth/iam/cas/login?"
        "service=https%3A%2F%2Feaichat.ctyun.cn%3A443%2Fchat%2F%23%2Faichat&consent=false"
    )
    chat_url = "https://eaichat.ctyun.cn/chat/#/aichat"

    my_username = os.getenv("APP_USER")
    my_password = os.getenv("APP_PASSWORD")

    if not my_username or not my_password:
        print("错误：未检测到 APP_USER 或 APP_PASSWORD 环境变量。")
        sys.exit(1)

    # 动态构造 Cookie 文件路径，包含手机号
    # 格式：/app/data/ctyun_cookies_xxx_.json
    if os.getenv("RUNNING_IN_DOCKER") == "true":
        cookie_file = f"/app/data/ctyun_cookies_{my_username}_.json"
    else:
        cookie_file = f"./ctyun_cookies_{my_username}_.json"

    browser_options = init_browser_options()
    page = ChromiumPage(addr_or_opts=browser_options)
    atexit.register(page.quit)
    attempt = 0
    max_retries = 3
    succeeded = False
    while attempt < max_retries:
        print(f"--- 对话尝试: {attempt + 1}/{max_retries} ---")
        try:
            is_logged_in = False

            # === 使用动态路径进行持久化验证 ===
            print(f"正在建立域名上下文环境，准备使用账号 {my_username} 的缓存...")
            page.get(chat_url)
            time.sleep(1)

            if attempt <= 0:
                if load_cookies(page, cookie_file):
                    print("正在验证 Cookie 是否有效...")
                    page.get(chat_url)
                    # 修复：Cookie 免密验证超时从 5s 放宽到 10s，避免 SPA 渲染慢被误判为"Cookie 失效"而无谓进入更高风险的账密登录流程。
                    if page.wait.ele_displayed(
                        "css:div.input-box.input-wrap", timeout=10
                    ):
                        print(f"[*] 账号 {my_username} 免密登录成功！")
                        is_logged_in = True
                    else:
                        print("[-] Cookie 已失效，准备进行账密登录...")

            # === 登录流程 (如果 Cookie 无效) ===
            if not is_logged_in:
                # 修复：重试时浏览器可能已带有效登录态（如上一轮账密登录成功但对话阶段失败），
                # 此时再走账密登录会被 CAS 重定向回聊天页，登录输入框永远不出现而白等超时
                # （实测 12:41 任务的尝试 3 正是死于该死角）。先探测聊天页输入框是否可直接使用。
                if page.wait.ele_displayed("css:div.input-box.input-wrap", timeout=6):
                    print("[*] 检测到浏览器已有登录态，跳过账密登录")
                    is_logged_in = True
                else:
                    is_success = execute_login_with_listener(
                        page, login_url, my_username, my_password
                    )
                    if is_success:
                        # 登录成功后保存到对应手机号的文件中
                        time.sleep(5)
                        save_cookies(page, cookie_file)
                        is_logged_in = True
                    else:
                        print("[!] 自动化登录未能成功执行。")
                        sys.exit(1)

            # === 执行互动获取积分 ===
            if is_logged_in:
                chat_and_earn_points(page)
                print("\n对话任务已完成")
                succeeded = True
            break

        except Exception as e:
            attempt += 1
            print(f"[!] 执行过程中发生异常: {e}")
            # 修复：重试间隔从 5s 缩短到 3s，为 3 次尝试留出更多时间预算（页面重置 1s 足够）。
            time.sleep(3)

    if not succeeded:
        # 关键修复：多次尝试仍未真正完成对话（未发送消息/未收到回复）时，
        # 必须以非 0 退出码结束，否则后端会误判"对话成功"并推送 ai_done，导致积分漏领却显示完成。
        print("[!] AI 对话任务多次尝试均未真正完成对话，按失败退出（退出码 1）")
        sys.exit(1)


# ==========================================
# OCR 模块封装
# ==========================================


class NumericOcrSolver:
    """使用单例模式封装的数字 OCR 识别器。"""

    _instance: Optional["NumericOcrSolver"] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "NumericOcrSolver":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_engine()
        return cls._instance

    def _init_engine(self) -> None:
        self.ocr = ddddocr.DdddOcr(show_ad=False)
        self.ocr.set_ranges(0)

    def solve(self, image_data: bytes) -> str:
        try:
            return self.ocr.classification(image_data)
        except Exception as e:
            return f"Error: {str(e)}"


def get_bytes_numeric_captcha(image_bytes: bytes) -> str:
    solver = NumericOcrSolver()
    return solver.solve(image_bytes)


# ==========================================
# 安全退出登录
# ==========================================


def safe_logout() -> None:
    """安全退出登录：删除本地 Cookie 缓存文件并清理会话状态。

    修复原因：后端 execute_task("logout") 以 --logout 参数调用本脚本，
    但原脚本不解析任何命令行参数，导致点"安全退出登录"按钮时
    反而执行了一遍完整的 AI 对话任务。
    """
    my_username = os.getenv("APP_USER")

    # 与 main() 保持一致的 Cookie 文件路径规则
    if os.getenv("RUNNING_IN_DOCKER") == "true":
        base_dir = "/app/data"
    else:
        base_dir = "."
    if not my_username:
        # 环境变量缺失时兜底：尝试清理 data 目录下所有 ctyun_cookies_*.json
        import glob

        patterns = [
            os.path.join(base_dir, "ctyun_cookies_*.json"),
            "./ctyun_cookies_*.json",
        ]
        removed = 0
        for pattern in patterns:
            for file_path in glob.glob(pattern):
                try:
                    os.remove(file_path)
                    print(f"[*] 已删除 Cookie 文件: {file_path}")
                    removed += 1
                except Exception as e:
                    print(f"[!] 删除 Cookie 文件失败 {file_path}: {e}")
        if removed:
            print("[*] 安全退出登录完成")
        else:
            print("[!] 未找到可清理的 Cookie 文件")
        return

    cookie_file = os.path.join(base_dir, f"ctyun_cookies_{my_username}_.json")
    if os.path.exists(cookie_file):
        try:
            os.remove(cookie_file)
            print(f"[*] 已删除 Cookie 文件: {cookie_file}")
            print("[*] 安全退出登录完成")
        except Exception as e:
            print(f"[!] 删除 Cookie 文件失败 {cookie_file}: {e}")
            sys.exit(1)
    else:
        print(f"[*] Cookie 文件不存在，无需清理: {cookie_file}")


if __name__ == "__main__":
    # 修复原因：支持 --logout 参数，与后端 execute_task("logout") 的调用方式对齐
    if "--logout" in sys.argv:
        safe_logout()
    else:
        main()
