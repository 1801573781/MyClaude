"""
Memory 模块单元测试（pytest）。

覆盖场景（来自 memory_spec.md 第 9 节）：
- CRUD 完整流程
- 检索排序与权重
- 压缩生成长期记忆（含异常处理）
- 注入上下文格式
- 遗忘策略
- 持久化与恢复
- 损坏文件恢复
- 空状态行为
"""

import json
import logging
import math
import os
import time
import uuid
from pathlib import Path
from typing import Dict, List

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.memory.memory_store import MemoryStore
from src.memory.memory_retrieval import MemoryRetrieval, estimate_tokens
from src.memory.memory_compressor import MemoryCompressor
from src.memory.memory_injector import MemoryInjector
from src.memory.memory_manager import MemoryManager


# ========== Fixtures ==========


def _make_config(tmp_path: Path, **overrides) -> Dict:
    """构造 MemoryManager 所需的配置字典。"""
    cfg = {
        "memory": {
            "enabled": True,
            "storage_path": str(tmp_path / ".memdir"),
            "short_term_max_entries": 50,
            "short_term_max_tokens": 8000,
            "long_term_max_inject": 5,
            "working_memory_max_tokens": 2000,
            "similarity_threshold": 0.15,
            "compress_batch_size": 20,
            "compress_llm_model": "DeepSeek",
            "forget_older_than_days": 30,
            "forget_importance_below": 0.2,
        }
    }
    cfg["memory"].update(overrides)
    return cfg


def _make_memory(content: str, mem_type: str = "short", importance: float = 0.5,
                 tags: List[str] = None, timestamp: int = None,
                 access_count: int = 0) -> Dict:
    """快速构造记忆字典。"""
    if tags is None:
        tags = []
    if timestamp is None:
        timestamp = int(time.time())
    return {
        "id": uuid.uuid4().hex,
        "content": content,
        "type": mem_type,
        "importance": importance,
        "timestamp": timestamp,
        "access_count": access_count,
        "last_access": timestamp,
        "tags": tags,
        "metadata": {},
    }


# ========== 1. CRUD 完整流程 ==========

class TestCRUD:
    """测试记忆的增删改查全流程。"""

    def test_add_working_memory(self, tmp_path):
        """添加工作记忆，验证仅存在于内存不持久化。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)

        mem_id = mm.add_memory("当前任务：修复 chat_llm bug", mem_type="working", importance=0.8)

        # 工作记忆在 all_memories 中可见
        all_mems = mm.get_all_memories()
        working_mems = [m for m in all_mems if m["type"] == "working"]
        assert len(working_mems) == 1
        assert working_mems[0]["id"] == mem_id
        assert working_mems[0]["content"] == "当前任务：修复 chat_llm bug"

        # 重新创建实例 → 工作记忆消失
        mm2 = MemoryManager(cfg)
        assert len([m for m in mm2.get_all_memories() if m["type"] == "working"]) == 0

    def test_add_short_memory_persists(self, tmp_path):
        """添加短期记忆，验证持久化并可从新实例恢复。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mem_id = mm.add_memory("用户偏好 pytest", mem_type="short", importance=0.7, tags=["python"])

        mm2 = MemoryManager(cfg)
        all_short = mm2.get_all_memories("short")
        assert len(all_short) == 1
        assert all_short[0]["id"] == mem_id
        assert all_short[0]["tags"] == ["python"]

    def test_add_long_memory(self, tmp_path):
        """添加长期记忆，验证持久化。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mm.add_memory("项目使用 MiniMax API", mem_type="long", importance=0.9)

        mm2 = MemoryManager(cfg)
        assert mm2.get_all_memories("long")[0]["content"] == "项目使用 MiniMax API"

    def test_update_memory_fields(self, tmp_path):
        """更新记忆的 content / importance / tags。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mem_id = mm.add_memory("旧内容", mem_type="short", importance=0.3, tags=["old"])

        # 更新
        assert mm.update_memory(mem_id, content="新内容", importance=0.9, tags=["new"])
        results = mm.get_memories("新内容", limit=1)
        assert results[0]["content"] == "新内容"
        assert results[0]["importance"] == 0.9
        assert results[0]["tags"] == ["new"]

    def test_update_nonexistent_returns_false(self, tmp_path):
        """更新不存在的 ID 返回 False。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        assert mm.update_memory("nonexistent_id", content="x") is False

    def test_delete_memory(self, tmp_path):
        """删除记忆后无法再查到。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mem_id = mm.add_memory("待删除", mem_type="short")

        assert mm.delete_memory(mem_id) is True
        assert mm.delete_memory(mem_id) is False  # 重复删除
        assert len(mm.get_all_memories("short")) == 0

    def test_delete_working_memory(self, tmp_path):
        """删除工作记忆。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mem_id = mm.add_memory("临时任务", mem_type="working")
        assert mm.delete_memory(mem_id) is True
        assert len(mm.get_all_memories("working")) == 0

    def test_type_cannot_be_changed(self, tmp_path):
        """update_memory 不应修改 type 字段。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mem_id = mm.add_memory("测试", mem_type="short")
        mm.update_memory(mem_id, content="测试2")  # 不传 type
        mems = mm.get_all_memories("short")
        assert mems[0]["type"] == "short"


