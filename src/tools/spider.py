#!/usr/bin/env python3
"""通用网页爬虫模块 — 单文件实现，抓取静态网页标题与正文链接，输出结构化 JSON。"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_TIMEOUT = 10
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def build_filename(url: str) -> str:
    """基于域名和时间戳生成输出文件名。"""
    domain = urlparse(url).netloc.replace(":", "_").replace(".", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{domain}_{timestamp}.json"


def fetch_html(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """
    发送 GET 请求并返回响应文本。
    对网络超时、HTTP 错误状态码统一抛出异常，由上层处理。
    """
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse_content(html: str) -> dict:
    """从 HTML 中提取页面标题、h1~h3 标题、所有 <a> 链接。"""
    soup = BeautifulSoup(html, "html.parser")

    # 页面标题
    page_title = ""
    title_tag = soup.find("title")
    if title_tag:
        page_title = title_tag.get_text(strip=True)

    # h1~h3 标题
    headings = []
    for level in ("h1", "h2", "h3"):
        for tag in soup.find_all(level):
            text = tag.get_text(strip=True)
            if text:
                headings.append({"level": level, "text": text})

    # 所有 <a> 标签
    links = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        links.append({"text": text if text else "", "href": a["href"]})

    return {
        "page_title": page_title,
        "headings": headings,
        "links": links,
    }


def save_json(data: dict, output_dir: str, filename: str) -> Path:
    """将数据写入 JSON 文件，返回写入路径。"""
    out_path = Path(output_dir) / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return out_path


def crawl(url: str, output_dir: str) -> Path:
    """
    执行完整爬取流程：
    1. 请求页面
    2. 解析 HTML
    3. 保存 JSON
    返回输出文件路径。
    """
    html = fetch_html(url)
    # 预留请求间隔位置（如需严格限速可在此处添加 time.sleep(1)）
    time.sleep(0)
    data = parse_content(html)
    filename = build_filename(url)
    output_path = save_json(data, output_dir, filename)

    # 同时输出到 stdout，便于流水线调用
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return output_path


def main():
    parser = argparse.ArgumentParser(description="通用网页爬虫 — 抓取标题与链接")
    parser.add_argument("--url", required=True, help="目标网页 URL")
    parser.add_argument("--output", required=True, help="JSON 输出目录")
    args = parser.parse_args()

    try:
        output_path = crawl(args.url, args.output)
        print(f"\n[成功] 已保存至: {output_path}", file=sys.stderr)
    except requests.ConnectionError:
        print("[错误] 网络连接失败，请检查 URL 或网络状态", file=sys.stderr)
        sys.exit(1)
    except requests.Timeout:
        print(f"[错误] 请求超时（>{DEFAULT_TIMEOUT}s），请稍后重试", file=sys.stderr)
        sys.exit(1)
    except requests.HTTPError as e:
        print(f"[错误] HTTP 错误: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[错误] 未知异常: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()