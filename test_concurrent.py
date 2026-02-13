#!/usr/bin/env python3
"""
并发请求测试脚本（持续1分钟）

持续发送并发请求，用于监控后台浏览器状态
"""
import asyncio
import time
import httpx

# 测试 URL 列表
TEST_URLS = [
    "https://www.python.org",
    "https://www.github.com",
    "https://www.wikipedia.org",
    "https://www.npmjs.com",
    "https://www.reddit.com",
]

# 测试配置
TEST_DURATION = 60  # 持续1分钟
CONCURRENT_REQUESTS = 3  # 每轮并发请求数


async def fetch_url(task_id: int, url: str, client: httpx.AsyncClient) -> dict:
    """抓取单个 URL"""
    start_time = time.time()

    try:
        response = await client.post(
            "http://localhost:2025/fetch_url",
            json={"url": url, "screenshot": True, "wait_time": 200}
        )
        result = response.json()
        elapsed = time.time() - start_time

        if result.get("success"):
            print(f"  [OK] {elapsed:.1f}s | {result.get('title', '')[:25]}")
        else:
            print(f"  [FAIL] {result.get('error', '未知错误')[:30]}")
        return result
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"  [ERR] {str(e)[:30]} | {elapsed:.1f}s")
        return {"success": False, "error": str(e)}


async def main():
    """主函数：持续发送并发请求"""
    end_time = time.time() + TEST_DURATION

    print("=" * 60)
    print(f"持续并发测试 | 时长: {TEST_DURATION}秒 | 并发: {CONCURRENT_REQUESTS}")
    print("=" * 60)
    print()

    success_count = 0
    total_count = 0
    round_num = 0

    async with httpx.AsyncClient(timeout=120.0) as client:
        while time.time() < end_time:
            round_num += 1
            round_start = time.time()
            remaining = int(end_time - time.time())

            print(f"[轮次 {round_num}] 剩余: {remaining}秒", end=" | ")

            # 并发执行请求
            tasks = [
                fetch_url(i, TEST_URLS[i % len(TEST_URLS)], client)
                for i in range(CONCURRENT_REQUESTS)
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 统计
            for r in results:
                if isinstance(r, dict) and r.get("success"):
                    success_count += 1
                total_count += 1

            round_time = time.time() - round_start
            print(f"本轮耗时: {round_time:.1f}s")

    print()
    print("=" * 60)
    print(f"测试完成！总请求: {total_count} | 成功: {success_count} | 成功率: {success_count/total_count*100:.1f}%")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
