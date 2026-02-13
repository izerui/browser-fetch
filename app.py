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
import psutil
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urljoin
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich.text import Text
from rich import box
from rich.live import Live

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright, Browser, async_playwright
from playwright_stealth import Stealth
from markdownify import markdownify

# Rich 控制台（用于美化输出）
rich_console = Console()

# 配置日志
logger = logging.getLogger(__name__)

# ==================== 配置 ====================

class Config:
    """服务配置"""

    # 服务配置
    PORT = int(os.getenv('BROWSER_SERVICE_PORT', '2025'))
    HOST = os.getenv('BROWSER_SERVICE_HOST', '0.0.0.0')

    # 浏览器配置
    POOL_SIZE = int(os.getenv('BROWSER_POOL_SIZE', '3'))  # 减少：5->3，每个浏览器约 200-400MB
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
        # 内存优化参数
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-breakpad',
        '--disable-client-side-phishing-detection',
        '--disable-component-extensions-with-background-pages',
        '--disable-features=TranslateUI,VizDisplayCompositor',
        '--disable-hang-monitor',
        '--disable-ipc-flooding-protection',
        '--disable-renderer-backgrounding',
        '--disable-features=site-per-process',
        '--disable-leak-detection',
    ]

    @classmethod
    def get_random_user_agent(cls) -> str:
        """获取随机 User-Agent"""
        return random.choice(cls.USER_AGENTS)


# ==================== 请求/响应模型 ====================

class FetchRequest(BaseModel):
    """抓取请求"""
    url: str
    wait_time: int = 200  # 等待时间（毫秒）
    wait_for_selector: str = ""  # 等待选择器
    screenshot: bool = True  # 是否截图
    block_media: bool = True  # 是否阻止图片/视频加载（降低内存）


class FetchResponse(BaseModel):
    """抓取响应"""
    success: bool
    fetched_url: str
    title: str = ""
    content: str = ""
    screenshot: str = ""  # base64 编码
    content_length: int = 0
    fetched_at: str = ""
    error: str = ""
    duration_seconds: float = 0  # 抓取耗时（秒）


# ==================== 内存监控工具 ====================

