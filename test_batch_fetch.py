#!/usr/bin/env python3
"""
批量抓取测试脚本

抓取多个网页并保存到 dist 目录
"""
import asyncio
import os
from pathlib import Path

import httpx


# 测试 URL 列表
TEST_URLS = [
    "https://www.python.org",
    "https://www.github.com",
    "https://www.wikipedia.org",
]


async def fetch_url(url: str, client: httpx.AsyncClient) -> dict:
    """抓取单个 URL"""
    response = await client.post(
        "http://localhost:2025/fetch_url",
        json={"url": url, "screenshot": True}
    )
    return response.json()


def save_result(result: dict, dist_dir: Path):
    """保存抓取结果"""
    if not result.get("success"):
        print(f"  ❌ 失败: {result.get('error', '未知错误')}")
        return

    # 生成文件名（使用 URL 的 path 部分）
    url = result["fetched_url"]
    filename = url.replace("https://", "").replace("http://", "").replace("/", "_")[:50]
    filename = filename.rstrip("_")

    # 保存 Markdown
    if result.get("markdown_content"):
        md_path = dist_dir / f"{filename}.md"
        md_path.write_text(result["markdown_content"], encoding="utf-8")
        print(f"  ✅ Markdown: {md_path}")

    # 保存截图
    if result.get("screenshot_base64"):
        import base64
        img_path = dist_dir / f"{filename}.png"
        img_bytes = base64.b64decode(result["screenshot_base64"])
        img_path.write_bytes(img_bytes)
        print(f"  ✅ 截图: {img_path}")

    print(f"  ⏱ 耗时: {result.get('duration_seconds', 0):.2f}秒")


async def main():
    """主函数"""
    # 创建 dist 目录
    dist_dir = Path("dist")
    dist_dir.mkdir(exist_ok=True)

    print(f"开始批量抓取，共 {len(TEST_URLS)} 个 URL")
    print(f"保存目录: {dist_dir.absolute()}")
    print("-" * 50)

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i, url in enumerate(TEST_URLS, 1):
            print(f"\n[{i}/{len(TEST_URLS)}] 抓取: {url}")
            try:
                result = await fetch_url(url, client)
                save_result(result, dist_dir)
            except Exception as e:
                print(f"  ❌ 异常: {e}")

    print("\n" + "-" * 50)
    print("抓取完成！")


if __name__ == "__main__":
    asyncio.run(main())
