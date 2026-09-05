"""
合并任务：单次浏览器会话内顺序完成「AI 对话 + 云电脑挂机」。

背景（效率优化）：
- 原调度分别触发 AI 对话（login_script.py）与挂机（pc_login.py），各自独立启动一个
  Chromium 进程并各自完成登录。Chromium 冷启动（headless + 加载）约 15~20s 且占用
  较多内存，两次独立启动既耗时又容易在资源紧张时登录失败。
- 两个产品（eaichat 网页 / pc.ctyun.cn 云电脑桌面）是两套独立认证体系，无法「一次登录
  两边都用」，但可以**复用同一个 Chromium 浏览器实例**：先登录 eaichat 做对话，再登录
  pc.ctyun.cn 进桌面挂机。这样把两次 Chromium 冷启动降为一次，省掉最贵的启动开销。

本脚本只新增编排逻辑，复用 login_script.py / pc_login.py 中已有的原子函数，不改动原脚本。
"""

import datetime
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# 复用现有脚本的原子函数（二者 main 均在 __main__ 守卫内，import 不会触发副作用）
import login_script
import pc_login

from DrissionPage import ChromiumPage

RUNNING_IN_DOCKER = os.getenv("RUNNING_IN_DOCKER") == "true"

AI_LOGIN_URL = (
    "https://desk.ctyun.cn/cloudB/dy/iam/api/auth/iam/cas/login?"
    "service=https%3A%2F%2Feaichat.ctyun.cn%3A443%2Fchat%2F%23%2Faichat&consent=false"
)


def _load_account() -> tuple:
    """账号优先取环境变量，缺失时 fallback 到 web_settings.json。"""
    username = os.getenv("APP_USER")
    password = os.getenv("APP_PASSWORD")
    if not username or not password:
        settings = pc_login.load_settings(RUNNING_IN_DOCKER)
        username = username or settings.get("username")
        password = password or settings.get("password")
    return username, password


def _init_browser() -> ChromiumPage:
    # 复用 pc_login 的浏览器参数（路径/无头/沙箱等与其完全一致），避免与单独挂机行为漂移
    options = pc_login.init_browser_options(RUNNING_IN_DOCKER)
    options.headless()  # 合并任务仅在服务器运行，强制无头
    page = ChromiumPage(addr_or_opts=options)
    return page


def _safe_quit(page: ChromiumPage) -> None:
    try:
        page.quit()
    except Exception:
        pass


def _do_ai_chat(page: ChromiumPage, username: str, password: str) -> bool:
    """复用 login_script 的登录 + 对话逻辑。失败返回 False 但不致命。"""
    print("[合并任务] === 阶段1：AI 对话 ===")
    try:
        is_logged_in = False
        # 尝试免密登录（复用 login_script 的 cookie 缓存）
        if RUNNING_IN_DOCKER:
            cookie_file = f"/app/data/ctyun_cookies_{username}_.json"
        else:
            cookie_file = f"./ctyun_cookies_{username}_.json"
        chat_url = "https://eaichat.ctyun.cn/chat/#/aichat"
        page.get(chat_url)
        time.sleep(1)
        if os.path.exists(cookie_file):
            login_script.load_cookies(page, cookie_file)
            page.get(chat_url)
            if page.wait.ele_displayed("css:div.input-box.input-wrap", timeout=5):
                print("[合并任务] AI 域免密登录成功")
                is_logged_in = True
            else:
                print("[合并任务] AI cookie 已失效，准备账密登录")
        if not is_logged_in:
            ok = login_script.execute_login_with_listener(
                page, AI_LOGIN_URL, username, password
            )
            if not ok:
                print("[合并任务] AI 账密登录失败，跳过对话阶段")
                return False
            time.sleep(5)
            login_script.save_cookies(page, cookie_file)
        login_script.chat_and_earn_points(page)
        print("[合并任务] === 阶段1 完成：AI 对话 ===")
        return True
    except Exception as e:
        print(f"[合并任务] AI 对话阶段异常（不阻断挂机）: {e}")
        return False


