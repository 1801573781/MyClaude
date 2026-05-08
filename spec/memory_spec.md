# Memory 模块需求规格文档（memory_spec.md）

## 1. 背景与目标

MyClaude 需要引入一个分层记忆系统，使 AI 助手能够跨会话、跨任务保留与遗忘信息，提升长期交互的连贯性。本模块参考 Claude Code、OpenClaw、Hermes 等主流 Agent 框架的记忆设计，提取其核心优点，并补充必要的实现细节，形成一份可直接指导编码的完整需求文档。

**核心参考来源与借鉴要点**：

| 来源 | 借鉴的核心机制 |
|------|---------------|
| **Claude Code** | 分层记忆（短期/长期）+ LLM 驱动的压缩总结，将对话历史提炼为持久知识。 |
| **OpenClaw** | TF‑IDF + 余弦相似度的轻量检索，不依赖向量数据库；记忆条目带 importance 权重。 |
| **Hermes** | 工作记忆（Working Memory）作为当前任务上下文缓存，任务边界自动清理。 |

**目标**：
- 提供短期（会话内）、长期（跨会话）和工作（当前任务）三层记忆结构。
- 支持自动压缩、检索、遗忘和显式读写。
- 完全独立于 MyClaude 现有模块（QueryLoop、Tool 系统等），仅暴露清晰接口。
- 能够通过 `add_tests` Skill 对模块进行完整的单元测试。

---

## 2. 总体架构

Memory 模块由以下子组件构成：

| 组件 | 职责 |
|------|------|
| **MemoryStore** | 物理存储：基于 JSON 文件（使用 `storage_path` 目录）的持久化，支持 CRUD 与元数据管理。 |
| **MemoryRetrieval** | 检索：基于 TF‑IDF + 余弦相似度获取最相关的记忆条目。 |
| **MemoryCompressor** | 当短期记忆超过阈值时，调用 LLM 将多条旧记忆合并/总结为更高层的长期记忆。 |
| **MemoryInjector** | 在每次 LLM 请求前，从长期和工作记忆中选择最相关的条目，生成注入的上下文文本。 |
| **MemoryManager** | 对外的统一 Facade，协调上述组件，提供简单的高层 API。 |

**组件协作流程**：

```
用户输入 → MemoryManager.inject_context()
                ├── 收集工作记忆（self._working_memories）
                ├── MemoryRetrieval.get_relevant(query) → 检索长期记忆
                └── MemoryInjector.format_context() → 拼接为注入文本

LLM 回复后 → MemoryManager.add_memory(type="short") → 追加短期记忆
                │
                └── MemoryManager.compress_short_term()
                        ├── 检测阈值 → 选出待压缩条目
                        ├── 调用 LLM 总结 → 生成长期记忆
                        └── 删除已压缩的短期记忆
```

---

## 3. 三层记忆定义

| 层级 | 生命周期 | 容量控制 | 典型内容 |
|------|----------|----------|----------|
| **工作记忆** | 单个任务（几轮对话） | 固定 token 预算（默认 2000 token） | 当前任务的目标、中间结论、临时变量、用户最新指令中的关键点。 |
| **短期记忆** | 一个会话（直到压缩或会话结束） | 条目数或 token 阈值（默认 50 条或 8000 token） | 对话轮次、工具调用结果、用户偏好、已完成的子任务。 |
| **长期记忆** | 跨会话（永久，除非主动遗忘） | 无硬性上限（支持分页检索） | 用户长期偏好（代码风格、常用工具）、项目知识点、历史重要决策。 |

**三层记忆之间的流转**：

```
工作记忆 ──(persist_working_to_short)──→ 短期记忆
                                              │
                         (compress_short_term) │ LLM 总结压缩
                                              ↓
                                         长期记忆
                                              │
                         (forget)              │ 时间/重要性淘汰
                                              ↓
                                           遗忘删除
```

---

## 4. 数据结构

每个记忆条目存储为以下 JSON 对象：

