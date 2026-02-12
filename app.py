"""
独立浏览器抓取服务

一个支持高并发的网页抓取服务，使用 Playwright 实现。
每个请求使用独立的浏览器实例，确保真正的并发处理。
"""
import asyncio
import logging
import os
import random
import re
import time
import uuid
import psutil
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright, Browser, async_playwright
from playwright_stealth import Stealth
from markdownify import markdownify

# 配置日志
logger = logging.getLogger(__name__)

# ==================== 配置 ====================

class Config:
    """服务配置"""

    # 服务配置
    PORT = int(os.getenv('BROWSER_SERVICE_PORT', '2025'))
    HOST = os.getenv('BROWSER_SERVICE_HOST', '0.0.0.0')

    # 浏览器配置
    POOL_SIZE = int(os.getenv('BROWSER_POOL_SIZE', '5'))
    MAX_CONCURRENT_PAGES = int(os.getenv('MAX_CONCURRENT_PAGES', '10'))
    HEADLESS = os.getenv('HEADLESS', 'true').lower() == 'true'
    MAX_SCREENSHOT_SIZE = int(os.getenv('MAX_SCREENSHOT_SIZE', '5242880'))

    # User-Agent 池
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]

    # 浏览器启动参数
    BROWSER_ARGS = [
        '--no-sandbox',
        '--disable-dev-shm-usage',
        '--disable-blink-features=AutomationControlled',
        '--disable-gpu',
        '--disable-extensions',
        '--disable-background-networking',
        '--disable-default-apps',
        '--disable-sync',
        '--no-first-run',
        '--disable-setuid-sandbox',
    ]

    @classmethod
    def get_random_user_agent(cls) -> str:
        """获取随机 User-Agent"""
        return random.choice(cls.USER_AGENTS)


# ==================== 请求/响应模型 ====================

class FetchRequest(BaseModel):
    """抓取请求"""
    url: str
    wait_time: int = 1000  # 等待时间（毫秒）
    wait_for_selector: str = ""  # 等待选择器
    screenshot: bool = True  # 是否截图


class FetchResponse(BaseModel):
    """抓取响应"""
    success: bool
    url: str
    title: str = ""
    content: str = ""
    screenshot: str = ""  # base64 编码
    content_length: int = 0
    fetched_at: str = ""
    error: str = ""
    fetch_time: float = 0  # 抓取耗时（秒）


# ==================== 内存监控工具 ====================

def get_memory_info() -> dict[str, Any]:
    """获取当前进程内存信息"""
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()

    # 获取所有子进程（Chromium 进程）
    children = process.children(recursive=True)
    children_mem = 0
    chromium_count = 0

    for child in children:
        try:
            child_mem = child.memory_info().rss
            children_mem += child_mem
            # 检查是否是 Chromium 进程
            if 'chrom' in child.name().lower() or 'chrome' in child.name().lower():
                chromium_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return {
        "process_rss_mb": round(mem_info.rss / 1024 / 1024, 2),
        "process_vms_mb": round(mem_info.vms / 1024 / 1024, 2),
        "children_rss_mb": round(children_mem / 1024 / 1024, 2),
        "total_rss_mb": round((mem_info.rss + children_mem) / 1024 / 1024, 2),
        "chromium_processes": chromium_count,
        "total_children": len(children),
    }


