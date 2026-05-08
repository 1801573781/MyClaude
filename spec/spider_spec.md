# 需求文档：通用网页爬虫模块

## 1. 背景与目标
需要为 MyClaude 项目增加一个独立的爬虫工具脚本，用于抓取静态网页的标题和正文链接，输出结构化 JSON。要求代码简洁、可单文件运行、不依赖复杂框架。

## 2. 功能范围（In Scope）
- [1] 支持命令行传入目标 URL
- [2] 使用 `requests` 发送 GET 请求，自定义 User-Agent 防屏蔽
- [3] 使用 `BeautifulSoup4` 解析 HTML
- [4] 提取：页面标题 `<title>`、所有 `<a>` 标签的文本和 href、所有 `<h1>`~`<h3>` 标题
- [5] 输出为 JSON 格式，文件名基于域名和时间戳，由调用方通过 `--output` 参数指定保存目录
- [6] 基础错误处理：网络超时、HTTP 4xx/5xx、编码异常

## 3. 非功能需求
- 单文件实现：`spider.py`，不超过 150 行
- 不引入 Scrapy、Selenium 等重型框架
- 遵守 `robots.txt` 精神，默认请求间隔 1 秒（本示例目标为公开测试站，无需严格限速，但代码里预留 `time.sleep` 位置）

## 4. 接口契约与代码结构

## 5. 文件命名与路径强制约定
- **代码文件必须命名为** `spider.py`
- **存放路径**：`D:/AI/MyClaude/src/tools/spider.py`（如果 `src/tools/` 目录不存在，请先创建）
- **测试文件必须命名为** `test_spider.py`，存放在同一目录 `D:/AI/MyClaude/src/tools/`

## 6. 自动化闭环要求（强制）
生成 `spider.py` 后，必须自动执行以下闭环，直到全部通过：
1. 使用 `add_tests` 技能（若不存在则按规范生成）为 `spider.py` 创建单元测试文件 `test_spider.py`，覆盖主要功能（请求、解析、错误处理、输出）。
2. 运行 `pytest` 执行测试。
3. 若测试失败：
   - 分析失败原因（区分是测试代码缺陷还是 `spider.py` 缺陷）
   - **仅修改 `spider.py`** 修复缺陷（不得修改测试代码），保存后重新运行测试。
   - 重复此步骤直到所有测试通过。
4. 全部通过后输出 `<done>`。

### 命令行入口
```bash
    python spider.py --url "https://quotes.toscrape.com/" --output {输出目录}