```json
{
  "id": "mem_a1b2c3d4",
  "content": "用户偏好使用 pytest 进行单元测试，且要求覆盖率 > 80%",
  "type": "long",
  "importance": 0.7,
  "timestamp": 1746000000,
  "access_count": 3,
  "last_access": 1746000100,
  "tags": ["python", "pytest", "用户偏好"],
  "metadata": {"source": "compress", "original_count": 5}
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | 唯一标识符，使用 `uuid.uuid4().hex` 生成（32 位十六进制字符串）。 |
| `content` | `str` | 记忆的原始文本（UTF-8），最大长度建议 2000 字符（非强制截断）。 |
| `type` | `str` | 枚举值：`"working"`、`"short"`、`"long"`。 |
| `importance` | `float` | 范围 `0.0 ~ 1.0`，默认 `0.5`。影响压缩选择（越低越优先被压缩）和检索权重。 |
| `timestamp` | `int` | 创建时的 Unix 时间戳（秒），由 `time.time()` 生成。 |
| `access_count` | `int` | 该记忆被检索命中的次数，每次检索命中时 `+= 1`。 |
| `last_access` | `int` | 最后一次被检索命中的 Unix 时间戳。 |
| `tags` | `list[str]` | 可选，用于分类检索。压缩生成的长期记忆自动打上 `"compressed"` 标签。 |
| `metadata` | `dict` | 可选，存储扩展信息。压缩时记录 `original_count`（被合并的原始条目数）。 |

**导入时的数据校验规则**：
- `id` 缺失或为空字符串 → 自动生成新 UUID。
- `type` 不在 `["working", "short", "long"]` → 默认设为 `"short"`。
- `importance` 超出 `0.0~1.0` → 截断到边界值。
- `timestamp` 缺失或为 0 → 使用当前时间。
- `tags` 不是列表 → 默认设为 `[]`。
- 校验失败记录 `logging.warning`，不阻塞加载。

---

## 5. 核心接口（供内部及测试使用）

```python
from typing import List, Optional, Dict
import uuid


class MemoryManager:
    """
    Memory 模块的统一入口（Facade）。
    协调 MemoryStore / MemoryRetrieval / MemoryCompressor / MemoryInjector 四个子组件。
    所有方法均为同步（无 async/await）。
    """

    def __init__(self, config: Dict):
        """
        初始化 MemoryManager。

        参数:
            config: 完整配置字典（与 config.yaml 结构一致），
                    必须包含 config["memory"] 子字典。

        内部行为:
            1. 从 config["memory"] 提取各项阈值参数。
            2. 创建 MemoryStore 实例，加载持久化文件。
            3. 创建 MemoryRetrieval / MemoryCompressor / MemoryInjector 实例。
            4. 初始化空的工作记忆列表 self._working_memories: List[Dict]。
        """
        pass

    # ========== CRUD ==========

    def add_memory(self,
                   content: str,
                   mem_type: str,
                   importance: float = 0.5,
                   tags: Optional[List[str]] = None) -> str:
        """
        添加一条记忆，返回记忆 ID（32 位十六进制字符串）。

        参数:
            content:    记忆文本内容（UTF-8）。
            mem_type:   "working" | "short" | "long"。
            importance: 0.0~1.0，默认 0.5。
            tags:       可选标签列表。

        行为:
            - mem_type == "working" → 仅追加到 self._working_memories，不持久化。
            - mem_type == "short" | "long" → 创建条目并调用 MemoryStore.save() 持久化。

        返回:
            新创建记忆的 id 字符串。
        """
        pass

    def get_memories(self,
                     query: str,
                     mem_type: Optional[str] = None,
                     limit: int = 5) -> List[Dict]:
        """
        根据查询文本检索最相关的记忆条目。

        参数:
            query:    查询文本（用于 TF‑IDF 向量化）。
            mem_type: 可选过滤类型，None 表示检索 short + long。
            limit:    最大返回条数。

        返回:
            按最终得分降序排列的记忆字典列表。
            命中条目的 access_count 和 last_access 同步更新并持久化。

        异常处理:
            向量化失败或记忆为空 → 返回 []，记录 logging.warning。
        """
        pass

    def get_all_memories(self,
                         mem_type: Optional[str] = None) -> List[Dict]:
        """
        返回所有指定类型的记忆（不修改 access_count）。

        参数:
            mem_type: None 返回全部，或指定 "short" / "long"。

        用途:
            主要用于测试验证和调试导出。
        """
        pass

    def update_memory(self,
                      memory_id: str,
                      content: Optional[str] = None,
                      importance: Optional[float] = None,
                      tags: Optional[List[str]] = None) -> bool:
        """
        更新已有记忆的部分字段。

        参数:
            memory_id:  目标记忆 ID。
            content:    新内容（None 表示不修改）。
            importance: 新重要性（None 表示不修改）。
            tags:       新标签列表（None 表示不修改）。

        返回:
            True 表示更新成功，False 表示未找到指定 ID。

        注意:
            不能修改 type 字段（类型一旦确定不可更改）。
        """
        pass

    def delete_memory(self, memory_id: str) -> bool:
        """
        永久删除一条记忆。

        返回:
            True 表示删除成功，False 表示未找到指定 ID。
        """
        pass

    # ========== 记忆生命周期 ==========

    def compress_short_term(self) -> int:
        """
        当短期记忆超过阈值时，调用 LLM 将旧条目合并为长期记忆。

        详细算法见 6.2 节。

        返回:
            本次压缩新生成的长期记忆数量（0 表示未触发或无需压缩）。
        """
        pass

    def forget(self,
               older_than_days: int = 30,
               importance_below: float = 0.2) -> int:
        """
        根据时间与重要性自动遗忘长期记忆。

        详细算法见 6.4 节。

        返回:
            被删除的记忆条目数量。
        """
        pass

    def inject_context(self,
                       current_query: str,
                       max_tokens: int = 2000) -> str:
        """
        为当前 LLM 请求生成需要注入的上下文文本。

        详细格式见 6.3 节。

        返回:
            格式化的 Markdown 上下文字符串。
            若工作记忆为空且无相关长期记忆，返回空字符串 ""。
        """
        pass

    def clear_working_memory(self) -> None:
        """
        清空当前工作记忆。

        调用时机:
            新任务开始时由外部调用者（如 QueryLoop）调用。
        """
        pass

    def persist_working_to_short(self) -> int:
        """
        将当前工作记忆中所有条目转移为短期记忆（持久化），并清空工作记忆。

        返回:
            转移的条目数量。
        注意:
            转移后的条目 type 变为 "short"，importance 保留原值。
        """
        pass