def format_bytes(bytes_value: int) -> str:
    """格式化字节数为可读格式"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} TB"


# ==================== 浏览器实例池 ====================

class BrowserPool:
    """浏览器实例池"""

    def __init__(self, pool_size: int):
        self.pool_size = pool_size
        self.browsers: list[Browser] = []
        self.playwright = None
        self.semaphore = asyncio.Semaphore(pool_size)
        self._initialized = False
        self._request_count = 0  # 请求计数器
        self._start_time = time.time()  # 启动时间

    async def initialize(self):
        """初始化浏览器池"""
        if self._initialized:
            return

        logger.info(f"初始化浏览器实例池，大小: {self.pool_size}")

        try:
            self.playwright = await async_playwright().start()

            # 启动多个浏览器实例
            for i in range(self.pool_size):
                browser = await self.playwright.chromium.launch(
                    headless=Config.HEADLESS,
                    args=Config.BROWSER_ARGS
                )
                self.browsers.append(browser)
                logger.info(f"浏览器实例 {i}: 已启动")

            self._initialized = True
            logger.info(f"浏览器实例池初始化完成，实例数: {len(self.browsers)}")

        except Exception as e:
            logger.error(f"初始化浏览器池失败: {e}")
            raise

    async def shutdown(self):
        """关闭所有浏览器实例"""
        logger.info("关闭浏览器实例池...")

        for i, browser in enumerate(self.browsers):
            try:
                await browser.close()
                logger.info(f"浏览器实例 {i}: 已关闭")
            except Exception as e:
                logger.warning(f"关闭浏览器实例 {i} 时出错: {e}")

        self.browsers.clear()

        if self.playwright:
            try:
                await self.playwright.stop()
                logger.info("Playwright 已停止")
            except Exception as e:
                logger.warning(f"停止 Playwright 时出错: {e}")

        self._initialized = False

    async def fetch_page(self, request: FetchRequest) -> FetchResponse:
        """从池中获取一个浏览器实例来抓取页面"""
        if not self._initialized:
            await self.initialize()

        self._request_count += 1
        start_time = time.time()

        async with self.semaphore:
            # 获取一个可用的浏览器实例（轮询）
            browser = self.browsers[id(asyncio.current_task()) % len(self.browsers)]

            context = None
            page = None

            try:
                # 创建浏览器上下文
                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=Config.get_random_user_agent(),
                )

                page = await context.new_page()

                # 应用反爬虫
                await self._apply_stealth(page)

                # 设置请求头
                await page.set_extra_http_headers(self._get_headers())

                # 导航到页面
                await page.goto(request.url, wait_until="domcontentloaded", timeout=30000)

                # 等待指定时间
                if request.wait_time > 0:
                    await page.wait_for_timeout(request.wait_time)

                # 等待选择器
                if request.wait_for_selector:
                    await page.wait_for_selector(request.wait_for_selector, timeout=10000)

                # 获取内容
                title = await page.title()
                html_content = await page.content()

                # 转换为 Markdown
                markdown_content = markdownify(html_content)
                cleaned_content = self._clean_markdown(markdown_content)

                # 截图
                screenshot_b64 = ""
                if request.screenshot:
                    screenshot_bytes = await page.screenshot(full_page=False)
                    if len(screenshot_bytes) <= Config.MAX_SCREENSHOT_SIZE:
                        import base64
                        screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

                fetch_time = time.time() - start_time

                return FetchResponse(
                    success=True,
                    url=request.url,
                    title=title or "无标题",
                    content=cleaned_content,
                    screenshot=screenshot_b64,
                    content_length=len(cleaned_content),
                    fetched_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                    fetch_time=fetch_time
                )

            except Exception as e:
                logger.error(f"抓取失败 {request.url}: {e}")
                fetch_time = time.time() - start_time
                return FetchResponse(
                    success=False,
                    url=request.url,
                    error=str(e),
                    fetch_time=fetch_time
                )

            finally:
                if page:
                    try:
                        await page.close()
                    except:
                        pass
                if context:
                    try:
                        await context.close()
                    except:
                        pass

    async def _apply_stealth(self, page):
        """应用反爬虫脚本"""
        stealth = Stealth()
        await stealth.apply_stealth_async(page)

    def _get_headers(self) -> dict[str, str]:
        """获取请求头"""
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "DNT": "1",
        }

    def _clean_markdown(self, content: str) -> str:
        """清理 Markdown"""
        content = re.sub(r'\n{3,}', '\n\n', content)
        content = re.sub(r'^\s+$/gm', '', content)
        return content.strip()


# ==================== 全局实例池 ====================

_browser_pool: BrowserPool | None = None


def get_browser_pool() -> BrowserPool:
    """获取浏览器实例池（单例）"""
    global _browser_pool
    if _browser_pool is None:
        pool_size = Config.POOL_SIZE
        _browser_pool = BrowserPool(pool_size)
    return _browser_pool


# ==================== FastAPI 应用 ====================

app = FastAPI(
    title="Browser Fetch Service",
    description="独立的网页抓取服务，支持高并发",
    version="1.0.0"
)


@app.get("/")
async def root():
    """根路径"""
    return {
        "service": "Browser Fetch Service",
        "version": "1.0.0",
        "status": "running",
        "pool_size": Config.POOL_SIZE,
    }


@app.get("/health")
async def health():
    """健康检查"""
    pool = get_browser_pool()
    mem_info = get_memory_info()

    # 计算运行时间
    uptime = time.time() - pool._start_time if pool._start_time else 0

    return {
        "status": "healthy",
        "browser_started": pool._initialized,
        "pool_size": Config.POOL_SIZE,
        "max_concurrent": Config.MAX_CONCURRENT_PAGES,
        "request_count": pool._request_count,
        "uptime_seconds": round(uptime, 2),
        "memory": mem_info,
    }


@app.get("/stats")
async def stats():
    """详细统计信息"""
    pool = get_browser_pool()
    mem_info = get_memory_info()

    # 计算运行时间
    uptime = time.time() - pool._start_time if pool._start_time else 0

    # 系统信息
    sys_mem = psutil.virtual_memory()
    sys_cpu = psutil.cpu_percent(interval=0.1)

    return {
        "service": {
            "name": "Browser Fetch Service",
            "version": "1.0.0",
            "uptime_seconds": round(uptime, 2),
            "request_count": pool._request_count,
            "requests_per_second": round(pool._request_count / uptime, 2) if uptime > 0 else 0,
        },
        "browser_pool": {
            "pool_size": Config.POOL_SIZE,
            "max_concurrent": Config.MAX_CONCURRENT_PAGES,
            "initialized": pool._initialized,
            "active_browsers": len(pool.browsers),
        },
        "memory": {
            "process_mb": mem_info["process_rss_mb"],
            "children_mb": mem_info["children_rss_mb"],
            "total_mb": mem_info["total_rss_mb"],
            "chromium_processes": mem_info["chromium_processes"],
            "total_children": mem_info["total_children"],
        },
        "system": {
            "cpu_percent": sys_cpu,
            "memory_total_gb": round(sys_mem.total / 1024 / 1024 / 1024, 2),
            "memory_available_gb": round(sys_mem.available / 1024 / 1024 / 1024, 2),
            "memory_percent": sys_mem.percent,
        },
    }


@app.get("/metrics")
async def metrics():
    """Prometheus 风格的监控指标"""
    pool = get_browser_pool()
    mem_info = get_memory_info()

    uptime = time.time() - pool._start_time if pool._start_time else 0

    metrics_text = f"""# HELP browser_service_requests_total Total number of requests