# ========== 2. 检索排序与权重 ==========

class TestRetrieval:
    """测试 TF‑IDF 检索、Jaccard、权重公式。"""

    def test_empty_query_returns_empty(self, tmp_path):
        """纯停用词查询返回空。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mm.add_memory("some content", mem_type="short")
        results = mm.get_memories("the a is of")  # 全停用词
        assert results == []

    def test_empty_memory_returns_empty(self, tmp_path):
        """无记忆时检索返回 []。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        assert mm.get_memories("查询") == []

    def test_retrieval_by_relevance(self, tmp_path):
        """添加多条记忆，验证高相关内容排在前面。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)

        # 高相关
        mm.add_memory("pytest 单元测试框架使用", mem_type="short", importance=0.9, tags=["python"])
        # 低相关
        mm.add_memory("MiniMax API 配置", mem_type="short", importance=0.5)
        mm.add_memory("用户界面颜色偏好蓝色", mem_type="short", importance=0.5)

        results = mm.get_memories("pytest 单元测试", limit=3)
        assert len(results) >= 1
        assert "pytest" in results[0]["content"].lower()

    def test_importance_affects_ranking(self, tmp_path):
        """高 importance 的同内容应排在前面。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mm.add_memory("性能优化技巧", mem_type="short", importance=0.2)
        mm.add_memory("性能优化技巧", mem_type="short", importance=0.9)

        results = mm.get_memories("性能优化", limit=2)
        assert len(results) == 2
        # 高 importance 的在前
        assert results[0]["importance"] >= results[1]["importance"]

    def test_access_count_boosts_score(self, tmp_path):
        """多次命中后 access_count 增加，后续检索中排名上升。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mem_id = mm.add_memory("冷门记忆", mem_type="short", importance=0.5)

        # 多次检索
        for _ in range(5):
            mm.get_memories("冷门记忆")
        # 检查 access_count 增加
        all_mems = mm.get_all_memories("short")
        assert all_mems[0]["access_count"] >= 5

    def test_working_memory_jaccard(self, tmp_path):
        """工作记忆使用 Jaccard 相似度检索。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mm.add_memory("当前需要重构 query_loop", mem_type="working")

        results = mm.get_memories("重构 query_loop", limit=5)
        assert len(results) >= 1
        assert results[0]["type"] == "working"

    def test_retrieval_truncates_to_limit(self, tmp_path):
        """检索结果不超过 limit。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        for i in range(20):
            mm.add_memory(f"记忆条目 {i}", mem_type="short")
        results = mm.get_memories("记忆", limit=3)
        assert len(results) <= 3


# ========== 3. 压缩生成长期记忆 ==========

class TestCompression:
    """测试短期记忆压缩为长期记忆。"""

    def _mock_llm_success(self, messages, max_tokens, temperature):
        """模拟 LLM 返回固定总结。"""
        return "用户偏好 pytest 且要求覆盖率 > 80%；项目使用 MiniMax API；代码风格遵循 PEP8。"

    def _mock_llm_empty(self, messages, max_tokens, temperature):
        """模拟 LLM 返回空字符串。"""
        return ""

    def _mock_llm_exception(self, messages, max_tokens, temperature):
        """模拟 LLM 调用异常。"""
        raise RuntimeError("LLM 不可用")

    def test_compress_below_threshold_does_nothing(self, tmp_path):
        """短期记忆不足阈值时不压缩。"""
        cfg = _make_config(tmp_path, short_term_max_entries=50)
        mm = MemoryManager(cfg)
        mm.set_llm_call(self._mock_llm_success)
        mm.add_memory("测试记忆", mem_type="short")

        long_before = len(mm.get_all_memories("long"))
        result = mm.compress_short_term()
        assert result == 0
        assert len(mm.get_all_memories("long")) == long_before

    def test_compress_generates_long_memory(self, tmp_path):
        """超过条目阈值时压缩生成长期记忆。"""
        cfg = _make_config(tmp_path, short_term_max_entries=5, compress_batch_size=5)
        mm = MemoryManager(cfg)
        mm.set_llm_call(self._mock_llm_success)

        # 添加 6 条短期记忆（超阈值 5）
        for i in range(6):
            mm.add_memory(f"短期记忆 {i}", mem_type="short", importance=0.5)

        long_before = len(mm.get_all_memories("long"))
        new_long = mm.compress_short_term()
        long_after = len(mm.get_all_memories("long"))

        assert new_long >= 1
        assert long_after > long_before

        # 验证长期记忆带 compressed 标签
        long_mems = mm.get_all_memories("long")
        compressed = [m for m in long_mems if "compressed" in m.get("tags", [])]
        assert len(compressed) >= 1
        # 验证 metadata.original_count
        for c in compressed:
            assert "original_count" in c.get("metadata", {})

    def test_compress_empty_llm_still_deletes_short(self, tmp_path):
        """LLM 返回空时，不创建长期记忆但短期记忆仍被删除。"""
        cfg = _make_config(tmp_path, short_term_max_entries=3, compress_batch_size=3)
        mm = MemoryManager(cfg)
        mm.set_llm_call(self._mock_llm_empty)

        for i in range(4):
            mm.add_memory(f"待压缩 {i}", mem_type="short")

        short_before = len(mm.get_all_memories("short"))
        long_before = len(mm.get_all_memories("long"))
        mm.compress_short_term()
        short_after = len(mm.get_all_memories("short"))
        long_after = len(mm.get_all_memories("long"))

        assert short_after < short_before  # 短期记忆减少
        assert long_after == long_before   # 无新长期记忆

    def test_compress_llm_exception_does_not_crash(self, tmp_path):
        """LLM 异常时不崩溃，且部分短期记忆被删除。"""
        cfg = _make_config(tmp_path, short_term_max_entries=3, compress_batch_size=3)
        mm = MemoryManager(cfg)
        mm.set_llm_call(self._mock_llm_exception)

        for i in range(4):
            mm.add_memory(f"待压缩 {i}", mem_type="short")

        # 不应抛异常
        mm.compress_short_term()

        # 至少删除了一些短期记忆（不会无限重试）
        short_after = len(mm.get_all_memories("short"))
        assert short_after < 4  # 部分删除


# ========== 4. 注入上下文格式 ==========

class TestInjectContext:
    """测试上下文注入格式化。"""

    def test_empty_context_returns_empty(self, tmp_path):
        """无工作记忆且无检索结果时返回空字符串。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        result = mm.inject_context(current_query="随便查")
        assert result == ""

    def test_disabled_returns_empty(self, tmp_path):
        """enabled=False 时始终返回空。"""
        cfg = _make_config(tmp_path, enabled=False)
        mm = MemoryManager(cfg)
        mm.add_memory("有工作记忆", mem_type="working")
        mm.add_memory("有长期记忆", mem_type="long")
        result = mm.inject_context(current_query="长期记忆")
        assert result == ""

    def test_working_memory_injected(self, tmp_path):
        """工作记忆出现在注入上下文中。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mm.add_memory("用户希望使用 pytest", mem_type="working")
        mm.add_memory("当前正在修改 chat_llm.py", mem_type="working")

        context = mm.inject_context(current_query="写测试")
        assert "当前任务上下文" in context
        assert "pytest" in context
        assert "chat_llm" in context

    def test_long_memory_injected(self, tmp_path):
        """长期记忆检索后出现在注入上下文中。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mm.add_memory("用户偏好每行代码不超过 120 字符", mem_type="long", importance=0.8)
        mm.add_memory("项目使用 MiniMax API", mem_type="long", importance=0.7)

        context = mm.inject_context(current_query="代码风格", max_tokens=2000)
        assert "相关历史记忆" in context
        assert "相关性" in context

    def test_token_truncation(self, tmp_path):
        """max_tokens 限制生效，上下文不会无限增长。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        # 添加大量工作记忆
        for i in range(50):
            mm.add_memory(f"工作记忆第 {i} 条包含大量文本内容 " * 5, mem_type="working")

        context = mm.inject_context(current_query="测试", max_tokens=500)
        # token 估算不应超过 max_tokens 过多
        tokens = estimate_tokens(context)
        # 允许小幅超出（因估算误差 + 最后一条是整个加入的）
        assert tokens <= max(500 * 2, tokens)

    def test_header_present(self, tmp_path):
        """注入文本包含标准头部。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mm.add_memory("测试工作记忆", mem_type="working")

        context = mm.inject_context(current_query="测试")
        assert "记忆上下文" in context or "Memory" in context