```

---

## 6. 详细算法与异常处理

### 6.1 检索算法（MemoryRetrieval）

#### 6.1.1 预处理

对查询字符串 `query` 执行以下步骤：
1. **小写化**：`query.lower()`。
2. **分词**：按非字母数字字符（正则 `[^a-zA-Z0-9\u4e00-\u9fff]+`）分割，中文按单字切分。
3. **去停用词**：
   - 英文停用词集合：`{"the", "a", "an", "is", "are", "was", "were", "of", "in", "on", "to", "for", "and", "or", "it", "this", "that", "with", "as", "at", "by", "from"}`。
   - 中文停用词集合（可选）：`{"的", "了", "和", "是", "在", "不", "我", "有", "也", "就", "都", "要", "会", "可以", "这个"}`。

#### 6.1.2 构建 TF‑IDF 向量

**纯 Python 实现（不依赖 sklearn）**：

1. **构建词表**：收集所有长期记忆 + 短期记忆的 `content` 中出现过的所有词（去重），排序后建立 `word → index` 映射。
2. **计算 TF（词频）**：对每条记忆 `m`，`tf(w) = count(w, m.content) / len(m.content 的分词列表)`。
3. **计算 IDF（逆文档频率）**：`idf(w) = log( (N + 1) / (df(w) + 1) ) + 1`，其中 `N` = 记忆总数，`df(w)` = 包含词 `w` 的记忆数量。
4. **TF‑IDF 向量**：对每条记忆（和查询），构建长度为 `|词表|` 的向量，每个维度 = `tf(w) * idf(w)`。

**优化**：记忆数量 > 500 时，限制词表大小为前 2000 个最高文档频率的词（按 df 降序截断），控制向量维度。

#### 6.1.3 相似度计算

余弦相似度：

```
cos_sim(a, b) = (a · b) / (||a|| * ||b|| + 1e-8)
```

其中 `1e-8` 防止除零。

#### 6.1.4 最终得分

```
raw_score = cos_sim(query_vec, memory_vec)
final_score = raw_score * (0.7 + 0.3 * importance) * (1 + log(access_count + 1) / 10)
```

其中 `log` 为自然对数。`access_count` 的加成因子在 1.0 ~ 1.7 之间（`access_count=0` 时为 1.0，`access_count=100` 时约 1.46）。

#### 6.1.5 过滤与排序

- 仅保留 `final_score >= similarity_threshold`（默认 0.15）的条目。
- 按 `final_score` 降序排列。
- 截取前 `limit` 条。
- 命中条目的 `access_count += 1`，`last_access = time.time()`，并持久化。

#### 6.1.6 工作记忆的特殊处理

工作记忆不参与 TF‑IDF 向量化。对每条工作记忆，直接计算 `query.lower()` 与 `content.lower()` 的 **Jaccard 相似度**（交集词数 / 并集词数），若 > 阈值则纳入结果，排在长期记忆之前。工作记忆不修改 `access_count`。

#### 6.1.7 异常处理

- 记忆集合为空 → 返回 `[]`。
- 查询分词后为空（如纯停用词） → 返回 `[]`，记录 `logging.warning`。
- 向量维度不一致（新增记忆后词表变化） → 自动重建向量，记录 `logging.info`。

---

### 6.2 压缩策略（MemoryCompressor）

压缩是将短期记忆提炼为长期记忆的核心机制，灵感来自 Claude Code 的对话压缩流水线。

#### 6.2.1 触发条件

任一条件满足即触发：

| 条件 | 判断方式 |
|------|---------|
| 条目数超限 | `len(short_term_memories) > short_term_max_entries`（默认 50） |
| Token 总量超限 | `_estimate_tokens(所有短期记忆 content 之和) > short_term_max_tokens`（默认 8000） |

Token 估算函数 `_estimate_tokens(text: str) -> int`：
- 若 `tiktoken` 可用：使用 `tiktoken.get_encoding("cl100k_base").encode(text)`。
- 否则使用粗略公式：`len(text) // 4 + len(re.findall(r'[\u4e00-\u9fff]', text)) // 2`。

