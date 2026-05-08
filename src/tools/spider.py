#!/usr/bin/env python3
"""
通用网页爬虫工具 - 抓取静态网页的标题、正文链接和标题标签，输出结构化 JSON。
使用方式：
    python spider.py --url "https://quotes.toscrape.com/" --output ./output
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 15
REQUEST_DELAY = 1.0   # 请求间隔（秒），预留位置


def build_headers() -> dict:
    """构造自定义 User-Agent 请求头。"""
    return {"User-Agent": DEFAULT_USER_AGENT}


def fetch_html(url: str) -> str:
    """
    发送 GET 请求获取 HTML 内容。
    返回解码后的文本字符串。
    """
    headers = build_headers()
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        raise RuntimeError(f"请求超时（{REQUEST_TIMEOUT}s）：{url}")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"HTTP 错误：{e}")
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"无法连接：{url}")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"请求异常：{e}")

    # 自动检测编码
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def parse_html(html: str) -> dict:
    """
    解析 HTML，提取结构化数据。
    返回字典：
        - title: 页面标题
        - links: [{"text": ..., "href": ...}, ...]
        - headings: [{"level": str, "text": ...}, ...]
    """
    soup = BeautifulSoup(html, "html.parser")

    # 页面标题
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # 所有 <a> 标签
    links = []
    for a_tag in soup.find_all("a"):
        text = a_tag.get_text(strip=True)
        href = a_tag.get("href", "")
        if text or href:
            links.append({"text": text, "href": href})

    # 所有 <h1>~<h3> 标题
    headings = []
    for level in ("h1", "h2", "h3"):
        for tag in soup.find_all(level):
            text = tag.get_text(strip=True)
            if text:
                headings.append({"level": level, "text": text})

    return {
        "title": title,
        "links": links,
        "headings": headings,
    }


def generate_filename(url: str) -> str:
    """基于域名和时间戳生成输出文件名。"""
    host = urlparse(url).hostname or "unknown"
    host = host.replace(".", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{host}_{timestamp}.json"


def save_json(data: dict, output_dir: Path, filename: str) -> Path:
    """将数据保存为 JSON 文件，返回保存路径。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return filepath


def run(url: str, output_dir: str) -> dict:
    """
    执行爬取流程：
    1. 请求 HTML
    2. 解析数据
    3. 保存 JSON
    返回结果字典（供测试断言使用）。
    """
    output_path = Path(output_dir)

    # 预留请求间隔
    time.sleep(REQUEST_DELAY)

    html = fetch_html(url)
    data = parse_html(html)
    data["_meta"] = {
        "url": url,
        "fetched_at": datetime.now().isoformat(),
    }

    filename = generate_filename(url)
    saved_path = save_json(data, output_path, filename)

    return {
        "status": "ok",
        "saved_path": str(saved_path),
        "data": data,
    }


def main():
    parser = argparse.ArgumentParser(
        description="通用网页爬虫 - 提取标题、链接和 h1~h3 标题，输出 JSON"
    )
    parser.add_argument(
        "--url", required=True, help="目标网页 URL"
    )
    parser.add_argument(
        "--output", required=True, help="JSON 输出目录"
    )
    args = parser.parse_args()

    try:
        result = run(args.url, args.output)
        print(f"[OK] 爬取成功 → {result['saved_path']}")
        print(f"  标题: {result['data']['title']}")
        print(f"  链接数: {len(result['data']['links'])}")
        print(f"  标题标签数: {len(result['data']['headings'])}")
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()