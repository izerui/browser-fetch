#!/usr/bin/env python3
"""
独立浏览器抓取服务主入口

启动方式：
    python main.py

或使用 uvicorn：
    uvicorn main:app --host 0.0.0.0 --port 2025
"""
import asyncio
import logging
import os

import uvicorn
from fastapi import FastAPI

from app import app

# 配置日志
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)


def main():
    """主函数"""
    port = int(os.getenv('BROWSER_SERVICE_PORT', '2025'))
    host = os.getenv('BROWSER_SERVICE_HOST', '0.0.0.0')

    logger.info("=" * 60)
    logger.info("独立浏览器抓取服务")
    logger.info("=" * 60)
    logger.info(f"Host: {host}")
    logger.info(f"Port: {port}")
    logger.info(f"Pool Size: {os.getenv('BROWSER_POOL_SIZE', 5)}")
    logger.info(f"Headless: {os.getenv('HEADLESS', 'true')}")
    logger.info("=" * 60)
    logger.info("")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )


if __name__ == "__main__":
    main()