#### 6.2.2 选择待压缩条目

1. 对所有短期记忆按优先级排序（升序，排在前面的优先被压缩）：
   ```
   排序键 = (importance, timestamp)
   ```
   即：`importance` 越低越优先，`importance` 相同时 `timestamp` 越旧越优先。

2. 选取前 `compress_batch_size`（默认 20）条作为一批 `batch`。

3. **跳过条件**：若 `batch` 为空，直接返回 0（无短期记忆）。

#### 6.2.3 构造 LLM 压缩提示词

```
[系统角色设定 - 作为 user 消息的前缀]
你是一个记忆压缩助手。你的任务是将多条离散的短期记忆总结为一条简洁、信息密集的长期记忆。

压缩规则：
1. 保留所有关键事实、用户偏好、项目决策、技术约束。
2. 丢弃冗余、重复、琐碎的细节。
3. 使用简洁的陈述句，每条关键信息用分号或逗号分隔。
4. 输出仅包含总结文本，不要加任何前缀、解释或 Markdown 标记。
5. 总结控制在 100 ~ 500 字符以内。

待压缩的短期记忆：
- {batch[0].content}
- {batch[1].content}
- ...
- {batch[N-1].content}

请输出总结：
```

**注意**：此提示词作为单条 `user` 消息发送（MiniMax 兼容格式），不设置 `system` 角色。

#### 6.2.4 调用 LLM

使用 `src/query/chat_llm.py` 中的 `_chat_with_retry` 函数（或封装后的 `chat_with_retry`）：

```python
try:
    summary_text, is_truncated = chat_with_retry(
        model=config["memory"]["compress_llm_model"],
        messages=[{"role": "user", "content": compression_prompt}],
        max_tokens=512,
        temperature=0.3,
    )
except Exception as e:
    logger.error(f"LLM 压缩调用失败: {e}")
    summary_text = None
```

#### 6.2.5 处理 LLM 返回

| 情况 | 行为 |
|------|------|
| **成功返回非空字符串** | ① 创建新长期记忆：`type="long"`，`importance=avg(batch 的 importance)`，`tags=["compressed"]`，`metadata={"original_count": len(batch)}`。② 从短期记忆中删除 `batch` 中所有条目。③ `new_long_count += 1`。 |
| **返回空字符串或仅含空白** | 不创建长期记忆，但仍然删除 `batch` 中所有条目（避免死循环）。记录 `logging.warning("LLM 压缩返回空结果，已丢弃该批次")`。 |
| **LLM 调用异常或超时** | 不创建长期记忆，仍然删除 `batch` 中**前 10 条**（避免无限重试同一批），保留其余条目。记录 `logging.error`。 |

#### 6.2.6 循环压缩

压缩不是一次性的，而是循环执行直到满足退出条件：

