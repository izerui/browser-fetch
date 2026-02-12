#!/bin/bash

# 独立浏览器服务启动脚本

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  独立浏览器抓取服务${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 检查虚拟环境
if [ -d venv ]; then
    echo -e "${GREEN}✓ 激活虚拟环境${NC}"
    source venv/bin/activate
else
    echo -e "${YELLOW}创建虚拟环境...${NC}"
    python -m venv venv
    source venv/bin/activate
fi

# 安装依赖
echo -e "${GREEN}✓ 安装依赖...${NC}"
pip install -r requirements.txt

# 安装 Playwright 浏览器
echo -e "${GREEN}✓ 安装 Playwright 浏览器...${NC}"
playwright install chromium
playwright install-deps chromium

# 加载环境变量
if [ -f .env ]; then
    export $(cat .env | xargs)
    echo -e "${GREEN}✓ 已加载 .env 配置${NC}"
else
    echo -e "${YELLOW}⚠ 未找到 .env 文件，使用默认配置${NC}"
fi

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}启动浏览器服务...${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${YELLOW}配置信息:${NC}"
echo -e "  端口: ${GREEN}${BROWSER_SERVICE_PORT:-2025}${NC}"
echo -e "  主机: ${GREEN}${BROWSER_SERVICE_HOST:-0.0.0.0}${NC}"
echo -e "  实例池: ${GREEN}${BROWSER_POOL_SIZE:-5}${NC}"
echo -e "  并发数: ${GREEN}${MAX_CONCURRENT_PAGES:-10}${NC}"
echo ""
echo -e "${YELLOW}服务地址: http://localhost:${BROWSER_SERVICE_PORT:-2025}${NC}"
echo -e "${YELLOW}按 Ctrl+C 停止服务${NC}"
echo ""
echo -e "${BLUE}========================================${NC}"
echo ""

# 启动服务
python main.py
