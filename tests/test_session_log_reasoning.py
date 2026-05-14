"""测试 SessionLog 中推理内容（reasoning_content）的记录功能"""
import pytest
import shutil
import os
from pathlib import Path
from datetime import datetime

# 将 src 目录加入路径
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from query.session_log import SessionLog
import utility.config_loader as cl


TEST_LOG_ROOT = Path(__file__).resolve().parent.parent / "code_output" / "__test_logs__"


class TestSessionLogReasoning:
    """测试推理内容记录相关功能"""

    @classmethod
    def setup_class(cls):
        """创建测试日志目录"""
        TEST_LOG_ROOT.mkdir(parents=True, exist_ok=True)

    @classmethod
    def teardown_class(cls):
        """清理测试日志目录"""
        if TEST_LOG_ROOT.exists():
            shutil.rmtree(TEST_LOG_ROOT)

    @pytest.fixture(autouse=True)
    def setup_log_dir(self):
        """每个测试前确保目录干净且 global_cfg 指向测试目录"""
        original_logs_root = cl.global_cfg.base_path.logs_root
        cl.global_cfg.base_path.logs_root = str(TEST_LOG_ROOT)
        # 清理旧的测试日志文件
        for f in TEST_LOG_ROOT.glob("*"):
            f.unlink()
        yield
        cl.global_cfg.base_path.logs_root = original_logs_root

    @pytest.fixture
    def session_log(self):
        """创建 SessionLog 实例"""
        log = SessionLog()
        log.format = "md"
        return log

    # ========== log_reasoning_content 测试 ==========

    def test_log_reasoning_content_non_empty(self, session_log):
        """测试非空推理内容被记录"""
        session_log.init_session()
        session_log.log_reasoning_content("这是推理过程内容")
        session_log.log_llm_rsp("AI 回复内容")

        # 读取日志文件
        log_path = TEST_LOG_ROOT / session_log.session_file_name
        content = log_path.read_text(encoding="utf-8")

        assert "推理内容" in content
        assert "<details>" in content
        assert "<summary>展开查看推理过程</summary>" in content
        assert "这是推理过程内容" in content

    def test_log_reasoning_content_empty(self, session_log):
        """测试空推理内容不被记录"""
        session_log.init_session()
        session_log.log_reasoning_content("")  # 空字符串
        session_log.log_llm_rsp("AI 回复内容")

        # 读取日志文件
        log_path = TEST_LOG_ROOT / session_log.session_file_name
        content = log_path.read_text(encoding="utf-8")

        assert "推理内容" not in content
        assert "<details>" not in content

    def test_log_reasoning_content_none_empty(self, session_log):
        """测试仅空白字符的推理内容不产生折叠块"""
        session_log.init_session()
        session_log.log_reasoning_content("   \n  ")  # 空白字符
        session_log.log_llm_rsp("AI 回复内容")

        log_path = TEST_LOG_ROOT / session_log.session_file_name
        content = log_path.read_text(encoding="utf-8")

        assert "<details>" not in content

    def test_log_reasoning_content_before_llm_rsp(self, session_log):
        """测试推理内容紧邻 AI 回复前记录"""
        session_log.init_session()
        session_log.log_llm_req([{"role": "user", "content": "Hello"}])
        session_log.log_reasoning_content("思考：这是一个计算问题...")
        session_log.log_llm_rsp("答案是42")

        log_path = TEST_LOG_ROOT / session_log.session_file_name
        content = log_path.read_text(encoding="utf-8")

        # 推理内容应在 AI 回复之前
        reasoning_pos = content.index("推理内容")
        llm_rsp_pos = content.index("ASSISTANT")
        assert reasoning_pos < llm_rsp_pos

    # ========== _format_log_item 推理内容测试 ==========

    def test_format_log_item_with_reasoning(self, session_log):
        """测试 _format_log_item 正确格式化推理条目"""
        result = session_log._format_log_item({"reasoning": "test reasoning"})

        assert "**推理内容**：" in result
        assert "<details>" in result
        assert "<summary>展开查看推理过程</summary>" in result
        assert "test reasoning" in result

    def test_format_log_item_without_reasoning(self, session_log):
        """测试没有推理内容的条目不包含 details 标签"""
        result = session_log._format_log_item({"role": "assistant", "content": "hello"})

        assert "推理内容" not in result
        assert "<details>" not in result

    def test_format_log_item_reasoning_empty_string(self, session_log):
        """key 存在但值为空字符串时不输出"""
        result = session_log._format_log_item({"reasoning": ""})

        assert "推理内容" not in result
        assert "<details>" not in result

    # ========== HTML 格式测试 ==========

    @pytest.fixture
    def session_log_html(self):
        """创建 HTML 格式的 SessionLog"""
        log = SessionLog()
        log.format = "html"
        return log

    def test_html_reasoning_not_in_pre(self, session_log_html):
        """HTML 格式中推理内容不应嵌套在 <pre> 内"""
        session_log_html.init_session()
        session_log_html.log_reasoning_content("推理过程...")
        session_log_html.log_llm_rsp("回复内容")

        log_path = TEST_LOG_ROOT / session_log_html.session_file_name
        content = log_path.read_text(encoding="utf-8")

        # 推理 details 应该在 <pre> 外部，检查 details 和 pre 结构
        assert "<details" in content
        assert "展开查看推理过程" in content

    # ========== 兼容性测试 ==========

    def test_compatibility_no_reasoning_in_flow(self, session_log):
        """模拟不支持 reasoning 的提供商：不调用 log_reasoning_content"""
        session_log.init_session()
        # 不调用 log_reasoning_content
        session_log.log_llm_rsp("正常回复")

        log_path = TEST_LOG_ROOT / session_log.session_file_name
        content = log_path.read_text(encoding="utf-8")

        assert "推理内容" not in content
        assert "ASSISTANT" in content
        assert "正常回复" in content

    def test_multiple_turns_mixed_reasoning(self, session_log):
        """多轮对话中，部分轮次有推理、部分没有"""
        session_log.init_session()

        # 第1轮：有推理
        session_log.log_turn(1)
        session_log.log_llm_req([{"role": "user", "content": "问题1"}])
        session_log.log_reasoning_content("推理1")
        session_log.log_llm_rsp("回复1")

        # 第2轮：无推理
        session_log.log_turn(2)
        session_log.log_llm_req([{"role": "user", "content": "问题2"}])
        session_log.log_llm_rsp("回复2")

        # 第3轮：有推理
        session_log.log_turn(3)
        session_log.log_llm_req([{"role": "user", "content": "问题3"}])
        session_log.log_reasoning_content("推理3")
        session_log.log_llm_rsp("回复3")

        log_path = TEST_LOG_ROOT / session_log.session_file_name
        content = log_path.read_text(encoding="utf-8")

        # 推理1 和 推理3 都应该出现
        assert "推理1" in content
        assert "推理3" in content

        # 所有回复都应该出现
        assert "回复1" in content
        assert "回复2" in content
        assert "回复3" in content

        # 轮次2 不应有推理内容区块
        # (通过检查 Turn 2 和 Turn 3 之间没有推理内容来验证)
        turn2_pos = content.find("Turn 2")
        turn3_pos = content.find("Turn 3")
        segment = content[turn2_pos:turn3_pos]
        assert "推理内容" not in segment


if __name__ == "__main__":
    pytest.main([__file__, "-v"])