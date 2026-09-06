"""
桌面版公共路径模块
- 统一管理数据目录与运行时目录，替代 Docker 版的 /app/data、/tmp 硬编码路径
- 优先级：环境变量 CTYUN_DATA_DIR > exe/脚本所在目录下的 data/
- Windows 打包后（PyInstaller）以 _MEIPASS 旁边的真实 exe 目录为基准
"""


import os
import sys
import tempfile


def _base_dir() -> str:
    """应用根目录：打包后为 exe 所在目录；源码运行为本文件所在目录的上一级。"""
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        return os.path.dirname(os.path.abspath(sys.executable))
    # 源码运行：desktop/paths.py -> desktop/
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _base_dir()

# 数据目录（账号、Cookie、日志、配置，用户唯一需要备份的目录）
DATA_DIR = os.environ.get("CTYUN_DATA_DIR") or os.path.join(BASE_DIR, "data")

# 应用运行目录（脚本、CtYun 核心等，随软件分发）
APP_DIR = os.path.join(BASE_DIR, "app")

# CtYun.dll 保活核心所在目录
CTYUN_CORE_DIR = os.path.join(BASE_DIR, "ctyun-core")

# Web 前端目录（templates / static）
WEB_DIR = os.path.join(BASE_DIR, "web")

# Web 面板服务端目录（web_server/app.py）——【修复说明】web_server 与本模块同级、
# 不在 app/ 脚本目录下，此前 launcher 误用 app_path("web_server", ...) 拼到
# desktop\app\web_server\ 导致面板启动即报 FileNotFoundError；这里提供专用常量统一管理
WEB_SERVER_DIR = os.path.join(BASE_DIR, "web_server")

# 便携 .NET 运行时目录（随软件分发，免用户安装）
DOTNET_DIR = os.path.join(BASE_DIR, "dotnet")

# 临时文件目录（对应 Docker 版的 /tmp）
TMP_DIR = os.path.join(DATA_DIR, ".tmp")

for _d in (DATA_DIR, TMP_DIR):
    os.makedirs(_d, exist_ok=True)


def data_path(*parts) -> str:
    """拼接数据目录下的路径。"""
    return os.path.join(DATA_DIR, *parts)


def tmp_path(*parts) -> str:
    """拼接应用临时目录下的路径（替代 /tmp）。"""
    return os.path.join(TMP_DIR, *parts)


def app_path(*parts) -> str:
    """拼接应用目录（脚本等）下的路径。"""
    return os.path.join(APP_DIR, *parts)