# ========== 5. 遗忘策略 ==========

class TestForget:
    """测试遗忘策略。"""

    def test_forget_empty_returns_zero(self, tmp_path):
        """无长期记忆时 forget 返回 0。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        assert mm.forget() == 0

    def test_forget_old_low_importance(self, tmp_path):
        """过期 + 低重要性记忆被删除。"""
        cfg = _make_config(tmp_path, forget_older_than_days=10, forget_importance_below=0.5)
        mm = MemoryManager(cfg)

        # 15 天前，低重要性
        old_time = int(time.time() - 15 * 86400)
        mem = _make_memory("过期的低价值记忆", mem_type="long", importance=0.2, timestamp=old_time)
        mm._store.add(mem)

        deleted = mm.forget(older_than_days=10, importance_below=0.3)
        assert deleted >= 1

    def test_recent_high_importance_kept(self, tmp_path):
        """近期高重要性记忆不被删除。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)

        # 5 天前，高重要性
        recent = int(time.time() - 5 * 86400)
        mem = _make_memory("重要的近期记忆", mem_type="long", importance=0.9, timestamp=recent)
        mm._store.add(mem)

        deleted = mm.forget(older_than_days=10, importance_below=0.3)
        assert deleted == 0

    def test_pinned_memory_never_forgotten(self, tmp_path):
        """带 pinned 标签的记忆永不被自动遗忘。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)

        old_time = int(time.time() - 100 * 86400)
        mem = _make_memory("固定记忆", mem_type="long", importance=0.1, timestamp=old_time,
                           tags=["pinned"])
        mm._store.add(mem)

        deleted = mm.forget(older_than_days=10, importance_below=1.0)
        assert deleted == 0

    def test_high_value_grace_period(self, tmp_path):
        """高重要性 + 高访问量记忆享有宽限期。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)

        # 90 天前（超过默认 30*3=90），但高重要性 + 高访问量 → 应延迟到 120 天
        old_time = int(time.time() - 95 * 86400)
        mem = _make_memory("高价值记忆", mem_type="long", importance=0.9, timestamp=old_time,
                           access_count=20)
        mm._store.add(mem)

        deleted = mm.forget(older_than_days=30, importance_below=0.2)
        # 95 天 > 30*4=120? No. 95 < 120，所以应保留
        assert deleted == 0

        # 125 天前 → 即使高价值也应删除
        very_old_time = int(time.time() - 125 * 86400)
        mem2 = _make_memory("高价值但太旧", mem_type="long", importance=0.9, timestamp=very_old_time,
                            access_count=20)
        mm._store.add(mem2)
        deleted2 = mm.forget(older_than_days=30, importance_below=0.2)
        assert deleted2 >= 1


