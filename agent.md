# Browser Fetch Service - AI Agent 使用说明

本文档记录了浏览器抓取服务的重要配置和修改注意事项，供 AI Agent 和开发人员参考。

---

## 最近的修改 (2026-02-13)

### 1. 页面加载策略优化

**问题：** 独立新闻网（如 independent.co.uk）页面加载不完整

**解决方案：**
```python
# app.py 第 324 行
await page.goto(request.url, wait_until="load", timeout=30000)
```

**说明：**
- 从 `wait_until="commit"` 改为 `wait_until="load"`
- `commit`: 仅等待服务器响应（页面开始加载）
- `load`: 等待页面完全加载完成
- `networkidle`: 等待网络空闲（最慢，适合 SPA）

### 2. 整页截图功能

**实现：**
```python
# app.py 第 356-360 行
screenshot_bytes = await page.screenshot(
    full_page=True,  # 整页截图
    type="jpeg",      # JPEG 格式
    quality=60        # 质量 0-100，60 平衡质量和大小
)
```

**参数说明：**
| 参数 | 值 | 说明 |
|------|-----|------|
| full_page | True | 截取整个页面，包括滚动区域 |
| type | jpeg | JPEG 格式比 PNG 小 3-5 倍 |
| quality | 60 | 60% 质量，视觉效果可接受，文件小 |

### 3. 分辨率优化

```python
# app.py 第 299 行
viewport={"width": 1280, "height": 720}
```

**内存影响：**
- 1920×1080 → 约 8MB/图
- 1280×720 → 约 3.5MB/图
- 节省约 **56% 截图内存**

### 4. 媒体拦截优化

**策略变更：** 从文件扩展名拦截改为资源类型拦截

```python
# app.py 第 306-314 行
async def block_media_route(route, request):
    resource_type = request.resource_type
    # 只阻止图片、视频、音频，允许所有其他资源
    if resource_type in ["image", "media", "audio", "video"]:
        await route.abort()
    else:
        await route.continue_()
```

**拦截效果：**
| 资源类型 | 是否拦截 | 说明 |
|----------|----------|------|
| image | ✅ | 图片 |
| media/audio/video | ✅ | 媒体文件 |
| stylesheet | ❌ | 样式（保留） |
| script | ❌ | 脚本（保留） |
| font | ❌ | 字体（保留） |
| document | ❌ | 文档（保留） |

**内存节省：** 40-60%

### 5. 内存监控增强

**新增字段：** `chromium_details` 数组

```python
# app.py 第 117-142 行
chromium_details = [
    {"pid": 12345, "name": "chrome-headless-shell", "rss_mb": 150.2},
    {"pid": 12346, "name": "chrome-headless-shell", "rss_mb": 120.4},
    # ...
]
```

**监控输出示例：**
```
📊 [抓取中] 总内存: 750.5MB
浏览器 0:
  └─ 进程: 12345 (chrome-headless-shell) - 150.2MB
浏览器 1:
  └─ 进程: 12346 (chrome-headless-shell) - 120.4MB
浏览器 2:
  (无活跃进程)
```

### 6. 智能重启机制

**重启条件（同时满足）：**

1. **强制重启：** 抓取次数达到 10 次
2. **空闲重启：**
   - 已使用过（`fetch_counts > 0`）
   - 空闲超过 5 秒
   - 无活跃请求

**活跃请求保护：**
```python
# app.py 第 173, 292, 421 行
self._active_requests: list = [None] * pool_size

# 请求开始
self._active_requests[browser_index] = True

# 请求完成
self._active_requests[browser_index] = None

# 重启前检查
has_active_request = self._active_requests[i] is not None
if not has_active_request:  # 只有无活跃请求时才重启
    # 执行重启...
```

### 7. 链接自动修复

**功能：** 将 Markdown 中的相对链接自动转换为绝对链接

**修复类型：**

| 链接类型 | 修复前 | 修复后 |
|----------|--------|--------|
| Markdown 链接 | `[text](/abc)` | `[text](https://sample.com/abc)` |
| 协议相对路径 | `//cdn.com/lib.js` | `https://cdn.com/lib.js` |
| HTML href | `href="login"` | `href="https://sample.com/login"` |
| JavaScript 链接 | `javascript:void(0)` | `href="#"` |
| Markdown 图片 | `![alt](img.jpg)` | `![alt](https://sample.com/img.jpg)` |
| 空链接 | `href=""` | `href="#"` |

**实现位置：** `app.py` 第 708-758 行

### 8. 读写锁机制（引用计数 + Condition 变量）

**问题场景：** 监控进程要重启浏览器时，刚好有请求正在使用该浏览器，导致请求失败。

