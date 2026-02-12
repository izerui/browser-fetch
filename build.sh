#!/bin/bash
# Docker 镜像构建和推送脚本

set -e

# 配置
IMAGE_NAME="izerui/browser-fetch"
VERSION=$(uv run python -c "import tomllib; config = tomllib.load(open('pyproject.toml', 'rb')); print(config['project']['version'])")
LOCAL_NAME="browser-fetch:${VERSION}"

# 自定义基础镜像（如果设置）
CUSTOM_BASE_IMAGE="${CUSTOM_BASE_IMAGE:-serv999.com/hub/python:3.12-slim}"

echo "========================================="
echo "构建 Docker 镜像"
echo "镜像名: ${IMAGE_NAME}"
echo "版本: ${VERSION}"
if [ -n "$CUSTOM_BASE_IMAGE" ]; then
    echo "基础镜像: ${CUSTOM_BASE_IMAGE}"
fi
echo "========================================="

# 构建镜像
echo ""
echo "📦 正在构建镜像..."

if [ -n "$CUSTOM_BASE_IMAGE" ]; then
    # 使用自定义基础镜像构建（动态替换，不修改原文件）
    sed "s|FROM python:3.12-slim|FROM ${CUSTOM_BASE_IMAGE}|g" Dockerfile | docker build -f - -t "${LOCAL_NAME}" .
else
    # 使用原始 Dockerfile 构建
    docker build -f Dockerfile -t "${LOCAL_NAME}" .
fi

# 打标签
echo ""
echo "🏷️  打标签..."
docker tag "${LOCAL_NAME}" "${IMAGE_NAME}:latest"
docker tag "${LOCAL_NAME}" "${IMAGE_NAME}:${VERSION}"

echo ""
echo "✅ 构建完成!"

# 推送镜像
read -p "是否推送到 Docker Hub? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "📤 正在推送镜像..."
    docker push "${IMAGE_NAME}:latest"
    docker push "${IMAGE_NAME}:${VERSION}"
    echo ""
    echo "✅ 推送完成!"
    echo ""
    echo "拉取命令:"
    echo "  docker pull ${IMAGE_NAME}:latest"
    echo "  docker pull ${IMAGE_NAME}:${VERSION}"
fi

echo ""
echo "========================================="
echo "本地镜像:"
docker images | grep "browser-fetch"
echo "========================================="
