#!/bin/bash
# Docker 镜像构建和推送脚本

set -e

# 配置
IMAGE_NAME="izerui/browser-fetch"
VERSION=$(uv run python -c "import tomllib; config = tomllib.load(open('pyproject.toml', 'rb')); print(config['project']['version'])")

# 自定义基础镜像（如果设置）
CUSTOM_BASE_IMAGE="${CUSTOM_BASE_IMAGE:-serv999.com/hub/python:3.12-slim}"
# 构建平台（默认：当前平台；多架构：linux/amd64,linux/arm64）
BUILD_PLATFORMS="${BUILD_PLATFORMS:-}"  # 空表示自动检测当前平台

# 如果未指定平台，自动检测
if [ -z "$BUILD_PLATFORMS" ]; then
    HOST_ARCH=$(uname -m)
    case $HOST_ARCH in
        x86_64) BUILD_PLATFORMS="linux/amd64" ;;
        aarch64|arm64) BUILD_PLATFORMS="linux/arm64" ;;
        *) echo "不支持的架构: $HOST_ARCH"; exit 1 ;;
    esac
fi

# 提取架构简称
ARCH_TAG="${BUILD_PLATFORMS##*/}"

echo "========================================="
echo "构建 Docker 镜像"
echo "镜像名: ${IMAGE_NAME}"
echo "版本: ${VERSION}"
echo "平台: ${BUILD_PLATFORMS}"
if [ -n "$CUSTOM_BASE_IMAGE" ]; then
    echo "基础镜像: ${CUSTOM_BASE_IMAGE}"
fi
echo "========================================="

# 构建镜像（推送到特定架构标签）
echo ""
echo "📦 正在构建镜像..."

if [ -n "$CUSTOM_BASE_IMAGE" ]; then
    # 使用自定义基础镜像构建（动态替换，不修改原文件）
    sed "s|FROM python:3.12-slim|FROM ${CUSTOM_BASE_IMAGE}|g" Dockerfile | \
      docker buildx build --platform "${BUILD_PLATFORMS}" -f - \
      --tag "${IMAGE_NAME}:${VERSION}-${ARCH_TAG}" \
      --push \
      .
else
    # 使用原始 Dockerfile 构建
    docker buildx build --platform "${BUILD_PLATFORMS}" -f Dockerfile \
      --tag "${IMAGE_NAME}:${VERSION}-${ARCH_TAG}" \
      --push \
      .
fi

# 尝试合并多架构到 latest（如果两个架构都存在）
echo ""
echo "🔗 尝试合并多架构标签..."
docker buildx imagetools create \
  --tag "${IMAGE_NAME}:latest" \
  --tag "${IMAGE_NAME}:${VERSION}" \
  "${IMAGE_NAME}:${VERSION}-amd64" \
  "${IMAGE_NAME}:${VERSION}-arm64" 2>/dev/null && \
  echo "✅ 多架构标签创建成功!" || \
  echo "⚠️  部分架构不存在，已推送: ${IMAGE_NAME}:${VERSION}-${ARCH_TAG}"

echo ""
echo "========================================="
echo "已推送镜像:"
echo "  ${IMAGE_NAME}:${VERSION}-${ARCH_TAG}"
echo "========================================="
echo ""
echo "💡 提示: CI 会自动构建 amd64，本地构建 arm64"
echo "   两个架构都推送后，会自动合并成 :latest 多架构标签"
