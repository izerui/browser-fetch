# 独立浏览器抓取服务

一个独立的、支持高并发的网页抓取服务，使用 Playwright 实现。

## 功能特性

- 🚀 支持高并发（可配置浏览器实例数量）
- 🔄 自动重启机制
- 📊 健康检查端点
- 🎯 反爬虫检测规避
- 📸 自动截图
- 📝 Markdown 格式输出

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 文件配置参数
```

### 3. 启动服务

```bash
python main.py
```

服务将在 `http://localhost:2025` 启动。

## 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `BROWSER_SERVICE_PORT` | 2025 | 服务端口 |
| `BROWSER_SERVICE_HOST` | 0.0.0.0 | 服务主机 |
| `HEADLESS` | true | 无头模式 |
| `BROWSER_POOL_SIZE` | 5 | 浏览器实例池大小 |
| `MAX_CONCURRENT_PAGES` | 10 | 每个实例的最大并发页面数 |
| `MAX_SCREENSHOT_SIZE` | 5242880 | 最大截图大小（字节） |

## API 端点

### 健康检查
```
GET /health
```

响应示例：
```json
{
  "status": "healthy",
  "browser_started": true,
  "pool_size": 5,
  "max_concurrent": 10,
  "request_count": 42,
  "uptime_seconds": 3600.5,
  "memory": {
    "process_rss_mb": 45.2,
    "process_vms_mb": 512.3,
    "children_rss_mb": 2048.5,
    "total_rss_mb": 2093.7,
    "chromium_processes": 5,
    "total_children": 5
  }
}
```

### 详细统计
```
GET /stats
```

响应示例：
```json
{
  "service": {
    "name": "Browser Fetch Service",
    "version": "1.0.0",
    "uptime_seconds": 3600.5,
    "request_count": 42,
    "requests_per_second": 0.012
  },
  "browser_pool": {
    "pool_size": 5,
    "max_concurrent": 10,
    "initialized": true,
    "active_browsers": 5
  },
  "memory": {
    "process_mb": 45.2,
    "children_mb": 2048.5,
    "total_mb": 2093.7,
    "chromium_processes": 5,
    "total_children": 5
  },
  "system": {
    "cpu_percent": 15.5,
    "memory_total_gb": 16.0,
    "memory_available_gb": 8.5,
    "memory_percent": 46.9
  }
}
```

### Prometheus 监控指标
```
GET /metrics
```

响应格式（Prometheus 文本格式）：
```
# HELP browser_service_requests_total Total number of requests
# TYPE browser_service_requests_total counter
browser_service_requests_total 42

# HELP browser_service_uptime_seconds Service uptime in seconds
# TYPE browser_service_uptime_seconds gauge
browser_service_uptime_seconds 3600.50

# HELP browser_service_pool_size Browser pool size
# TYPE browser_service_pool_size gauge
browser_service_pool_size 5

# HELP browser_service_memory_bytes Total memory usage in bytes
# TYPE browser_service_memory_bytes gauge
browser_service_memory_bytes 2196484096

# HELP browser_service_chromium_processes Number of Chromium processes
# TYPE browser_service_chromium_processes gauge
browser_service_chromium_processes 5

# HELP browser_service_max_concurrent Maximum concurrent pages per browser
# TYPE browser_service_max_concurrent gauge
browser_service_max_concurrent 10
```

### 抓取网页
```
POST /fetch
Content-Type: application/json

{
  "url": "https://example.com",
  "wait_time": 1000,
  "wait_for_selector": "",
  "screenshot": true
}
```

### 抓取并保存文件
```
POST /fetch_with_files?root_dir=/path/to/save
Content-Type: application/json

{
  "url": "https://example.com",
  "wait_time": 1000,
  "screenshot": true
}
```

## Docker 部署

```bash
docker build -t browser-service .
docker run -p 2025:2025 -e BROWSER_POOL_SIZE=5 browser-service
```

## 架构说明

### 异步架构
- ✅ 完全使用 `async/await`，无阻塞调用
- ✅ Async Playwright API
- ✅ 异步 HTTP 通信 (httpx.AsyncClient)
- ✅ 所有 I/O 操作都是异步的