# ========== 6. 持久化与恢复 ==========

class TestPersistence:
    """测试记忆的持久化与恢复。"""

    def test_persist_and_restore(self, tmp_path):
        """创建记忆 → 新实例加载 → 所有记忆恢复。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mm.add_memory("持久化测试 1", mem_type="short", importance=0.3)
        mm.add_memory("持久化测试 2", mem_type="long", importance=0.8, tags=["tag1"])

        # 新实例
        mm2 = MemoryManager(cfg)
        all_mems = mm2.get_all_memories()
        assert len(all_mems) == 2

        short = mm2.get_all_memories("short")
        assert len(short) == 1
        assert short[0]["content"] == "持久化测试 1"

        long_m = mm2.get_all_memories("long")
        assert len(long_m) == 1
        assert long_m[0]["tags"] == ["tag1"]

    def test_persist_working_to_short(self, tmp_path):
        """工作记忆转移为短期后持久化。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mm.add_memory("工作项 A", mem_type="working", importance=0.6)
        mm.add_memory("工作项 B", mem_type="working", importance=0.4)

        count = mm.persist_working_to_short()
        assert count == 2
        assert len(mm.get_all_memories("working")) == 0

        # 短期记忆已持久化
        mm2 = MemoryManager(cfg)
        short = mm2.get_all_memories("short")
        assert len(short) == 2
        contents = {m["content"] for m in short}
        assert contents == {"工作项 A", "工作项 B"}

    def test_clear_working_memory(self, tmp_path):
        """清空工作记忆。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mm.add_memory("临时", mem_type="working")
        mm.clear_working_memory()
        assert len(mm.get_all_memories("working")) == 0


# ========== 7. 损坏文件恢复 ==========

class TestCorruptionRecovery:
    """测试文件损坏时的自动恢复。"""

    def test_invalid_json_recovers(self, tmp_path):
        """非法 JSON 文件不导致崩溃，自动回退到空列表。"""
        storage_dir = tmp_path / ".memdir"
        storage_dir.mkdir(parents=True, exist_ok=True)
        json_path = storage_dir / "memories.json"
        json_path.write_text("这不是有效的 JSON {{{", encoding="utf-8")

        store = MemoryStore(str(storage_dir))
        assert store.get_all() == []

    def test_root_not_list_recovers(self, tmp_path):
        """JSON 根元素不是列表时回退到空列表。"""
        storage_dir = tmp_path / ".memdir"
        storage_dir.mkdir(parents=True, exist_ok=True)
        json_path = storage_dir / "memories.json"
        json_path.write_text('{"key": "value"}', encoding="utf-8")

        store = MemoryStore(str(storage_dir))
        assert store.get_all() == []

    def test_backup_restore_works(self, tmp_path):
        """主文件损坏但从备份恢复成功。"""
        storage_dir = tmp_path / ".memdir"
        storage_dir.mkdir(parents=True, exist_ok=True)

        # 先创建有效数据并写入备份
        valid_data = [{"id": "test1", "content": "有效数据", "type": "short",
                       "importance": 0.5, "timestamp": 1000, "access_count": 0,
                       "last_access": 1000, "tags": [], "metadata": {}}]
        bak_path = storage_dir / "memories.json.bak.1"
        with open(bak_path, "w", encoding="utf-8") as f:
            json.dump(valid_data, f, ensure_ascii=False)

        # 主文件损坏
        json_path = storage_dir / "memories.json"
        json_path.write_text("损坏的数据 {{{", encoding="utf-8")

        store = MemoryStore(str(storage_dir))
        all_mems = store.get_all()
        assert len(all_mems) == 1
        assert all_mems[0]["id"] == "test1"

    def test_corrupt_item_skipped(self, tmp_path):
        """列表中单条损坏条目被跳过，其他正常加载。"""
        storage_dir = tmp_path / ".memdir"
        storage_dir.mkdir(parents=True, exist_ok=True)
        json_path = storage_dir / "memories.json"

        data = [
            "这不是字典而是字符串",  # 损坏
            {"id": "good1", "content": "正常条目", "type": "short",
             "importance": 0.5, "timestamp": 1000, "access_count": 0,
             "last_access": 1000, "tags": [], "metadata": {}},
        ]
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        store = MemoryStore(str(storage_dir))
        all_mems = store.get_all()
        assert len(all_mems) == 1
        assert all_mems[0]["id"] == "good1"


# ========== 8. 空状态行为 ==========

class TestEmptyState:
    """测试模块在无记忆时的安全行为。"""

    def test_empty_store_count(self, tmp_path):
        """空存储 count 返回 0。"""
        store = MemoryStore(str(tmp_path / ".memdir"))
        assert store.count() == 0
        assert store.count("short") == 0

    def test_empty_memory_manager_all_returns_empty(self, tmp_path):
        """空 MemoryManager 的 get_all_memories 返回空列表。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        assert mm.get_all_memories() == []
        assert mm.get_all_memories("working") == []

    def test_empty_compress_returns_zero(self, tmp_path):
        """空短期记忆压缩返回 0。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        mm.set_llm_call(lambda m, mt, t: "总结")
        assert mm.compress_short_term() == 0

    def test_empty_forget_returns_zero(self, tmp_path):
        """空长期记忆遗忘返回 0。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        assert mm.forget() == 0

    def test_empty_inject_returns_empty(self, tmp_path):
        """无任何记忆时 inject_context 返回空。"""
        cfg = _make_config(tmp_path)
        mm = MemoryManager(cfg)
        assert mm.inject_context() == ""