**解决方案：** 使用引用计数 + `asyncio.Condition` 实现读写锁模式

| 角色 | 允许并发 | 说明 |
|------|----------|------|
| 请求（读者） | ✅ 是 | 多个请求可同时使用同一浏览器 |
| 重启（写者） | ❌ 否 | 需要等待所有请求完成 |

**核心数据结构：**
```python
# app.py 第 370-373 行
self._ref_counts = [0] * pool_size      # 每个浏览器的活跃请求计数
self._restarting = [False] * pool_size   # 是否正在重启
self._conditions = [asyncio.Condition() for _ in range(pool_size)]  # 条件变量
```

**工作流程：**

| 步骤 | 请求侧 | 监控侧 |
|------|--------|--------|
| 1. 获取锁 | `async with cond:` | `async with cond:` |
| 2. 等待 | `while self._restarting[i]: await cond.wait()` | 无 |
| 3. 操作 | `self._ref_counts[i] += 1` | `self._restarting[i] = True` |
| 4. 释放 | `self._ref_counts[i] -= 1; cond.notify_all()` | `self._restarting[i] = False; cond.notify_all()` |

**代码实现：**

请求获取浏览器（`app.py` 第 444-452 行）：
```python
cond = self._conditions[browser_index]
async with cond:
    # 如果正在重启，等待完成
    while self._restarting[browser_index]:
        logger.info(f"浏览器 {browser_index} 正在重启，等待完成...")
        await cond.wait()
    # 增加引用计数
    self._ref_counts[browser_index] += 1
```

请求完成（`app.py` 第 586-593 行）：
```python
cond = self._conditions[browser_index]
async with cond:
    self._ref_counts[browser_index] -= 1
    # 通知等待的监控任务（如果有）
    cond.notify_all()
```

监控重启（`app.py` 第 631-661 行）：
```python
# 重启条件检查
should_restart = (
    has_been_used
    and idle_time > self._idle_timeout
    and self._ref_counts[i] == 0      # 无活跃请求
    and not self._restarting[i]       # 未在重启中
)

async with cond:
    self._restarting[i] = True        # 标记为重启，阻止新请求

try:
    # 重启浏览器...
    await self.browsers[i].close()
    self.browsers[i] = await self.playwright.chromium.launch(...)
finally:
    async with cond:
        self._restarting[i] = False
        cond.notify_all()              # 通知等待的请求
```

**时序图：**

```
请求A          请求B          监控任务
  │              │              │
  ├─ acquire ───┤              │
  ├─ ref=1 ─────┤              │
  │              ├─ acquire ───┤
  │              ├─ ref=2 ─────┤
  │              │              ├─ 检查 ref=2，跳过
  │              │              │
  ├─ ref=1 ─────┤              │
  │              ├─ ref=1 ─────┤
  │              │              ├─ 检查 ref=0，开始重启
  │              │              ├─ restarting=True
  │              │              │
  ├─ acquire ───┤              │
  ├─ 等待 ──────┤              │
  │              │              ├─ 重启完成
  │              │              ├─ restarting=False
  │              │              ├─ notify_all()
  ├─ 继续执行 ──┤              │
```

### 9. 并发负载均衡分配逻辑

**问题场景：** 多个并发请求同时到达时，所有请求都被分配到同一个浏览器实例。

**根本原因：** `self._request_count += 1` 在 `semaphore` 外部执行

**错误示例：**

```python
# 错误写法（修复前）
self._request_count += 1              # 在 semaphore 外部

async with self.semaphore:
    browser_index = self._request_count % len(self.browsers)
```

**执行流程：**
```
时间线：请求A/B/C/D 同时到达

请求A: _count=0 → _count=1 → 等待 semaphore
请求B: _count=1 → _count=2 → 等待 semaphore
请求C: _count=2 → _count=3 → 等待 semaphore
请求D: _count=3 → _count=4 → 等待 semaphore

进入 semaphore 后，所有请求都用 _count=4 计算：
请求A: 4 % 4 = 0  → 浏览器0
请求B: 4 % 4 = 0  → 浏览器0
请求C: 4 % 4 = 0  → 浏览器0
请求D: 4 % 4 = 0  → 浏览器0
```

**解决方案：** 把计数器移入 semaphore 内部

```python
# 正确写法（修复后）
async with self.semaphore:
    self._request_count += 1              # 在 semaphore 内部
    browser_index = (self._request_count - 1) % len(self.browsers)
```

**修复后流程：**
```
时间线：请求A/B/C/D 同时到达

请求A: 进入 semaphore → _count=1 → 0%4=0 → 浏览器0
请求B: 进入 semaphore → _count=2 → 1%4=1 → 浏览器1
请求C: 进入 semaphore → _count=3 → 2%4=2 → 浏览器2
请求D: 进入 semaphore → _count=4 → 3%4=3 → 浏览器3
```