# TYPE browser_service_requests_total counter
browser_service_requests_total {pool._request_count}

# HELP browser_service_uptime_seconds Service uptime in seconds
# TYPE browser_service_uptime_seconds gauge
browser_service_uptime_seconds {uptime:.2f}

# HELP browser_service_pool_size Browser pool size
# TYPE browser_service_pool_size gauge
browser_service_pool_size {Config.POOL_SIZE}

# HELP browser_service_memory_bytes Total memory usage in bytes
# TYPE browser_service_memory_bytes gauge
browser_service_memory_bytes {mem_info["total_rss_mb"] * 1024 * 1024}

# HELP browser_service_chromium_processes Number of Chromium processes
# TYPE browser_service_chromium_processes gauge
browser_service_chromium_processes {mem_info["chromium_processes"]}

# HELP browser_service_max_concurrent Maximum concurrent pages per browser
# TYPE browser_service_max_concurrent gauge
browser_service_max_concurrent {Config.MAX_CONCURRENT_PAGES}
"""

    from fastapi.responses import Response
    return Response(
        content=metrics_text,
        media_type="text/plain",
    )


@app.post("/fetch")
async def fetch_page(request: FetchRequest):
    """
    抓取网页内容

    Args:
        request: 抓取请求

    Returns:
        抓取结果
    """
    pool = get_browser_pool()
    result = await pool.fetch_page(request)
    return result


@app.post("/fetch_with_files")
async def fetch_with_files(
    request: FetchRequest,
    root_dir: str = "/tmp/browser_fetch"
):
    """
    抓取网页并保存到文件

    Args:
        request: 抓取请求
        root_dir: 根目录

    Returns:
        包含文件路径的抓取结果
    """
    import base64

    pool = get_browser_pool()
    result = await pool.fetch_page(request)

    if not result.success:
        return result

    # 保存文件（在后台任务中）
    def save_files():
        import random

        os.makedirs(root_dir, exist_ok=True)

        # 保存 Markdown
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', result.title)
        safe_title = re.sub(r'\s+', '_', safe_title)
        random_suffix = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=5))

        markdown_filename = f"{safe_title}_{random_suffix}.md"
        markdown_path = os.path.join(root_dir, markdown_filename)

        with open(markdown_path, 'w', encoding='utf-8') as f:
            f.write(result.content)

        # 保存截图
        screenshot_filename = ""
        if result.screenshot:
            screenshot_uuid = str(uuid.uuid4())
            screenshot_filename = f"{screenshot_uuid}.png"
            screenshot_path = os.path.join(root_dir, screenshot_filename)

            screenshot_bytes = base64.b64decode(result.screenshot)
            with open(screenshot_path, 'wb') as f:
                f.write(screenshot_bytes)

        return markdown_filename, screenshot_filename

    # 使用 asyncio.to_thread 避免阻塞
    markdown_filename, screenshot_filename = await asyncio.to_thread(save_files)

    return {
        "success": True,
        "url": result.url,
        "title": result.title,
        "file_path": markdown_path,
        "screenshot_path": os.path.join(root_dir, screenshot_filename) if screenshot_filename else "",
        "content_length": result.content_length,
        "fetched_at": result.fetched_at,
        "fetch_time": result.fetch_time
    }


# ==================== 生命周期管理 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化浏览器池
    pool = get_browser_pool()
    await pool.initialize()
    logger.info("浏览器服务已就绪")

    yield

    # 关闭时清理
    await pool.shutdown()
    logger.info("浏览器服务已关闭")


app.router.lifespan_context = lifespan

# 导出 app 供 uvicorn 使用
__all__ = ["app"]