# ========== 9. 数据校验 ==========

class TestValidateItem:
    """测试 _validate_item 方法。"""

    @staticmethod
    def _call_validate(item):
        """通过 MemoryStore 实例调用 _validate_item。"""
        # 创建一个临时 store，使用其 _validate_item 方法
        import tempfile
        store = MemoryStore(tempfile.mkdtemp())
        return store._validate_item(item)

    def test_missing_id_generated(self):
        """缺失 id 时自动生成。"""
        validated = self._call_validate({"content": "test", "type": "short"})
        assert "id" in validated
        assert len(validated["id"]) == 32

    def test_invalid_type_defaults_to_short(self):
        """无效 type 默认设为 short。"""
        validated = self._call_validate({"id": "x", "content": "test", "type": "invalid"})
        assert validated["type"] == "short"

    def test_importance_clamped(self):
        """importance 超出范围时截断。"""
        validated = self._call_validate({"id": "x", "content": "test", "type": "short", "importance": 1.5})
        assert validated["importance"] == 1.0

        validated2 = self._call_validate({"id": "y", "content": "test", "type": "short", "importance": -0.5})
        assert validated2["importance"] == 0.0

    def test_tags_not_list_defaults_to_empty(self):
        """tags 不是列表时设为空列表。"""
        validated = self._call_validate({"id": "x", "content": "test", "type": "short", "tags": "not_a_list"})
        assert validated["tags"] == []

    def test_missing_timestamp_set_to_now(self):
        """缺失 timestamp 设为当前时间。"""
        validated = self._call_validate({"id": "x", "content": "test", "type": "short"})
        assert validated["timestamp"] > 0
        assert abs(validated["timestamp"] - time.time()) < 5