**代码位置：** `app.py` 第 436-442 行

**关键点：**
1. `self._request_count += 1` 必须在 `async with self.semaphore` 内部
2. 使用 `(self._request_count - 1)` 确保索引从 0 开始
3. `semaphore` 限制总并发数为 `POOL_SIZE`，防止浏览器实例过载

---

## 关键配置

### 浏览器池配置

```python
# 默认值（app.py 第 38-39 行）
POOL_SIZE = 3  # 浏览器实例数量
MAX_CONCURRENT_PAGES = 10  # 每个实例的最大并发数
```

**理论最大并发：** 3 × 10 = **30 个同时请求**

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| BROWSER_SERVICE_PORT | 2025 | 服务端口 |
| BROWSER_SERVICE_HOST | 0.0.0.0 | 服务主机 |
| HEADLESS | true | 无头模式 |
| BROWSER_POOL_SIZE | 3 | 浏览器池大小 |
| MAX_CONCURRENT_PAGES | 10 | 最大并发页面数 |
| MAX_SCREENSHOT_SIZE | 5242880 | 最大截图大小（字节） |

### 内存估算

| 配置 | 最小内存 | 最大内存 | 峰值内存 |
|------|----------|----------|----------|
| POOL=2, CONCURRENT=3 | 300 MB | 500 MB | ~400 MB |
| POOL=3, CONCURRENT=10 | 600 MB | 900 MB | ~750 MB |
| POOL=5, CONCURRENT=10 | 1.5 GB | 2.5 GB | ~2 GB |

**注意：** 以上估算已启用 `block_media: true`

---

## 常见问题

### Q1: 页面加载不完整怎么办？

**检查项：**
1. 确认 `wait_until="load"`（已在代码中）
2. 增加 `wait_time` 参数（默认 200ms）
3. 使用 `wait_for_selector` 等待特定元素

**示例：**
```json
{
  "url": "https://example.com",
  "wait_time": 1000,
  "wait_for_selector": ".main-content"
}
```

### Q2: 内存占用过高怎么办？

**优化方案：**
1. 确保 `block_media: true`（默认已启用）
2. 减少 `BROWSER_POOL_SIZE`
3. 检查 `/health` 端点监控内存

### Q3: 如何确认智能重启是否工作？

**查看日志：**
```bash
# 空闲重启日志
浏览器 0 空闲5秒，执行重启...
📊 [重启完成] RSS: 45.2MB | 子进程: 1024.5MB | 总计: 1069.7MB

# 强制重启日志（达到10次）
浏览器 0 达到10次，执行重启...
```

### Q4: 链接修复不生效？

**检查项：**
1. 确认 `base_url` 参数正确传递
2. 查看返回的 `markdown_content` 字段
3. 链接修复在服务端自动完成，无需额外处理

---

## 调试命令

### 检查服务状态
```bash
curl http://localhost:2025/health
```

### 获取详细统计
```bash
curl http://localhost:2025/stats
```

### Prometheus 指标
```bash
curl http://localhost:2025/metrics
```

### 查看 Chromium 进程
```bash
ps aux | grep chromium
```

### 实时监控内存
```bash
watch -n 1 'curl -s http://localhost:2025/health | jq ".memory"'
```

### 内存监控输出

服务已内置 **Rich 美化输出**，所有内存监控信息会自动以表格形式展示：

**输出场景：**
- 📊 初始化阶段（进度条 + 摘要表格）
- 📊 抓取中/完成（总览 + 进程详情）
- 🔄 浏览器重启（警告面板）
- 🔽 服务关闭（进度条）

无需额外运行监控脚本，启动服务即可看到美化输出。

---

## 代码位置参考

| 功能 | 文件位置 | 行号 |
|------|----------|------|
| 页面加载策略 | app.py | 494 |
| 整页截图 | app.py | 530-535 |
| 媒体拦截 | app.py | 477-485 |
| 内存监控函数 | app.py | 108-142 |
| Rich 美化输出 | app.py | 145-273 |
| 读写锁机制（引用计数+Condition） | app.py | 309-313 (数据结构), 444-452 (请求获取), 586-593 (请求完成), 617-661 (监控重启) |
| 并发负载均衡分配 | app.py | 436-442 |
| 智能滚动 | app.py | 680-713 |
| 链接修复 | app.py | 716-758 |
| Rich 进度条 | app.py | 338-365 (启动), 395-415 (关闭) |

---

## 版本历史

| 日期 | 版本 | 主要变更 |
|------|------|----------|
| 2026-02-13 | 1.0.0 | 优化页面加载、整页截图、内存监控、智能重启 |