```
new_long_count = 0

while True:
    if not 触发条件:   # 短期记忆已低于阈值
        break

    batch = 选择待压缩条目()

    if len(batch) < 3:   # 剩余条目太少，不值得压缩
        break

    result = 压缩一批(batch)
    if result > 0:
        new_long_count += result
        continue

    # 连续两轮未生成新长期记忆 → 终止
    if 上一轮也未生成:
        logger.warning("连续两轮压缩未产出，终止循环")
        break

# 目标：压缩后短期记忆低于阈值的 80%
target_entries = int(short_term_max_entries * 0.8)
target_tokens = int(short_term_max_tokens * 0.8)

# 若仍然超标且无法继续压缩，删除最旧的条目直到达标
while len(short_term) > target_entries:
    remove_oldest_short()
while total_tokens(short_term) > target_tokens:
    remove_oldest_short()
```

#### 6.2.7 完整伪代码

```python
def compress_short_term(self) -> int:
    """执行压缩，返回新生成的长期记忆数量。"""
    new_long_count = 0
    last_round_produced = True

    while self._should_compress():
        batch = self._select_compression_batch()

        if len(batch) < 3:
            break  # 太少，不值得压缩

        # 构造提示词
        prompt = self._build_compression_prompt(batch)

        # 调用 LLM
        summary = self._call_llm_for_summary(prompt)

        if summary and summary.strip():
            # 成功：创建长期记忆
            avg_importance = sum(m["importance"] for m in batch) / len(batch)
            self.add_memory(
                content=summary.strip(),
                mem_type="long",
                importance=round(avg_importance, 2),
                tags=["compressed"],
                metadata={"original_count": len(batch)},
            )
            # 删除已压缩的短期记忆
            for m in batch:
                self.delete_memory(m["id"])
            new_long_count += 1
            last_round_produced = True
        elif summary is not None:
            # LLM 返回空：仍然删除 batch 避免死循环
            for m in batch:
                self.delete_memory(m["id"])
            logger.warning("LLM 压缩返回空结果，已丢弃该批次")
            last_round_produced = True
        else:
            # LLM 调用异常：仅删除一半避免丢失重要信息
            for m in batch[: max(len(batch) // 2, 5)]:
                self.delete_memory(m["id"])
            logger.error("LLM 压缩调用失败，已删除部分条目")
            if not last_round_produced:
                break  # 连续两轮失败，终止
            last_round_produced = False

    # 兜底：仍超标则强制删除最旧条目
    self._enforce_thresholds()
    return new_long_count
```

#### 6.2.8 压缩的测试要点

- 模拟 LLM 返回固定字符串（通过依赖注入或 monkeypatch `_call_llm_for_summary`）。
- 验证压缩后短期记忆数量减少、长期记忆增加。
- 验证 LLM 返回空时不创建长期记忆但短期记忆仍被删除。
- 验证 LLM 异常时不会丢失全部短期记忆。
- 验证连续失败时循环能正确终止而非无限重试。
- 验证 `metadata.original_count` 记录正确。

---

### 6.3 注入上下文格式（MemoryInjector）

#### 6.3.1 输出格式

**工作记忆段**（放在最前面，若无则省略此段）：

```
[当前任务上下文]
- {content1}
- {content2}
```

**长期记忆段**（检索结果，若无则省略此段）：

```
[相关历史记忆]
- {content} (相关性: {score:.2f})
- {content} (相关性: {score:.2f})
```

**最终注入文本**：

```
[记忆上下文 - 由 Memory 模块自动生成]

[当前任务上下文]
- 用户希望使用 pytest 进行单元测试
- 当前正在修改 src/query/chat_llm.py

[相关历史记忆]
- 用户偏好每行代码不超过 120 字符 (相关性: 0.72)
- 项目使用 MiniMax API，模型为 DeepSeek-V3 (相关性: 0.65)
```

#### 6.3.2 Token 截断

1. 先计算工作记忆段的 token 数。
2. 若工作记忆段已超过 `max_tokens * 0.6`，截断工作记忆（丢弃末尾条目），为长期记忆保留至少 40% 空间。
3. 长期记忆按 `importance * final_score` 降序排列，依次加入直到总 token 数超过 `max_tokens`。
4. 被截断的条目记录 `logging.debug`。

#### 6.3.3 空上下文

