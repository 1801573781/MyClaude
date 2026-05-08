import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from spider import (
    build_headers,
    parse_html,
    generate_filename,
    save_json,
    run,
    fetch_html,
)


# ========== build_headers ==========
def test_build_headers_returns_dict():
    """正常路径：返回包含 User-Agent 的字典"""
    headers = build_headers()
    assert isinstance(headers, dict)
    assert "User-Agent" in headers
    assert "Mozilla" in headers["User-Agent"]


# ========== parse_html ==========
def test_parse_html_title():
    """正常路径：提取页面标题"""
    html = "<html><head><title>Test Page</title></head><body></body></html>"
    result = parse_html(html)
    assert result["title"] == "Test Page"


def test_parse_html_title_stripped():
    """标题前后有空白字符"""
    html = "<html><head><title>  Hello World  </title></head><body></body></html>"
    result = parse_html(html)
    assert result["title"] == "Hello World"


def test_parse_html_no_title():
    """无 title 标签"""
    html = "<html><head></head><body></body></html>"
    result = parse_html(html)
    assert result["title"] == ""


def test_parse_html_empty_title():
    """title 标签存在但内容为空"""
    html = "<html><head><title></title></head><body></body></html>"
    result = parse_html(html)
    assert result["title"] == ""


def test_parse_html_links():
    """正常路径：提取所有 <a> 标签"""
    html = """
    <html><body>
        <a href="/page1">Page 1</a>
        <a href="/page2">Page 2</a>
        <a>No href</a>
    </body></html>
    """
    result = parse_html(html)
    assert len(result["links"]) == 3
    assert result["links"][0] == {"text": "Page 1", "href": "/page1"}
    assert result["links"][1] == {"text": "Page 2", "href": "/page2"}
    assert result["links"][2] == {"text": "No href", "href": ""}


def test_parse_html_headings():
    """正常路径：提取 h1~h3 标题"""
    html = """
    <html><body>
        <h1>Main Title</h1>
        <h2>Sub Title</h2>
        <h3>Section</h3>
        <h4>Not Included</h4>
    </body></html>
    """
    result = parse_html(html)
    assert len(result["headings"]) == 3
    assert result["headings"][0] == {"level": "h1", "text": "Main Title"}
    assert result["headings"][1] == {"level": "h2", "text": "Sub Title"}
    assert result["headings"][2] == {"level": "h3", "text": "Section"}


def test_parse_html_empty_headings():
    """边界条件：空 h1 标签不被收录"""
    html = """
    <html><body>
        <h1></h1>
        <h2>Valid</h2>
    </body></html>
    """
    result = parse_html(html)
    assert len(result["headings"]) == 1
    assert result["headings"][0]["text"] == "Valid"


def test_parse_html_empty_links_filtered():
    """边界条件：text 和 href 都为空的 <a> 不应被收录"""
    html = '<html><body><a></a><a href="/only-href">link</a></body></html>'
    result = parse_html(html)
    # 第一个 a 的 text="" 且 href=""，不应加入
    assert len(result["links"]) == 1
    assert result["links"][0] == {"text": "link", "href": "/only-href"}


# ========== generate_filename ==========
def test_generate_filename_format():
    """正常路径：基于域名和时间戳生成文件名"""
    url = "https://www.example.com/page"
    filename = generate_filename(url)
    # 格式：www_example_com_YYYYMMDD_HHMMSS.json
    assert filename.startswith("www_example_com_")
    assert filename.endswith(".json")


def test_generate_filename_no_hostname():
    """边界条件：URL 无 hostname"""
    url = "invalid-url"
    filename = generate_filename(url)
    assert filename.startswith("unknown_")
    assert filename.endswith(".json")


# ========== save_json ==========
def test_save_json_creates_file():
    """正常路径：保存 JSON 到指定目录"""
    data = {"key": "value", "list": [1, 2, 3]}
    output_dir = Path(tempfile.mkdtemp())
    filename = "test_output.json"
    try:
        saved_path = save_json(data, output_dir, filename)
        assert saved_path.exists()
        with open(saved_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == data
    finally:
        # 清理临时文件
        saved_path.unlink(missing_ok=True)
        output_dir.rmdir()


def test_save_json_creates_dir():
    """边界条件：输出目录不存在时自动创建"""
    data = {"a": 1}
    base_temp = Path(tempfile.mkdtemp())
    output_dir = base_temp / "nested" / "subdir"
    filename = "deep.json"
    try:
        saved_path = save_json(data, output_dir, filename)
        assert saved_path.exists()
    finally:
        # 清理
        import shutil
        shutil.rmtree(base_temp, ignore_errors=True)


# ========== fetch_html (使用 mock) ==========
@patch("tools.spider.requests.get")
def test_fetch_html_success(mock_get):
    """正常路径：成功获取 HTML"""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.text = "<html><title>Mock</title></html>"
    mock_resp.apparent_encoding = None
    mock_resp.encoding = "utf-8"
    mock_get.return_value = mock_resp

    html = fetch_html("https://example.com")
    assert "Mock" in html


@patch("tools.spider.requests.get")
def test_fetch_html_http_error(mock_get):
    """异常路径：HTTP 4xx/5xx"""
    import requests as req

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = req.exceptions.HTTPError("404 Not Found")
    mock_get.return_value = mock_resp

    with pytest.raises(RuntimeError, match="HTTP 错误"):
        fetch_html("https://example.com/404")


@patch("tools.spider.requests.get")
def test_fetch_html_timeout(mock_get):
    """异常路径：请求超时"""
    import requests as req

    mock_get.side_effect = req.exceptions.Timeout("timeout")

    with pytest.raises(RuntimeError, match="请求超时"):
        fetch_html("https://example.com")


@patch("tools.spider.requests.get")
def test_fetch_html_connection_error(mock_get):
    """异常路径：无法连接"""
    import requests as req

    mock_get.side_effect = req.exceptions.ConnectionError("refused")

    with pytest.raises(RuntimeError, match="无法连接"):
        fetch_html("https://example.com")


# ========== run (端到端，mock 网络) ==========
@patch("tools.spider.time.sleep", return_value=None)
@patch("tools.spider.requests.get")
def test_run_success(mock_get, mock_sleep):
    """端到端测试：完整流程成功"""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.text = "<html><head><title>E2E</title></head><body><a href='/a'>Link A</a></body></html>"
    mock_resp.apparent_encoding = None
    mock_resp.encoding = "utf-8"
    mock_get.return_value = mock_resp

    output_dir = tempfile.mkdtemp()
    try:
        result = run("https://example.com", output_dir)
        assert result["status"] == "ok"
        assert result["data"]["data"]["title"] == "E2E"
        assert len(result["data"]["data"]["links"]) == 1
        assert result["data"]["data"]["links"][0]["text"] == "Link A"
        assert "_meta" in result["data"]
        assert result["data"]["_meta"]["url"] == "https://example.com"
    finally:
        import shutil
        shutil.rmtree(output_dir, ignore_errors=True)