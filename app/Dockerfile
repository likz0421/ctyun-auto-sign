# 天翼云电脑自动化 + Web 管理面板（开源发布版镜像）
# 基础镜像：su3817807/ctyun（含 CtYun.dll 保活程序与 .NET 8 运行时，公共镜像无个人数据，已核验）
# 优化：单层安装 + --no-install-recommends + 彻底清理缓存

FROM su3817807/ctyun:latest

USER root

ENV TZ=Asia/Shanghai \
    RUNNING_IN_DOCKER=true \
    WEB_PORT=8080 \
    DRISSIONPAGE_CHROMIUM_PATH=/usr/bin/chromium

# 时区 + apt + pip + cron + 清理（全部放在一层，避免中间层占空间）
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        fonts-wqy-zenhei \
        cron \
        libgl1 \
        libglib2.0-0 \
        dos2unix \
        curl \
        chromium \
    && pip3 install --no-cache-dir \
        DrissionPage==4.0.4.19 \
        ddddocr \
        flask \
        --break-system-packages \
    # ---- 统一 chromium 路径，强制 DrissionPage 复用系统 chromium，禁止其自行下载 ----
    && CHROME_BIN=$(command -v chromium || command -v chromium-browser || command -v google-chrome || echo /usr/bin/chromium) \
    && ln -sf "$CHROME_BIN" /usr/bin/chromium \
    && echo "export DRISSIONPAGE_CHROMIUM_PATH=/usr/bin/chromium" >> /etc/profile.d/ctyun.sh \
    && echo '{"chromium_path": "/usr/bin/chromium", "auto_install": false}' > /root/.drissionpage_config.json \
    # ---- 清理缓存 ----
    && apt-get purge -y --auto-remove python3-pip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /root/.cache \
    && rm -rf /tmp/* \
    && find /usr/share/doc -type f -delete 2>/dev/null || true \
    && find /usr/share/man -type f -delete 2>/dev/null || true \
    && find /usr/share/locale -mindepth 1 -maxdepth 1 ! -name 'zh*' -type d -exec rm -rf {} + 2>/dev/null || true

# 复制应用文件（一层）
# 注意：combined_task.py 必须复制进镜像，否则调度线程触发「合并任务」时
# 会因脚本不存在而失败（AI 对话与挂机时间相同时走合并路径）。
COPY app/login_script.py /app/login_script.py
COPY app/pc_login.py /app/pc_login.py
COPY app/combined_task.py /app/combined_task.py
COPY app/entrypoint.sh /app/entrypoint.sh
COPY web/ /app/web/
COPY web_server/ /app/web_server/

# 注意：定时任务（AI 对话 / 云电脑挂机）已由 Web 后端 Python 调度线程驱动
# （见 web_server/app.py 的 _scheduler_loop），不再依赖系统 cron，
# 否则会与应用内调度"双触发"且系统 cron 不读 web_settings.json 导致前端改时间无效。
# 因此这里**不再安装写死默认时间的系统 cron**，仅处理脚本权限。
RUN dos2unix /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh \
    && chmod -R 755 /app/web /app/web_server

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