若工作记忆为空且检索无结果，返回空字符串 `""`。调用方（未来集成时）检测到空字符串则不注入。

---

### 6.4 遗忘策略（forget）

遗忘是长期记忆的"垃圾回收"机制，参考 Hermes 的基于时间 + 重要性的自动淘汰。

#### 6.4.1 删除条件

对每条 `type="long"` 的记忆：

```
age_days = (time.time() - memory["timestamp"]) / 86400

if age_days >= older_than_days * 3:
    → 强制删除（年龄已达阈值 3 倍，即使重要性高也清理）
elif age_days >= older_than_days and memory["importance"] <= importance_below:
    → 低重要性过期删除
else:
    → 保留
```

#### 6.4.2 保护机制

- `importance > 0.8` 且 `access_count > 10` 的高价值记忆，即使满足强制删除条件也**再保留 1 倍时间**（即 `older_than_days * 4` 时才强制删除）。
- `tags` 包含 `"pinned"` 的记忆**永不自动遗忘**（需手动 `delete_memory`）。

#### 6.4.3 后处理

若本次 `forget()` 删除了任何条目，调用 `MemoryRetrieval.rebuild_index()` 重建 TF‑IDF 索引。

#### 6.4.4 伪代码

```python
def forget(self, older_than_days: int = 30,
           importance_below: float = 0.2) -> int:
    now = time.time()
    deleted_count = 0
    to_delete = []

    for mem in self._store.get_all("long"):
        age_days = (now - mem["timestamp"]) / 86400

        # 保护 pinned 记忆
        if "pinned" in mem.get("tags", []):
            continue

        # 高价值记忆延缓删除
        grace_multiplier = 4 if (mem["importance"] > 0.8
                                 and mem["access_count"] > 10) else 3

        if age_days >= older_than_days * grace_multiplier:
            to_delete.append(mem["id"])
        elif (age_days >= older_than_days
              and mem["importance"] <= importance_below):
            to_delete.append(mem["id"])

    for mem_id in to_delete:
        self._store.delete(mem_id)
        deleted_count += 1

    if deleted_count > 0:
        self._retrieval.rebuild_index()
        logger.info(f"遗忘操作完成：删除 {deleted_count} 条长期记忆")

    return deleted_count
```

---

### 6.5 文件存储（MemoryStore）

#### 6.5.1 存储路径

```
{config.memory.storage_path}/memories.json
```

默认路径：`.memdir/memories.json`（相对于项目根目录）。

#### 6.5.2 写策略

- **全量写入**：每次修改（add / update / delete）后完整序列化整个内存列表到 JSON 文件。
- **原子写入**：先写入临时文件 `memories.json.tmp`，写入成功后 `os.replace()` 原子替换原文件（Windows 上也保证原子性）。
- **备份**：替换前将原文件复制为 `memories.json.bak.1`，最多保留 3 个滚动备份（`.bak.1`、`.bak.2`、`.bak.3`）。
- 初期不支持并发写，多实例场景由外部调用者自行保证互斥。

#### 6.5.3 读策略

- **启动时全量加载**：`MemoryStore.__init__()` 中读取 `memories.json`，解析为 `List[Dict]`，保存在 `self._memories`。
- **运行时**：所有读写操作直接修改 `self._memories`，写操作后调用 `_flush()` 同步到磁盘。
- **延迟加载优化**：记忆总数 > 10000 时，考虑分文件存储（当前版本不实现，保留扩展点）。

#### 6.5.4 数据完整性与恢复

```
读取流程:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            raise ValueError("根元素不是列表")
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"记忆文件损坏或不存在: {e}")
        # 尝试从备份恢复
        for bak_path in sorted_backups():
            try:
                with open(bak_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    logger.info(f"从备份 {bak_path} 恢复成功")
                    break
            except Exception:
                continue
        else:
            raw = []  # 所有备份都失败，初始化为空

    条目级校验:
    valid_memories = []
    for item in raw:
        try:
            validated = self._validate_item(item)
            valid_memories.append(validated)
        except Exception as e:
            logger.warning(f"跳过损坏条目: {e}")
    self._memories = valid_memories
```

#### 6.5.5 JSON 序列化配置

```python
json.dumps(data, ensure_ascii=False, indent=2, default=str)
```

- `ensure_ascii=False`：支持中文直接写入。
- `indent=2`：人可读。
- `default=str`：防止 datetime 等非标准类型导致序列化崩溃。