def _do_pc_hang(page: ChromiumPage, username: str, password: str) -> None:
    """复用 pc_login 的登录 + 进桌面 + 挂机逻辑。"""
    print("[合并任务] === 阶段2：云电脑挂机 ===")
    auth_data_file = pc_login.get_auth_data_file(username, RUNNING_IN_DOCKER)
    device_code = pc_login.get_device_code(username, RUNNING_IN_DOCKER)

    page.get(pc_login.LOGIN_URL)
    pc_login.inject_local_storage_session(page, device_code, auth_data_file)
    page.refresh()
    time.sleep(2)

    relogin_attempts = 0
    max_relogin_attempts = 3
    unknown_attempts = 0
    desktop_opened = False

    while True:
        page.get(pc_login.DESKTOP_URL)
        time.sleep(1)
        pc_login.wait_desktop_list_refresh_done(page, timeout=60)
        state = pc_login.get_desktop_state(page)
        print(f"\r[*] desktop-list 状态: {state}")

        if state in ("auth_expired", "unknown"):
            if relogin_attempts >= max_relogin_attempts:
                raise RuntimeError("重登次数已达上限")
            relogin_attempts += 1
            print(f"[合并任务] 未登录/过期，开始账密重登 ({relogin_attempts}/{max_relogin_attempts})")
            if not pc_login.execute_login(page, username, password):
                raise RuntimeError("重新登录失败")
            pc_login.save_auth_data(page, auth_data_file)
            continue

        if state == "no_desktop":
            print("[合并任务] 当前账号无云电脑资源，任务结束。")
            return
        if state == "only_phone":
            print("[合并任务] 当前账号仅有云手机资源，任务结束。")
            return
        if state == "has_pc_button":
            print("[合并任务] 检测到「进入AI云电脑」按钮，准备进入。")
            if not pc_login.click_enter_ai_pc(page):
                continue
            if not pc_login.wait_desktop_opened(page, timeout=240):
                continue
            desktop_opened = True
            break
        if state == "desktop_entered_auto":
            print("[合并任务] 已自动进入云电脑页面。")
            desktop_opened = True
            break

        unknown_attempts += 1
        if unknown_attempts >= 3:
            raise RuntimeError("无法识别 desktop-list 页面状态")
        time.sleep(2)

    if not desktop_opened:
        raise RuntimeError("未进入云电脑页面")

    auth_data = pc_login.read_auth_data(page)
    mobile = auth_data.get("mobilephone") if auth_data else None
    print(f"[合并任务] 登录成功账号: {mobile}") if mobile else print("[-] 登录成功，但未能读取 mobilephone")

    pc_login.wait_for_points_with_points(
        page,
        pc_login.HANG_SECONDS,
        running_in_docker=RUNNING_IN_DOCKER,
        config_redeem_only=False,
    )


def main() -> None:
    username, password = _load_account()
    if not username or not password:
        print("[!] 缺少账号或密码：请通过 Web 面板「账号设置」保存，或设置环境变量 APP_USER/APP_PASSWORD。")
        sys.exit(1)

    # 确保挂机时长取自 web_settings（HANG_MINUTES 由 execute_task 注入环境变量）
    page = _init_browser()
    print("[合并任务] 单浏览器会话启动，将依次执行 AI 对话 → 云电脑挂机")

    ai_ok = _do_ai_chat(page, username, password)
    # AI 失败不阻断挂机
    try:
        _do_pc_hang(page, username, password)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[合并任务] 挂机阶段异常: {e}")
        try:
            pc_login._write_hang_status(
                running=False,
                status="挂机失败",
                message=f"合并任务挂机异常：{str(e)[:160]}",
                updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception:
            pass
        sys.exit(1)
    finally:
        _safe_quit(page)
    print("[合并任务] 全部阶段完成（AI对话%s）" % ("成功" if ai_ok else "跳过"))


if __name__ == "__main__":
    main()