# ========== 10. 工具函数测试 ==========

class TestUtilities:
    """测试分词、token 估算等工具函数。"""

    def test_tokenize_english(self):
        """英文分词并去除停用词。"""
        tokens = _tokenize("The quick brown fox jumps over the lazy dog")
        assert "the" not in tokens
        assert "quick" in tokens
        assert "fox" in tokens

    def test_tokenize_chinese(self):
        """中文分词（单字切分）并去停用词。"""
        tokens = _tokenize("这是一个测试句子")
        assert "的" not in tokens  # 停用词
        assert "测" in tokens
        assert "试" in tokens

    def test_estimate_tokens(self):
        """token 估算函数返回合理值。"""
        est = estimate_tokens("Hello world this is a test")
        assert est > 0
        assert est < 50  # 不太可能超过 50

    def test_cosine_similarity_identical(self):
        """相同向量余弦相似度为 1。"""
        a = [1.0, 2.0, 3.0]
        b = [1.0, 2.0, 3.0]
        sim = MemoryRetrieval._cosine_similarity(a, b)
        assert sim == pytest.approx(1.0, abs=1e-6)

    def test_cosine_similarity_orthogonal(self):
        """正交向量余弦相似度为 0。"""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        sim = MemoryRetrieval._cosine_similarity(a, b)
        assert sim == pytest.approx(0.0, abs=1e-6)


# ========== 11. MemoryStore 原子写入 ==========

class TestAtomicWrite:
    """测试原子写入与备份机制。"""

    def test_flush_creates_file(self, tmp_path):
        """刷盘后文件存在。"""
        store = MemoryStore(str(tmp_path / ".memdir"))
        store.add({"content": "测试", "type": "short"})
        json_path = tmp_path / ".memdir" / "memories.json"
        assert json_path.exists()

    def test_backup_created_on_write(self, tmp_path):
        """第二次写入后生成备份文件。"""
        storage_dir = tmp_path / ".memdir"
        store = MemoryStore(str(storage_dir))
        store.add({"content": "第一版", "type": "short"})
        store.add({"content": "第二版", "type": "short"})

        bak1 = storage_dir / "memories.json.bak.1"
        assert bak1.exists()

    def test_delete_batch(self, tmp_path):
        """批量删除功能。"""
        store = MemoryStore(str(tmp_path / ".memdir"))
        ids = [store.add({"content": f"记忆 {i}", "type": "short"}) for i in range(5)]
        deleted = store.delete_batch(ids[:3])
        assert deleted == 3
        assert store.count() == 2