---

### 6.6 工作记忆生命周期

#### 6.6.1 内部结构

```python
self._working_memories: List[Dict] = []
```

工作记忆不持久化，仅存在于 `MemoryManager` 实例的内存中。

#### 6.6.2 生命周期操作

| 操作 | 方法 | 说明 |
|------|------|------|
| 添加 | `add_memory(..., type="working")` | 追加到 `self._working_memories`，`timestamp = time.time()` |
| 清空 | `clear_working_memory()` | `self._working_memories.clear()` |
| 转移 | `persist_working_to_short()` | 遍历 `self._working_memories`，每条作为 `type="short"` 调用 `add_memory` 持久化，然后清空列表。返回转移条数。 |
| Token 限制 | `_enforce_working_token_limit()` | 内部方法：当工作记忆总 token 数超过 `working_memory_max_tokens` 时，按 `importance` 升序删除末尾条目（保留最重要的）。 |

#### 6.6.3 任务边界

当前由**外部调用者**负责在适当时机调用：
- 新任务开始时 → `clear_working_memory()`
- 任务完成/阶段结束时 → `persist_working_to_short()`

Memory 模块自身不主动判断任务边界。

---

## 7. 配置项（扩展 config.yaml）

在 `config.yaml` 中添加以下段落：

```yaml
memory:
  enabled: true                           # 是否启用记忆模块
  storage_path: ".memdir"                 # 持久化文件目录（相对于项目根目录）
  short_term_max_entries: 50              # 短期记忆条目数阈值
  short_term_max_tokens: 8000             # 短期记忆 token 总量阈值
  long_term_max_inject: 5                 # 每次注入的长期记忆最大条数
  working_memory_max_tokens: 2000         # 工作记忆 token 上限
  similarity_threshold: 0.15              # 检索相似度最低阈值
  compress_batch_size: 20                 # 每批压缩的短期记忆条数
  compress_llm_model: "DeepSeek"          # 压缩时使用的 LLM 模型名
  forget_older_than_days: 30              # 遗忘：超过此天数的记忆纳入候选
  forget_importance_below: 0.2            # 遗忘：重要性低于此值的记忆纳入候选
```

**配置加载**：由 `src/utility/config_loader.py` 统一加载，通过 `SimpleNamespace` 支持点号访问（如 `config.memory.storage_path`）。`enabled=False` 时，MemoryManager 的 `inject_context()` 始终返回空字符串，其他方法正常执行（不阻塞）。

---

## 8. 文件结构

```
D:/AI/MyClaude/src/memory/
├── __init__.py             # 导出 MemoryManager
├── memory_manager.py       # MemoryManager（Facade）
├── memory_store.py         # MemoryStore（JSON 持久化）
├── memory_retrieval.py     # MemoryRetrieval（TF‑IDF 检索）
├── memory_compressor.py    # MemoryCompressor（LLM 压缩）
├── memory_injector.py      # MemoryInjector（上下文格式化）
└── test_memory.py          # 单元测试（由 add_tests Skill 生成）
```

**依赖限制**：
- **允许导入**：`src/utility/config_loader`、`src/utility/normal_utility`、`src/query/chat_llm`（仅 `memory_compressor.py` 使用）。
- **禁止导入**：`src/query/query_loop`、`src/llm_tool/*`、`src/cli/*`、`src/message/*`。
- **标准库依赖**：`json`、`os`、`time`、`uuid`、`logging`、`math`、`re`、`pathlib`。

---

## 9. 单元测试要求

使用 `add_tests` Skill 生成 `test_memory.py`，必须覆盖以下场景（每个场景至少一个测试函数）：