def get_memory_info() -> dict[str, Any]:
    """获取当前进程内存信息"""
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()

    # 获取所有子进程（Chromium 进程）
    children = process.children(recursive=True)
    children_mem = 0
    chromium_count = 0
    chromium_details = []  # 每个 Chromium 进程的详细信息

    for child in children:
        try:
            child_mem = child.memory_info().rss
            children_mem += child_mem
            # 检查是否是 Chromium 进程
            if 'chrom' in child.name().lower() or 'chrome' in child.name().lower():
                chromium_count += 1
                chromium_details.append({
                    "pid": child.pid,
                    "name": child.name(),
                    "rss_mb": round(child_mem / 1024 / 1024, 2),
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return {
        "process_rss_mb": round(mem_info.rss / 1024 / 1024, 2),
        "process_vms_mb": round(mem_info.vms / 1024 / 1024, 2),
        "children_rss_mb": round(children_mem / 1024 / 1024, 2),
        "total_rss_mb": round((mem_info.rss + children_mem) / 1024 / 1024, 2),
        "chromium_processes": chromium_count,
        "total_children": len(children),
        "chromium_details": chromium_details,  # 每个 Chromium 进程的详细信息
    }


def format_bytes(bytes_value: int) -> str:
    """格式化字节数为可读格式"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} TB"


def format_memory_mb(mb: float) -> str:
    """格式化内存 MB 为可读格式"""
    if mb < 1024:
        return f"{mb:.1f} MB"
    else:
        return f"{mb / 1024:.2f} GB"


def print_memory_summary(title: str, mem_info: dict, browser_pool=None, highlight_browser: int = None):
    """使用纯文本格式打印监控信息

    Args:
        title: 标题（如 "抓取中" 或 "抓取完成"）
        mem_info: get_memory_info() 返回的字典
        browser_pool: BrowserPool 实例（用于显示各浏览器状态）
        highlight_browser: 要高亮显示的浏览器索引
    """
    total_mb = mem_info['total_rss_mb']
    chromium_mb = mem_info['children_rss_mb']
    chromium_count = mem_info['chromium_processes']

    # 统计信息
    active_count = sum(1 for x in browser_pool._active_requests if x is not None) if browser_pool else 0
    total_requests = browser_pool._request_count if browser_pool else 0
    uptime = int(time.time() - browser_pool._start_time) if browser_pool else 0

    # 内存颜色
    if total_mb < 500:
        mem_color = "[green]"
    elif total_mb < 1000:
        mem_color = "[yellow]"
    else:
        mem_color = "[red]"

    # 格式化运行时间
    uptime_min = uptime // 60
    uptime_sec = uptime % 60
    if uptime_min > 0:
        uptime_text = f"{uptime_min}分{uptime_sec}秒"
    else:
        uptime_text = f"{uptime_sec}秒"

    cpu_percent = psutil.cpu_percent(interval=0.1)

    # 打印分隔线
    rich_console.print()

    # 概览信息
    rich_console.print(f"[bold cyan]━━ {title} ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]")
    rich_console.print(f"  CPU: {cpu_percent:.0f}%  |  总内存: {mem_color}{format_memory_mb(total_mb)}[/]  |  Chromium: {chromium_count}进程 ({format_memory_mb(chromium_mb)})")
    rich_console.print(f"  请求: {total_requests}次  |  运行: {uptime_text}", end="")
    if browser_pool:
        rich_console.print(f"  |  浏览器池: [cyan]{active_count}/{browser_pool.pool_size}[/] 活跃")
    else:
        rich_console.print()

    # 浏览器实例
    if browser_pool:
        rich_console.print(f"[bold cyan]━━ 浏览器实例 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]")

        for i in range(browser_pool.pool_size):
            count = browser_pool._fetch_counts[i]
            has_active = browser_pool._active_requests[i] is not None

            # 计算该浏览器实例的内存，并收集对应的进程 PID
            browser_mem_mb = 0
            browser_pids = []
            current_time = time.time()

            for detail in mem_info['chromium_details']:
                try:
                    proc = psutil.Process(detail['pid'])
                    proc_age = current_time - proc.create_time()
                    # 通过进程启动时间关联到浏览器实例
                    last_used = browser_pool._last_used[i]
                    if proc_age <= 300 and proc_age >= current_time - last_used - 1:
                        browser_mem_mb += detail['rss_mb']
                        browser_pids.append(detail['pid'])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            # 状态图标和样式（统一使用高亮背景色风格）
            if i == highlight_browser:
                status_icon = "▶"
                status_style = "[bold white on cyan]"
            elif has_active:
                status_icon = "⚡"
                status_style = "[bold black on yellow]"
            elif count >= 8:
                status_icon = "⚠"
                status_style = "[bold white on red]"
            elif count == 0:
                status_icon = "○"
                status_style = "[bold black on white]"
            else:
                status_icon = "✓"
                status_style = "[bold white on green]"

            # 内存显示
            if browser_mem_mb > 0:
                mem_color = "[red]" if browser_mem_mb > 200 else "[yellow]" if browser_mem_mb > 100 else "[green]"
                mem_text = f"{mem_color}{format_memory_mb(browser_mem_mb)}[/]"
            else:
                mem_text = "[dim]--[/]"

            # 重启倒计时
            restart_left = browser_pool._restart_threshold - count
            restart_text = f"{restart_left}次后重启" if restart_left > 3 else f"[yellow]{restart_left}次后重启[/]"

            # PID 显示（如果有）
            pid_part = ""
            if browser_pids:
                pid_str = ",".join(map(str, browser_pids[:3]))
                if len(browser_pids) > 3:
                    pid_str += f"+{len(browser_pids)-3}"
                pid_part = f"  |  PID: [cyan]{pid_str}[/]"

            rich_console.print(
                f"  {status_style}{status_icon}[/]  [cyan]B{i}[/]  {count}次  |  {restart_text}{pid_part}  |  内存: {mem_text}"
            )

    rich_console.print()


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
        self._stealth = Stealth()  # 复用 Stealth 实例
        self._fetch_counts = [0] * pool_size  # 每个浏览器的抓取计数
        self._restart_threshold = 10  # 每抓取 10 次强制重启
        self._last_used: list = [0.0] * pool_size  # 每个浏览器的最后使用时间
        self._idle_timeout = 5  # 空闲 5 秒后重启（如果有使用过）
        self._monitor_stop = None  # 监控任务停止事件
        self._active_requests: list = [None] * pool_size  # 每个浏览器当前是否有活跃请求的锁
        self._browser_locks: list = [asyncio.Lock() for _ in range(pool_size)]  # 每个浏览器的锁

    async def initialize(self):
        """初始化浏览器池"""
        if self._initialized:
            return

        # 使用 Rich 输出初始化信息
        rich_console.print()
        rich_console.print(Panel(
            f"[cyan]正在初始化浏览器实例池，大小: {self.pool_size}[/cyan]",
            border_style="cyan",
            padding=(0, 2)
        ))

        try:
            self.playwright = await async_playwright().start()

            # 启动多个浏览器实例（使用 Rich 进度条）
            rich_console.print()
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=40),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                console=rich_console,
                transient=True
            ) as progress:
                task = progress.add_task("[cyan]启动浏览器实例...[/]", total=self.pool_size)

                for i in range(self.pool_size):
                    browser = await self.playwright.chromium.launch(
                        headless=Config.HEADLESS,
                        args=Config.BROWSER_ARGS
                    )
                    self.browsers.append(browser)
                    progress.advance(task)

            self._initialized = True

            # 使用 Rich 美化输出（使用顶部已导入的 Table, Panel, box）
            init_table = Table(show_header=False, box=box.ROUNDED, padding=(0, 1))

            init_table = Table(show_header=False, box=box.ROUNDED, padding=(0, 1))
            init_table.add_column("", style="cyan", width=18)
            init_table.add_column("", justify="right")

            init_table.add_row("浏览器池大小", f"[cyan]{len(self.browsers)} 个[/]")
            init_table.add_row("最大并发", f"[cyan]{Config.MAX_CONCURRENT_PAGES}[/]")
            init_table.add_row("理论最大并发", f"[green]{len(self.browsers) * Config.MAX_CONCURRENT_PAGES}[/]")

            rich_console.print()
            rich_console.print(Panel(
                init_table,
                title="[bold green]✓ 浏览器池初始化完成[/bold green]",
                border_style="green"
            ))
            rich_console.print()

            # 启动常驻监控任务
            self._monitor_stop = asyncio.Event()
            asyncio.create_task(self._monitor_idle_browsers())

        except Exception as e:
            logger.error(f"初始化浏览器池失败: {e}")
            raise

    async def shutdown(self):
        """关闭所有浏览器实例"""
        # 使用 Rich 美化输出
        rich_console.print()
        rich_console.print(Panel(
            "[yellow]正在关闭浏览器实例池...[/yellow]",
            border_style="yellow",
            padding=(0, 2)
        ))

        # 先停止常驻监控任务
        if self._monitor_stop:
            self._monitor_stop.set()
            # Rich 输出
            rich_console.print(Panel(
                "[dim]⟳ 常驻监控任务已停止[/dim]",
                border_style="dim",
                padding=(0, 2)
            ))

        # 等待监控任务完全停止
        await asyncio.sleep(0.5)

        # 关闭所有浏览器实例（使用 Rich 进度条）
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=rich_console,
            transient=True
        ) as progress:
            task = progress.add_task("[cyan]关闭浏览器实例...[/]", total=len(self.browsers))

            for i, browser in enumerate(self.browsers):
                try:
                    await browser.close()
                    progress.advance(task)
                except Exception as e:
                    rich_console.print(Panel(
                        f"[red]关闭浏览器实例 {i} 时出错: {e}[/red]",
                        border_style="red"
                    ))

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
            # 获取一个可用的浏览器实例（原子轮询）
            browser_index = self._request_count % len(self.browsers)
            # 先标记浏览器正在使用（避免被监控任务重启）
            self._active_requests[browser_index] = True
            browser = self.browsers[browser_index]

            # 打印开始抓取（带监控面板）
            mem_info = get_memory_info()
            print_memory_summary(
                f"开始抓取 ({request.url[:50]}...)",
                mem_info,
                browser_pool=self,
                highlight_browser=browser_index
            )

            # 使用锁防止在请求过程中重启
            async with self._browser_locks[browser_index]:

                context = None
            page = None

            try:
                # 每次创建新的 context（干净隔离，创建很快）
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent=Config.get_random_user_agent(),
                )

                page = await context.new_page()

                # 只拦截真正的媒体文件，不阻止样式和字体
                if request.block_media:
                    async def block_media_route(route, request):
                        resource_type = request.resource_type
                        # 只阻止图片、视频、音频，允许所有其他资源
                        if resource_type in ["image", "media", "audio", "video"]:
                            await route.abort()
                        else:
                            await route.continue_()
                    await page.route("**", block_media_route)

                # 应用反爬虫脚本
                await self._apply_stealth(page)

                # 设置请求头
                await page.set_extra_http_headers(self._get_headers())

                # 导航到页面，等待完全加载（超时则使用已加载内容）
                try:
                    await page.goto(request.url, wait_until="load", timeout=30000)
                except Exception as goto_error:
                    error_msg = str(goto_error)
                    # 如果是页面/浏览器已关闭的致命错误，直接返回
                    if "closed" in error_msg.lower() or "crash" in error_msg.lower() or "target" in error_msg.lower():
                        raise
                    logger.warning(f"页面加载超时或出错，使用已加载内容: {goto_error}")

                # 等待指定时间
                if request.wait_time > 0:
                    await page.wait_for_timeout(request.wait_time)

                # 等待选择器（超时不影响结果）
                if request.wait_for_selector:
                    try:
                        await page.wait_for_selector(request.wait_for_selector, timeout=10000)
                    except Exception:
                        logger.warning(f"等待选择器超时: {request.wait_for_selector}")

                # 滚动到页面底部
                await self._scroll_page(page)

                # 获取内容
                title = await page.title()
                html_content = await page.content()

                # 异步转换为 Markdown（避免阻塞事件循环）
                markdown_content = await asyncio.to_thread(markdownify, html_content)
                cleaned_content = self._clean_markdown(markdown_content)
                # 修复相对链接为绝对链接
                fixed_content = self._fix_links(cleaned_content, request.url)

                # 截图（整页，JPEG 格式降低质量以减小文件大小）
                screenshot_b64 = ""
                if request.screenshot:
                    import base64
                    screenshot_bytes = await page.screenshot(
                        full_page=True,
                        type="jpeg",
                        quality=60  # JPEG 质量 0-100，60 平衡质量和大小
                    )
                    screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

                duration_seconds = time.time() - start_time

                return FetchResponse(
                    success=True,
                    fetched_url=request.url,
                    title=title or "无标题",
                    content=fixed_content,
                    screenshot=screenshot_b64,
                    content_length=len(fixed_content),
                    fetched_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                    duration_seconds=duration_seconds
                )

            except Exception as e:
                logger.error(f"抓取失败 {request.url}: {e}")
                duration_seconds = time.time() - start_time
                return FetchResponse(
                    success=False,
                    fetched_url=request.url,
                    error=str(e),
                    duration_seconds=duration_seconds
                )

            finally:
                # 关闭页面和 context，彻底释放内存
                if page:
                    try:
                        await page.evaluate("window.document.body.innerHTML = ''")
                        await page.close()
                        page = None
                    except:
                        page = None

                if context:
                    try:
                        await context.close()
                        context = None
                    except:
                        context = None

                # 强制多次垃圾回收，确保内存释放
                import gc
                for _ in range(3):
                    gc.collect()

                # 更新统计信息（只更新一次）
                self._last_used[browser_index] = time.time()
                self._fetch_counts[browser_index] += 1

                # 取消活跃请求标记（请求已完成）
                self._active_requests[browser_index] = None

                # 请求完成后的内存状态（使用 Rich 美化输出）
                mem_info = get_memory_info()

                # 输出抓取完成状态
                print_memory_summary(
                    "📊 抓取完成",
                    mem_info,
                    browser_pool=self,
                    highlight_browser=browser_index
                )

    
    async def _apply_stealth(self, page):
        """应用反爬虫脚本"""
        await self._stealth.apply_stealth_async(page)

    def _get_headers(self) -> dict[str, str]:
        """获取请求头"""
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "DNT": "1",
        }

    async def _monitor_idle_browsers(self):
        """常驻监控任务：定期检查每个浏览器实例的空闲状态"""
        while not self._monitor_stop.is_set():
            try:
                await asyncio.sleep(1)  # 每 1 秒检查一次
                current_time = time.time()

                for i in range(self.pool_size):
                    # 计算空闲时间
                    idle_time = current_time - self._last_used[i]
                    has_been_used = self._fetch_counts[i] > 0

                    # 检查是否有活跃请求
                    has_active_request = self._active_requests[i] is not None

                    # 重启条件：有使用过且空闲超过5秒且无活跃请求
                    should_restart = has_been_used and idle_time > self._idle_timeout and not has_active_request

                    if should_restart:
                        self._fetch_counts[i] = 0
                        async with self._browser_locks[i]:
                            try:
                                await self.browsers[i].close()
                                new_browser = await self.playwright.chromium.launch(
                                    headless=Config.HEADLESS,
                                    args=Config.BROWSER_ARGS
                                )
                                self.browsers[i] = new_browser

                                # 重启后垃圾回收并输出状态
                                import gc
                                gc.collect()
                                mem_info = get_memory_info()
                                print_memory_summary("✓ 浏览器重启完成", mem_info, browser_pool=self)
                            except Exception as e:
                                logger.error(f"重启浏览器 {i} 失败: {e}")
            except Exception as e:
                logger.error(f"监控任务异常: {e}")

    async def _scroll_page(self, page) -> None:
        """智能滚动页面以加载懒加载内容

        Args:
            page: Playwright 页面对象

        Returns:
            None
        """
        max_scrolls = 20
        scroll_wait_ms = 500

        try:
            for i in range(max_scrolls):
                # 检查是否已滚动到底部
                is_at_bottom = await page.evaluate("""
                    () => {
                        const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                        const windowHeight = window.innerHeight || document.documentElement.clientHeight;
                        const documentHeight = document.documentElement.scrollHeight;
                        return scrollTop + windowHeight >= documentHeight - 100;
                    }
                """)

                if is_at_bottom:
                    break

                # 执行滚动
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(scroll_wait_ms / 1000)

                logger.debug(f"执行第 {i+1} 次滚动")

        except Exception as e:
            logger.warning(f"滚动过程出错: {e}")

    def _clean_markdown(self, content: str) -> str:
        """清理 Markdown"""
        content = re.sub(r'\n{3,}', '\n\n', content)
        content = re.sub(r'^\s+$/gm', '', content)
        return content.strip()

    def _fix_links(self, content: str, base_url: str) -> str:
        """修复 Markdown 中的相对链接为绝对链接

        Args:
            content: Markdown 内容
            base_url: 基础 URL

        Returns:
            修复后的 Markdown 内容
        """
        # 提取基础 URL 的协议（http 或 https）
        base_protocol = 'https://' if base_url.startswith('https://') else 'http://'

        # 修复 Markdown 链接语法 [文本](链接) 和图片 ![alt](url)
        def fix_markdown_link(match):
            is_image = match.group(1).startswith('!')  # 是否是图片
            text = match.group(2)
            url = match.group(3)
            # 跳过已经是绝对链接的
            if url.startswith(('http://', 'https://', '#', 'mailto:', 'tel:')):
                return match.group(0)
            # 处理协议相对链接 //example.com
            if url.startswith('//'):
                url = base_protocol + url
                return match.group(0).replace(f']({match.group(3)})', f']({url})')
            # 转换为绝对链接
            absolute_url = urljoin(base_url, url)
            return match.group(0).replace(f']({match.group(3)})', f']({absolute_url})')

        # 匹配 [text](url) 和 ![alt](url)
        content = re.sub(r'(\!?\[)([^\]]+)\]\(([^)]+)\)', fix_markdown_link, content)

        # 修复 HTML 标签中的链接
        def fix_html_link(match):
            tag = match.group(1)
            url = match.group(2)
            # 移除 JavaScript 链接（安全考虑）
            if url.startswith('javascript:'):
                return f'{tag}="#"'
            # 跳过已经是绝对链接的
            if url.startswith(('http://', 'https://', '#', 'mailto:', 'tel:', 'data:')):
                return match.group(0)
            # 处理协议相对链接 //example.com
            if url.startswith('//'):
                absolute_url = base_protocol + url
                return f'{tag}="{absolute_url}"'
            # 转换为绝对链接
            absolute_url = urljoin(base_url, url)
            return f'{tag}="{absolute_url}"'

        # 匹配 href 和 src 属性
        content = re.sub(r'(href|src)="([^"]*)"', fix_html_link, content)

        # 移除空的 href 属性（会导致页面跳转到自身）
        content = re.sub(r'href=""', 'href="#"', content)

        return content


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


@app.post("/fetch_url")
async def fetch_url(
    request: FetchRequest
):
    """
    抓取网页并返回内容

    Args:
        request: 抓取请求

    Returns:
        包含 Markdown 内容和截图的抓取结果
    """
    pool = get_browser_pool()
    result = await pool.fetch_page(request)

    if not result.success:
        return result

    # 直接返回内存中的数据，不生成临时文件
    return {
        "success": True,
        "fetched_url": result.fetched_url,
        "title": result.title,
        "markdown_content": result.content,
        "screenshot_base64": result.screenshot,
        "content_length": result.content_length,
        "fetched_at": result.fetched_at,
        "duration_seconds": result.duration_seconds
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
