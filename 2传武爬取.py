# -*- coding: utf-8 -*-
"""
漫画爬虫 - 传武漫画下载器（API直连版）
通过逆向 API 直接获取图片链接，无需 Selenium 浏览器驱动
"""

import sys
import json
import time
import random
import logging
import argparse
import threading
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("comic_downloader.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


class ComicDownloader:
    """漫画下载器 — 通过 API 获取图片，无需浏览器"""

    # ---- 常量 ----
    BASE_URL = "https://manhuafree.com/"
    API_BASE = "https://api-get-v3.mgsearcher.com"
    COMIC_NAME = "传武"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    DOWNLOAD_DIR = Path("comic")
    CONFIG_FILE = Path("comic_downloader_config.json")
    FAILURE_LOG_DIR = Path("failure_logs")

    MAX_RETRY_API = 3
    MAX_DOWNLOAD_RETRY = 3
    MAX_WORKERS = 6

    MIN_REQUEST_INTERVAL = 0.2
    INITIAL_DYNAMIC_DELAY = 0.5
    MIN_DYNAMIC_DELAY = 0.1
    MAX_DYNAMIC_DELAY = 2.0

    HEADERS = {
        "User-Agent": USER_AGENT,
        "Referer": BASE_URL,
    }
    API_HEADERS = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "Referer": BASE_URL,
    }
    IMG_HEADERS = {
        "Accept": "image/webp,image/*",
        "User-Agent": USER_AGENT,
        "Referer": BASE_URL,
    }

    # ------------------------------------------------------------------
    def __init__(self, input_file: str = "manhua_page.html"):
        self.input_file = input_file

        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(self.HEADERS)
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=self.MAX_WORKERS * 2,
            pool_maxsize=self.MAX_WORKERS * 4,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.config: dict = {
            "last_chapter": None,
            "last_image": 0,
            "merge_images": False,
            "failure_records": [],
        }
        self.current_chapter: str | None = None

        self._delay_lock = threading.Lock()
        self.last_request_time = 0.0
        self.dynamic_delay = self.INITIAL_DYNAMIC_DELAY

        self.FAILURE_LOG_DIR.mkdir(exist_ok=True)
        self._load_config()

    # ==================================================================
    # 配置 & 日志
    # ==================================================================

    def _load_config(self):
        if self.CONFIG_FILE.exists():
            try:
                saved = json.loads(self.CONFIG_FILE.read_text(encoding="utf-8"))
                saved["failure_records"] = (
                    self.config["failure_records"] + saved.get("failure_records", [])
                )
                self.config.update(saved)
            except Exception as e:
                logger.warning("配置文件损坏: %s", e)
                self._log_failure("config_error", error_msg=e)

    def save_config(self, chapter=None, image=None):
        if chapter:
            self.config["last_chapter"] = chapter
        if image is not None:
            self.config["last_image"] = image
        try:
            self.CONFIG_FILE.write_text(
                json.dumps(self.config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("无法保存配置: %s", e)
            self._log_failure("save_config_error", error_msg=e)

    def _log_failure(self, error_type, chapter=None, image_url=None, error_msg=None):
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error_type": error_type,
            "chapter": chapter or self.current_chapter,
            "image_url": image_url,
            "error_msg": str(error_msg)[:500] if error_msg else None,
        }
        self.config["failure_records"].append(entry)

        log_file = self.FAILURE_LOG_DIR / f"failures_{datetime.now():%Y%m%d}.json"
        try:
            existing = (
                json.loads(log_file.read_text(encoding="utf-8"))
                if log_file.exists()
                else []
            )
            existing.append(entry)
            log_file.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("无法写入日志文件: %s", e)

    # ==================================================================
    # 速率控制
    # ==================================================================

    def adaptive_delay(self):
        with self._delay_lock:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.MIN_REQUEST_INTERVAL:
                time.sleep(self.MIN_REQUEST_INTERVAL - elapsed)
            self.last_request_time = time.time()

    def _adjust_delay(self, success: bool):
        with self._delay_lock:
            if success:
                self.dynamic_delay = max(self.dynamic_delay * 0.9, self.MIN_DYNAMIC_DELAY)
            else:
                self.dynamic_delay = min(self.dynamic_delay * 1.5, self.MAX_DYNAMIC_DELAY)

    # ==================================================================
    # 章节列表提取
    # ==================================================================

    def extract_chapter_links(self):
        """从本地 HTML 提取章节的 data-ms、data-cs 和名称"""
        try:
            html = Path(self.input_file).read_text(encoding="utf-8")
            soup = BeautifulSoup(html, "html.parser")
            chapters = []
            for item in soup.select(r'div.chapteritem.w-full.md\:w-1\/2 a'):
                name = item.select_one("span.chaptertitle").get_text(strip=True)
                if name == "公告":
                    continue
                ms = item.get("data-ms")
                cs = item.get("data-cs")
                href = item.get("href")
                if ms and cs:
                    chapters.append({"name": name, "ms": int(ms), "cs": int(cs), "href": href})
            return chapters
        except Exception as e:
            logger.error("提取章节链接出错: %s", e)
            self._log_failure("extract_chapters_failed", error_msg=e)
            return []

    # ==================================================================
    # API 调用
    # ==================================================================

    def fetch_chapter_images(self, ms: int, cs: int) -> dict | None:
        """通过 API 获取章节图片信息"""
        url = f"{self.API_BASE}/api/chapter/getinfo?m={ms}&c={cs}"
        for attempt in range(self.MAX_RETRY_API):
            try:
                self.adaptive_delay()
                resp = self.session.get(url, headers=self.API_HEADERS, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") and data.get("data", {}).get("info", {}).get("images"):
                    return data["data"]["info"]
                logger.warning("API 返回数据异常 (attempt %d)", attempt + 1)
            except Exception as e:
                logger.warning("API 请求失败 (attempt %d): %s", attempt + 1, e)
                if attempt < self.MAX_RETRY_API - 1:
                    time.sleep(self.dynamic_delay * (attempt + 1))
        return None

    @staticmethod
    def build_image_urls(info: dict) -> list:
        """根据 API 返回的 info 构造完整图片 URL 列表"""
        images = info.get("images", {}).get("images", [])
        line = info.get("images", {}).get("line", 2)
        host = "https://f40-1-4.g-mh.online" if line == 2 else "https://t40-1-4.g-mh.online"
        return [host + img["url"] for img in sorted(images, key=lambda x: x["order"])]

    # ==================================================================
    # 图片下载
    # ==================================================================

    def download_image_thread(self, img_url: str, save_dir: Path, index: int, attempt: int = 1):
        """下载单张图片（线程安全）"""
        try:
            self.adaptive_delay()

            resp = self.session.get(
                img_url, headers=self.IMG_HEADERS, stream=True, timeout=20
            )
            resp.raise_for_status()

            # 从 URL 路径获取扩展名
            path_part = img_url.split("?")[0]
            ext = path_part.rsplit(".", 1)[-1].lower() if "." in path_part else "webp"
            if ext not in ("jpg", "jpeg", "png", "webp", "bmp"):
                ct = resp.headers.get("Content-Type", "")
                if "jpeg" in ct or "jpg" in ct:
                    ext = "jpg"
                elif "png" in ct:
                    ext = "png"
                elif "webp" in ct:
                    ext = "webp"
                else:
                    ext = "webp"

            save_path = save_dir / f"{index}.{ext}"
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)

            self._adjust_delay(success=True)
            return True, img_url

        except Exception as e:
            if attempt < 2:
                time.sleep(self.dynamic_delay)
                return self.download_image_thread(img_url, save_dir, index, attempt + 1)
            self._adjust_delay(success=False)
            self._log_failure("image_download_failed", image_url=img_url, error_msg=e)
            return False, img_url

    # ==================================================================
    # 图片合并
    # ==================================================================

    @staticmethod
    def merge_images(image_paths, output_path):
        """垂直合并多张图片为一张长图"""
        images = []
        for p in image_paths:
            try:
                img = Image.open(p)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                images.append(img)
            except Exception as e:
                logger.warning("无法打开图片 %s: %s", p, e)

        if not images:
            return False

        widths = [img.width for img in images]
        total_height = sum(img.height for img in images)
        merged = Image.new("RGB", (max(widths), total_height))

        y_offset = 0
        for img in images:
            merged.paste(img, (0, y_offset))
            y_offset += img.height

        merged.save(output_path, "JPEG", quality=95)
        for img in images:
            img.close()
        return True

    # ==================================================================
    # 章节处理
    # ==================================================================

    def process_chapter(self, chapter: dict, start_from: int = 0) -> bool:
        """处理单个章节：调 API → 下载图片"""
        name = chapter["name"]
        self.current_chapter = name
        logger.info("开始处理章节: %s", name)

        # 1. 调 API 获取图片列表
        info = self.fetch_chapter_images(chapter["ms"], chapter["cs"])
        if not info:
            logger.error("获取章节图片信息失败: %s", name)
            self._log_failure("api_fetch_failed")
            return False

        img_urls = self.build_image_urls(info)
        total = len(img_urls)
        logger.info("API 返回 %d 张图片", total)

        if total == 0:
            logger.warning("章节 %s 没有图片", name)
            return False

        # 2. 断点续爬
        if start_from > 0:
            logger.info("从第 %d 张继续", start_from)
            img_urls = img_urls[start_from - 1:]

        # 3. 创建目录
        chapter_dir = self.DOWNLOAD_DIR / self.COMIC_NAME / name
        chapter_dir.mkdir(parents=True, exist_ok=True)

        # 4. 并发下载 + 失败重试
        start_idx = start_from if start_from > 0 else 1
        remaining = list(enumerate(img_urls))
        all_success = 0

        for retry_round in range(self.MAX_DOWNLOAD_RETRY):
            if not remaining:
                break
            if retry_round > 0:
                logger.info("重试 %d 张失败的图片 (第 %d 轮)...", len(remaining), retry_round)
                time.sleep(2)

            with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
                futures = {
                    executor.submit(
                        self.download_image_thread, url, chapter_dir, start_idx + idx
                    ): (idx, url)
                    for idx, url in remaining
                }
                remaining = []
                for future in as_completed(futures):
                    ok, _ = future.result()
                    if ok:
                        all_success += 1
                    else:
                        idx, url = futures[future]
                        remaining.append((idx, url))

        logger.info("章节 %s 下载完成: %d/%d", name, all_success, total)

        # 5. 图片合并
        if self.config["merge_images"] and all_success > 0:
            logger.info("开始合并图片...")
            merged_path = chapter_dir / f"{name}_merged.jpg"
            downloaded_files = sorted(chapter_dir.iterdir())
            if self.merge_images(
                [str(f) for f in downloaded_files if f.suffix in (".webp", ".jpg", ".png", ".bmp")],
                str(merged_path),
            ):
                logger.info("图片合并成功: %s", merged_path)

        # 6. 保存进度
        if all_success > 0:
            self.save_config(image=start_from + all_success if start_from > 0 else all_success)
        self.save_config(chapter=name)
        return True

    # ==================================================================
    # 主流程
    # ==================================================================

    def run(self):
        try:
            merge = input("是否要将图片合并为长图？(y/n, 默认n): ").strip().lower()
            self.config["merge_images"] = merge == "y"

            chapters = self.extract_chapter_links()
            if not chapters:
                logger.error("未找到有效章节，请检查输入文件: %s", self.input_file)
                return

            logger.info("共找到 %d 个章节", len(chapters))

            # 断点续爬
            start_index = 0
            if self.config["last_chapter"]:
                try:
                    start_index = next(
                        i for i, ch in enumerate(chapters)
                        if ch["name"] == self.config["last_chapter"]
                    )
                    logger.info("从章节 '%s' 继续", self.config["last_chapter"])
                except StopIteration:
                    logger.info("未找到上次章节，从头开始")

            for idx in range(start_index, len(chapters)):
                chapter = chapters[idx]
                logger.info("[%d/%d] 准备下载: %s", idx + 1, len(chapters), chapter["name"])

                start_img = self.config["last_image"] if idx == start_index else 0
                ok = self.process_chapter(chapter, start_img)

                if ok and idx < len(chapters) - 1:
                    delay = random.uniform(2, 5)
                    logger.info("等待 %.1f 秒后继续下一章...", delay)
                    time.sleep(delay)
                elif not ok:
                    logger.warning("章节 %s 处理失败，继续下一个", chapter["name"])

        except KeyboardInterrupt:
            logger.info("用户中断，保存进度后退出...")
            self._log_failure("user_interrupt")
        except Exception as e:
            logger.exception("主程序发生严重错误: %s: %s", type(e).__name__, e)
            self._log_failure("fatal_error", error_msg=e)
        finally:
            self.save_config()
            logger.info("程序终止，失败记录已保存到 %s", self.FAILURE_LOG_DIR)


# ======================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="传武漫画下载器 (API版)")
    parser.add_argument(
        "-i", "--input",
        default="manhua_page.html",
        help="章节列表 HTML 文件路径 (默认 manhua_page.html)",
    )
    args = parser.parse_args()
    ComicDownloader(input_file=args.input).run()
