FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 复制项目文件（pyproject.toml 需要 README.md）
COPY pyproject.toml uv.lock README.md ./

# 使用 uv 安装依赖
RUN uv sync --frozen --no-dev

# 安装 Playwright 浏览器
RUN uv run playwright install chromium
RUN uv run playwright install-deps chromium

# 复制应用代码
COPY app.py main.py ./

# 暴露端口
EXPOSE 2025

# 使用 uv run 启动应用
CMD ["uv", "run", "main.py"]