| 测试场景 | 验证要点 |
|---------|---------|
| **CRUD 完整流程** | 添加 work/short/long 三种类型 → 按 ID 查询 → 更新 content/importance/tags → 删除 → 确认删除后查询返回空。 |
| **检索排序与权重** | 添加 10 条记忆，使用相关查询词检索 → 验证高 importance + 高 access_count 的条目排在前面 → 验证 `final_score` 公式正确。 |
| **压缩生成长期记忆** | 填充 60 条短期记忆（超过阈值 50）→ monkeypatch LLM 返回固定总结 → 调用 `compress_short_term()` → 验证短期记忆减少至阈值 80% 以下 → 验证生成了带有 `["compressed"]` 标签的长期记忆 → 验证 `metadata.original_count` 正确。 |
| **压缩异常处理** | monkeypatch LLM 返回空字符串 → 验证不生成长期记忆但短期记忆仍被删除 → monkeypatch LLM 抛出异常 → 验证不崩溃且正确回退。 |
| **注入上下文格式** | 添加 3 条工作记忆 + 5 条长期记忆 → 调用 `inject_context()` → 验证输出包含 `[当前任务上下文]` 和 `[相关历史记忆]` → 验证不超过 `max_tokens`。 |
| **遗忘策略** | 添加 10 条长期记忆，设置不同的 `timestamp`（模拟 10/30/90/120 天前）和 `importance` → 调用 `forget()` → 验证仅删除满足条件的条目 → 验证 pinned 记忆不被删除 → 验证高价值记忆获得宽限期。 |
| **持久化与恢复** | 使用 `tmp_path` → 创建 MemoryManager → 添加记忆 → 销毁实例 → 重新创建实例 → 验证所有记忆完整恢复。 |
| **损坏文件恢复** | 手动写入非法 JSON 到 `tmp_path/memories.json` → 创建 MemoryManager → 验证不崩溃 → 验证自动回退到空列表或备份。 |
| **空状态行为** | MemoryManager 无任何记忆时 → `get_memories()` 返回 `[]` → `inject_context()` 返回 `""` → `compress_short_term()` 返回 0 → `forget()` 返回 0。 |

**测试隔离**：
- 所有测试必须使用 `tmp_path` fixture，禁止写入真实 `.memdir`。
- LLM 调用必须通过 `monkeypatch` 或依赖注入模拟，禁止在测试中发起真实 API 请求。
- 每个测试函数独立创建 MemoryManager 实例，避免测试间状态污染。

---

## 10. 非功能性需求

| 需求 | 规格 |
|------|------|
| **并发模型** | 纯同步，不使用 `async/await`。 |
| **依赖最小化** | 仅依赖标准库 + `pyyaml`（配置）+ `numpy`（可选，检索回退到纯 Python）。不依赖 `sklearn`、`tiktoken`（均为可选）。 |
| **性能** | 1000 条长期记忆的检索耗时 < 100ms（纯 Python TF‑IDF）。 |
| **鲁棒性** | 单条记忆损坏、LLM 调用失败、JSON 解析错误均不导致模块崩溃，全部捕获并以 `logging` 记录。 |
| **日志** | 使用 `logging.getLogger(__name__)`，级别可配置。关键路径：add/delete/compress/forget 记录 `INFO`，检索记录 `DEBUG`，异常记录 `ERROR`。 |
| **编码** | 所有文件 UTF-8，JSON 序列化时 `ensure_ascii=False`。 |
| **路径** | 所有文件操作使用绝对路径，基于 `config.memory.storage_path` 拼接。 |

---

## 11. 未来集成预留（当前不实现）

以下集成点已在架构中预留接口，当前版本**不实现**：

| 集成点 | 位置 | 说明 |
|--------|------|------|
| 记忆注入到系统提示词 | `src/message/llm_api_msg.py` | 在 `_load_system_prompt()` 末尾调用 `MemoryManager.inject_context()`，将返回文本追加到系统提示词之后（作为 `user` 消息，前缀 `[记忆上下文]`）。 |
| 任务边界自动检测 | `src/query/query_loop.py` | 在 `QueryLoop.run()` 开始时调用 `clear_working_memory()`；在每轮 LLM 回复后调用 `add_memory(type="short")` 记录工具调用结果和关键决策。 |
| 会话结束时压缩 | `src/query/query_loop.py` | 在 QueryLoop 退出前调用 `compress_short_term()` 和 `persist_working_to_short()`。 |
| 记忆可视化 | 新模块 `src/memory/memory_viz.py` | 命令行浏览记忆（按类型/标签过滤、分页显示）、导出为 Markdown 或 HTML 报告。 |
| 记忆搜索 CLI | `src/myclaude.py` | 新增 `/memory search <query>` 命令，直接调用 `get_memories()` 并打印结果。 |
| 向量数据库后端 | `src/memory/memory_retrieval.py` | 将 TF‑IDF 替换为 ChromaDB / FAISS 等向量数据库，用于 >10000 条记忆的高性能检索。保留 `MemoryRetrieval` 的抽象接口以支持后端切换。 |

