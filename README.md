# 漫画爬虫 · Comic Manga Downloader

> 从零到一：如何不靠浏览器驱动，纯 HTTP 请求下载数千张漫画图片。
>
> From zero to one: how to download thousands of manga images with pure HTTP requests — no browser automation needed.

[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![No Selenium](https://img.shields.io/badge/selenium-not%20needed-brightgreen)]()

---

## 📖 目录 / Table of Contents

- [快速开始 / Quick Start](#-快速开始--quick-start)
- [整体流程 / How It Works](#-整体流程--how-it-works)
- [代码导读 / Code Walkthrough](#-代码导读--code-walkthrough)
- [逆向过程（深度篇） / Reverse Engineering Deep Dive](#-逆向过程深度篇--reverse-engineering-deep-dive)
- [配置说明 / Configuration](#-配置说明--configuration)
- [常见问题 / FAQ](#-常见问题--faq)
- [免责声明 / Disclaimer](#-免责声明--disclaimer)

---

## 🚀 快速开始 / Quick Start

### 你需要什么 / Prerequisites

- Python **3.10+** 已安装（[下载](https://www.python.org/downloads/)）
- 一份章节列表 HTML 文件（`manhua_page.html`）—— [如何获取？](#-如何获取章节列表-html)

### 三步运行 / 3-Step Setup

```bash
# 1. 安装依赖
pip install requests beautifulsoup4 Pillow

# 2. 确保 manhua_page.html 在项目目录下

# 3. 运行
python 2传武爬取.py
```

运行时会提示：`是否要将图片合并为长图？(y/n, 默认n):` —— 选 `y` 则每章图片拼成一张长图。

### 指定文件 / Custom Input

```bash
python 2传武爬取.py -i dongman.html
```

### 下载到哪里 / Output Structure

```
comic/
└── 传武/
    ├── 第472话 第三卷 155 拔刺与突袭/
    │   ├── 1.webp
    │   ├── 2.webp
    │   ├── ...
    │   └── 第472话 第三卷 155 拔刺与突袭_merged.jpg  ← 合并长图（可选）
    ├── 第471话 .../
    └── ...
```

同时会生成两个辅助文件：

| 文件 | 用途 |
|------|------|
| `comic_downloader_config.json` | 断点续爬进度 |
| `comic_downloader.log` | 运行日志 |
| `failure_logs/` | 失败记录（按日期） |

> **断点续爬**：程序中断后重新运行，会自动从中断的章节继续，不需要从头开始。

---

## 🔄 整体流程 / How It Works

```
┌──────────────────────────────────────────────────┐
│                                                  │
│  ① 解析本地 HTML                                  │
│     manhua_page.html                             │
│     ↓ 提取 data-ms、data-cs、章节名               │
│                                                  │
│  ② 调用 API（每章一次）                           │
│     GET api-get-v3.mgsearcher.com                │
│         /api/chapter/getinfo?m=38&c=1709038      │
│     ↓ 返回 JSON → 75 张图片的相对路径             │
│                                                  │
│  ③ 拼接 CDN 完整 URL                             │
│     f40-1-4.g-mh.online + /hp/chuanwu/...webp    │
│                                                  │
│  ④ 多线程并发下载（6 线程）                       │
│     ↓ 失败自动重试 3 轮                           │
│                                                  │
│  ⑤ 可选：Pillow 合并成一张长图                    │
│                                                  │
└──────────────────────────────────────────────────┘
```

### 和旧版（Selenium）的对比 / Before vs After

| | 旧版 Selenium | 新版纯 API |
|---|---|---|
| 依赖 | Python + WebDriver + 浏览器 | Python 三件套 |
| 单章耗时 | 30~60 秒 | 5~10 秒 |
| 图片完整性 | 依赖滚动触发，可能漏图 | API 返回全部，100% |
| 环境配置 | 下载匹配版本的 msedgedriver | `pip install` 完事 |
| 代码量 | ~610 行 | ~320 行 |
| 稳定性 | 浏览器崩溃需恢复机制 | HTTP 请求天然可重试 |

**核心思想**：与其用浏览器模拟人看漫画（加载页面 → 滚动 → 等懒加载），不如直接问服务器要数据。

---

## 📘 代码导读 / Code Walkthrough

> 本章适合想理解爬虫原理的读者。零基础可以先看 [逆向过程](#-逆向过程深度篇--reverse-engineering-deep-dive) 理解"为什么这样做"，再回来看"代码怎么写"。

### 整体结构 / Code Structure

```
ComicDownloader 类
├── __init__()              ← 初始化：会话、配置、速率控制
├── extract_chapter_links() ← 解析本地 HTML，提取章节信息
├── fetch_chapter_images()  ← 调 API 拿图片列表（核心）
├── build_image_urls()      ← 拼接 CDN 完整 URL
├── download_image_thread() ← 下载单张图片（线程安全）
├── merge_images()          ← 合并为长图
├── process_chapter()       ← 单章处理流程（串联以上步骤）
└── run()                   ← 主循环
```

### 核心流程伪代码 / Core Pseudocode

```python
def run():
    加载配置（断点续爬进度）
    解析 manhua_page.html → 提取 [漫画ID, 章节ID, 章节名]

    for 每个章节:
        process_chapter(章节)

def process_chapter(章节):
    # 步骤①：调 API
    info = GET https://api-get-v3.mgsearcher.com/api/chapter/getinfo
           ?m={漫画ID}&c={章节ID}
           + headers: Accept: application/json, Referer: 网站域名

    # 步骤②：拼 URL
    line = info.images.line   # → 2
    host = "f40-1-4.g-mh.online"  # line=2 时；否则 t40-1-4
    urls = [host + img.url for img in info.images.images]

    # 步骤③：并发下载
    for retry in 1..3:                    # 失败重试 3 轮
        线程池(6个线程).map(download, urls)
        urls = 失败的URL列表               # 下一轮重试这些

    # 步骤④：保存进度
    save_config(章节名, 已下载图片数)
```

### 为什么要写这些代码？/ Design Decisions

**1. `requests.Session()` + 连接池**

```python
adapter = HTTPAdapter(pool_connections=12, pool_maxsize=24)
self.session.mount("https://", adapter)
```

复用 TCP 连接，6 个线程同时下载时不用每次握手。单机下载速度能跑满带宽。

**2. `adaptive_delay()` + `_adjust_delay()`**

```
成功 1 次 → 延迟 × 0.9（越来越快，最少 0.1s）
失败 1 次 → 延迟 × 1.5（越来越慢，最多 2.0s）
```

服务端压力大时自动降速；恢复正常后自动提速。加锁保证线程安全。

**3. 断点续爬 / Resume**

```
comic_downloader_config.json:
{
  "last_chapter": "第472话 ...",   ← 上次下载到的章节名
  "last_image": 30                ← 该章已下载到第 30 张
}
```

程序重启后，跳过已完成的章节，从中断处继续。按 `Ctrl+C` 正常退出会保存进度。

**4. 失败重试 / Retry**

```
第 1 轮：尝试下载全部图片
第 2 轮：只重试失败的
第 3 轮：同上
```

网络波动导致的个别失败不会影响整章。

---

## 🔍 逆向过程（深度篇） / Reverse Engineering Deep Dive

> 这是本文档的核心部分。我们将逐步演示如何从一个陌生网站中找到隐藏的 API。
>
> 你不需要事先理解任何名词。跟着操作即可。

### 准备工作 / Prerequisites

- 一个基于 Chromium 的浏览器（Chrome / Edge / Brave）
- 打开 **开发者工具**：按 `F12` 或 `Ctrl+Shift+I`

---

### 第一关：观察页面加载了什么 / Level 1: What Does the Page Load?

**操作步骤 / Steps：**

1. 打开任意一个漫画章节页面，例如：
   ```
   https://manhuafree.com/manga/chuanwu-gkgongfang/38-056733070-499
   ```

2. 按 `F12` 打开开发者工具，切换到 **Network（网络）** 面板。

3. **刷新页面**（`F5` 或 `Ctrl+R`）。

你会看到几十到上百个请求飞过。这些就是页面加载时浏览器发出的所有网络请求。

**观察重点 / What to look for：**

- `Doc` 类型：HTML 页面本身（第一个请求）
- `XHR` / `Fetch` 类型：JS 发出的异步请求 ← **API 通常在这里**
- `Img` 类型：图片请求
- `JS` 类型：JavaScript 文件

> 💡 **核心认知**：浏览器看到的所有内容（图片、文字、视频）都是通过"网络请求"获取的。我们要找的就是那个"告诉浏览器图片在哪里"的请求。

---

### 第二关：过滤出 API 请求 / Level 2: Filter for API Requests

**操作步骤 / Steps：**

1. 在 Network 面板中，点击 **Fetch/XHR** 过滤器（只显示 JS 发起的请求）：

   ```
   [All] [Doc] [CSS] [JS] [XHR/Fetch] ← 点这个
                         ^^^^^^^^^
   ```

   这时候面板应该只剩下少数几个请求。

2. 逐个点击每个请求，在右侧 **Preview（预览）** 标签中查看返回内容。

3. 寻找一个返回内容包含大量图片路径的请求。在 `manhuafree.com` 这个站上，你会找到一个请求：
   ```
   getinfo?m=38&c=1709038
   ```
   点击它，查看 **Preview**：

   ```json
   {
     "data": {
       "info": {
         "images": {
           "images": [
             {"order": 0, "url": "/hp/chuanwu/499/0_xxx.webp"},
             {"order": 1, "url": "/hp/chuanwu/499/1_xxx.webp"},
             ...
           ]
         }
       }
     }
   }
   ```

> 🎉 **这就是我们要找的 API！** 它用 JSON 格式列出了本章所有图片的路径。

**记录 API 信息 / Take notes：**

| 项目 | 值 |
|------|-----|
| 完整 URL | `https://api-get-v3.mgsearcher.com/api/chapter/getinfo?m=38&c=1709038` |
| 参数 `m` | `38`（漫画 ID） |
| 参数 `c` | `1709038`（章节 ID） |

---

### 第三关：找到参数来源 / Level 3: Where Do `m` and `c` Come From?

现在我们知道 API 需要两个参数：`m`（漫画ID）和 `c`（章节ID）。但这两个数字从哪来的？

**推论**：它们一定藏在页面的某个地方，因为浏览器需要知道它们才能发请求。

**操作步骤 / Steps：**

1. 在章节页面 **右键 → 查看网页源代码**（`Ctrl+U`）。

2. 搜索 `data-ms` 或 `data-cs`（`Ctrl+F`），你应该能找到：

   ```html
   <div id="chapterContent"
        data-ms="38"
        data-cs="1709038"
        data-ct="第472话 ..."
        data-ch="/">
   ```

> `data-*` 属性是 HTML 中嵌入数据的标准方式。JS 代码在运行时读取这些值，构造 API 请求。

3. 那章节列表呢？打开章节列表页 `https://manhuafree.com/chapterlist/chuanwu-gkgongfang`，查看源代码，搜索 `data-cs`：

   ```html
   <a href="/manga/chuanwu-gkgongfang/38-056733070-499"
      data-ms="38"
      data-cs="1709038"
      data-ct="第472话 ...">
   ```

> **每个章节链接都自带 `data-ms` 和 `data-cs`！** 所以只需要解析章节列表页的 HTML，不需要为每个章节单独访问页面。

---

### 第四关：请求头伪装 — 绕过 Cloudflare / Level 4: Header Disguise

现在我们知道 API 地址和参数了。试试直接访问：

```bash
curl "https://api-get-v3.mgsearcher.com/api/chapter/getinfo?m=38&c=1709038"
```

结果：**403 Forbidden** ❌

网站使用了 **Cloudflare** 保护，它识别出这不是浏览器的请求，于是拒绝。

**解决方案**：模拟浏览器的请求头。

在开发者工具中，点击那个 API 请求，切换到 **Headers** 标签，找到 **Request Headers** 部分。复制以下三项：

| Header | 值示例 | 作用 |
|--------|--------|------|
| `Accept` | `application/json` | 告诉服务器"我要 JSON" |
| `Referer` | `https://manhuafree.com/` | 告诉服务器"我从漫画站来的" |
| `User-Agent` | `Mozilla/5.0 ... Chrome/120.0 ...` | 告诉服务器"我是 Chrome 浏览器" |

加上这些头后：

```bash
curl "https://api-get-v3.mgsearcher.com/api/chapter/getinfo?m=38&c=1709038" \
  -H "Accept: application/json" \
  -H "Referer: https://manhuafree.com/" \
  -H "User-Agent: Mozilla/5.0 ... Chrome/120.0 ..."
```

结果：**200 OK** ✅ —— 返回完整 JSON！

> 💡 **核心认知**：HTTP 请求头是服务器判断"你是谁"的依据。Cloudflare 看到 `curl` 的默认 User-Agent 就会拦截，但看到模拟的 Chrome User-Agent 加上正确的 Referer 就会放行。这不是漏洞，而是基本的反爬策略。

---

### 第五关：图片 URL 的完整路径 / Level 5: Complete Image URLs

API 返回的图片路径是 **相对路径**：

```json
{ "url": "/hp/chuanwu/499/0_xxx.webp" }
```

但完整的图片 URL 需要一个域名。这个域名从哪来？

**方法一：看 JS 源码**

在 Network 面板中，找到负责渲染图片的 JS 文件。搜索 `g-mh.online` 或查看 JS 文件中如何拼接 URL。在这个网站上，JS 文件 `getChapter.xxx.js` 中有一段：

```javascript
const r = e?.line === 2
  ? "https://f40-1-4.g-mh.online"
  : "https://t40-1-4.g-mh.online";
```

图片的完整 URL = `f40-1-4.g-mh.online` + `/hp/chuanwu/499/0_xxx.webp`

其中 `line` 来自 API 返回的 JSON：`"line": 2`

**方法二：直接在页面上检查**

1. 打开章节页面，等图片加载完
2. 在任意一张漫画图片上 **右键 → 在新标签页打开图片**
3. 地址栏就是完整 URL

> 💡 **核心认知**：API 返回相对路径是为了灵活切换 CDN。不同漫画、不同清晰度可能走不同的 CDN 域名。`line` 字段就是用来区分这些情况的。

---

### 第六关：图片下载也要伪装 / Level 6: Image CDN Headers

图片 CDN 同样做了保护：

```bash
# 不加请求头 → 403
curl "https://f40-1-4.g-mh.online/hp/chuanwu/499/0_xxx.webp"
# → 403 Forbidden

# 加上正确的请求头 → 200
curl "..." \
  -H "Accept: image/webp,image/*" \
  -H "Referer: https://manhuafree.com/" \
  -H "User-Agent: Mozilla/5.0 ... Chrome/120.0 ..."
# → 200 OK + 80KB 图片数据
```

---

### 逆向总结 / Reverse Engineering Summary

```
浏览器中的操作                          →  爬虫中的对应代码
═══════════════════════════════════════════════════════════
打开章节列表页，查看源代码              →  requests.get() + BeautifulSoup
找到 data-ms / data-cs                 →  解析 HTML 属性
                                        →
看 Network 面板 XHR 请求               →  找到 API URL 模式
复制 API 返回的 JSON                   →  resp.json()
                                        →
找到 JS 中图片 URL 拼接逻辑             →  build_image_urls()
复制浏览器请求头                        →  API_HEADERS / IMG_HEADERS
                                        →
浏览器自动下载显示图片                  →  requests.get(url, stream=True)
```

整个过程本质上就是把浏览器做的事，用 Python 代码重做一遍。区别在于我们只取数据，不渲染 UI。

---

## ⚙️ 配置说明 / Configuration

### 修改漫画 / Change Target Manga

编辑 `2传武爬取.py` 顶部 `ComicDownloader` 类的类常量：

```python
COMIC_NAME = "传武"           # 改成目标漫画名（影响目录名）
BASE_URL = "https://manhuafree.com/"
```

同时准备对应的章节列表 HTML。

### 调整下载速度 / Tune Download Speed

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `MAX_WORKERS` | `6` | 并发下载线程数。不要超过 10，容易被封 IP |
| `MIN_REQUEST_INTERVAL` | `0.2` | 请求最小间隔（秒） |
| `INITIAL_DYNAMIC_DELAY` | `0.5` | 初始延迟（秒），会根据成功率自动调整 |
| `MAX_DOWNLOAD_RETRY` | `3` | 失败重试轮数 |
| `MAX_RETRY_API` | `3` | API 请求失败重试次数 |

### 如何获取章节列表 HTML / Getting the Chapter List

> ⚠️ 当前版本需要手动保存 HTML。未来计划改为自动获取。

**当前做法**：
1. 浏览器打开 `https://manhuafree.com/chapterlist/chuanwu-gkgongfang`
2. 等待页面加载完毕，滚动到底部确保所有章节加载
3. `Ctrl+S` 保存网页，选择"网页，仅 HTML"
4. 将保存的文件放到项目目录，默认命名为 `manhua_page.html`

**替代方案**：使用浏览器的"复制 outerHTML"功能：
1. F12 打开开发者工具
2. 点击左上角的元素选择器（或按 `Ctrl+Shift+C`）
3. 点击页面上的章节列表区域
4. 在 Elements 面板中右键高亮的元素 → Copy → Copy outerHTML
5. 粘贴到文本编辑器，保存为 `.html`

---

## ❓ 常见问题 / FAQ

<details>
<summary><b>Q: 为什么我运行后只有几章能下载？</b></summary>

检查 `manhua_page.html` 是否包含了全部章节。如果保存时没有滚动到底部，HTML 里可能只有前面几章的数据。重新保存时确保完整加载。（后期会改为自动获取，无需手动操作。）
</details>

<details>
<summary><b>Q: 提示 <code>ModuleNotFoundError: No module named 'xxx'</code></b></summary>

缺少依赖库。运行以下命令安装全部依赖：

```bash
pip install requests beautifulsoup4 Pillow
```
</details>

<details>
<summary><b>Q: API 返回 403 Forbidden 怎么办？</b></summary>

网站可能更新了防护策略。按 [逆向过程第四关](#第四关请求头伪装--绕过-cloudflare-level-4-header-disguise) 的步骤重新检查请求头是否需要更新。（提 Issue 也可以。）
</details>

<details>
<summary><b>Q: 下载的图片打不开？</b></summary>

图片是 `.webp` 格式，Windows 默认照片查看器可能不支持。用 Chrome 浏览器直接拖入查看，或安装 [WebP Codec](https://www.microsoft.com/store/productId/9PG2DK419DRG)。
</details>

<details>
<summary><b>Q: 能下载其他漫画吗？</b></summary>

可以。只要目标网站使用相同的 API 服务（`mgsearcher`），修改 `COMIC_NAME` 并准备对应的章节列表 HTML 即可。API 域名和参数结构可能不同，需要重新逆向。
</details>

<details>
<summary><b>Q: 会被封 IP 吗？</b></summary>

当前配置已经做了保护措施：
- 请求间隔控制（最小 0.2s）
- 章节间 2~5s 随机延迟
- 自适应降速（服务器压力大时自动减速）

如果你仍然遇到封 IP，可以调大 `MIN_REQUEST_INTERVAL` 和 `INITIAL_DYNAMIC_DELAY`。
</details>

---

## ⚖️ 免责声明 / Disclaimer

> **中文**
>
> 本项目仅供学习交流使用，旨在演示 Python 网络爬虫技术，包括 HTTP 请求分析、API 逆向、并发下载等知识点。
>
> - 请勿将本项目用于商业用途
> - 请勿大量下载以规避网站带宽压力
> - 下载的图片请在 24 小时内删除
> - 请支持正版漫画，尊重作者版权
> - 使用者需自行承担一切法律责任
>
> **English**
>
> This project is for educational purposes only. It demonstrates Python web scraping techniques including HTTP request analysis, API reverse engineering, and concurrent downloading.
>
> - Do not use for commercial purposes
> - Do not download at scale to avoid straining the website
> - Delete downloaded images within 24 hours
> - Support the original creators
> - Users assume all legal responsibility

---

## 📄 License

MIT — 你可以自由使用、修改、分发，但需保留原始许可声明。