### 高并发支持
```
浏览器实例池设计：
┌─────────────────────────────────────────┐
│         BrowserPool (5 个实例)           │
├─────────────────────────────────────────┤
│  实例 1 ──┐                              │
│  实例 2 ──┼──> 并发处理请求               │
│  实例 3 ──┤    (轮询分配)                │
│  实例 4 ──┤                              │
│  实例 5 ──┘                              │
└─────────────────────────────────────────┘
```

**理论最大并发：** `BROWSER_POOL_SIZE × MAX_CONCURRENT_PAGES`

默认配置：5 实例 × 10 并发 = **50 个同时抓取请求**

## 内存使用估算

### 单个 Chromium 实例内存占用

| 组件 | 内存占用 |
|------|----------|
| Chromium 主进程 | ~50-80 MB |
| 渲染进程 | ~100-200 MB |
| 每个标签页/页面 | ~30-50 MB |
| 基础开销 | ~20 MB |

### 不同配置方案内存估算

| BROWSER_POOL_SIZE | MAX_CONCURRENT_PAGES | 最小内存 | 最大内存 | 峰值内存 |
|-------------------|----------------------|----------|----------|----------|
| 2 | 3 | 400 MB | 600 MB | ~500 MB |
| 3 | 5 | 800 MB | 1.2 GB | ~1 GB |
| 5 | 10 | 2.5 GB | 3.8 GB | ~3.4 GB |
| 10 | 20 | 5 GB | 8 GB | ~6.5 GB |

**估算公式：**
```
基础开销: 20 MB
浏览器实例: POOL_SIZE × (80 MB + 200 MB)
并发页面: (POOL_SIZE × CONCURRENT) × 40 MB
```

### 推荐配置

| 场景 | 内存预算 | 推荐配置 | 命令 |
|------|----------|----------|------|
| 开发测试 | 1-2 GB | POOL_SIZE=2, CONCURRENT=3 | `export BROWSER_POOL_SIZE=2 && export MAX_CONCURRENT_PAGES=3` |
| 小型生产 | 4 GB | POOL_SIZE=5, CONCURRENT=10 (默认) | - |
| 中型生产 | 8 GB | POOL_SIZE=10, CONCURRENT=15 | `export BROWSER_POOL_SIZE=10 && export MAX_CONCURRENT_PAGES=15` |
| 大型生产 | 16 GB+ | POOL_SIZE=15, CONCURRENT=20 | `export BROWSER_POOL_SIZE=15 && export MAX_CONCURRENT_PAGES=20` |

### 内存优化建议

如果内存受限，可以调整浏览器启动参数：

```python
# 在 app.py 的 Config.BROWSER_ARGS 中添加
'--single-process',              # 单进程模式（节省内存但降低稳定性）
'--memory-pressure-off',         # 禁用内存压力检测
'--max_old_space_size=256',      # 限制 V8 堆内存
```

**注意：** 单进程模式会显著降低稳定性，仅建议在内存严重受限时使用。

## 性能特性

| 特性 | 状态 | 说明 |
|------|------|------|
| 异步架构 | ✅ | 完全使用 `async/await`，无阻塞调用 |
| 高并发支持 | ✅ | 浏览器实例池 + 信号量控制 |
| 默认并发数 | 50 | 5 实例 × 10 并发/实例 |
| 可扩展性 | ✅ | 通过环境变量调整池大小 |
| 进程隔离 | ✅ | 独立服务，不影响主应用 |
| 资源清理 | ✅ | 自动关闭 page 和 context |

## 监控和调试

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

### 查看进程内存使用
```bash
# 查看所有 Chromium 进程
ps aux | grep chromium

# 实时监控
watch -n 1 'ps aux | grep chromium | grep -v grep'

# 查看内存使用详情
curl -s http://localhost:2025/stats | jq '.memory'
```

### 查看进程内存使用
```bash
# 查看所有 Chromium 进程
ps aux | grep chromium

# 实时监控
watch -n 1 'ps aux | grep chromium | grep -v grep'
```

### Docker 容器监控
```bash
docker stats browser-service
```
