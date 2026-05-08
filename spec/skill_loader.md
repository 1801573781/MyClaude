# Skill Loader 模块需求规格文档

## 一、目标
实现 Skill 的渐进式加载，避免一次性把所有技能指令塞进对话上下文。

三层加载含义：
- L1（元数据层）：会话启动时只告知 AI 有哪些 Skill 可用（名称 + 一句话描述）
- L2（完整指令层）：当用户请求匹配某个 Skill 时，再加载该 Skill 的完整操作手册（流程、规则、示例）
- L3（资源层）：执行过程中，通过文件工具按需读取 Skill 附带的脚本/模板文件

## 二、模块职责
扫描 skill/ 文件夹，提供四个能力：
1. 获取所有 Skill 的元数据列表（每个 Skill 的 name 和 description）
2. 根据技能名，加载该 Skill 的完整正文（去掉元数据块后的 Markdown）
3. 根据技能名和相对路径，读取 Skill 目录下的资源文件（如 .py、.txt）的文本内容
4. 生成一段可供系统提示词使用的 Markdown 清单（列出所有 Skill 的名称和描述）

## 三、目录与文件约定
- Skill 根目录：D:/AI/MyClaude/skill/（由 config.yaml 中的 project_root 拼接得到）
- 每个 Skill 是一个子文件夹，文件夹名即为技能名
- 每个 Skill 子文件夹内必须包含 SKILL.md
- SKILL.md 开头必须有 YAML frontmatter，例如：
  ---
  name: add_tests
  description: 为 Python 函数生成 pytest 单元测试
  ---
  # Skill: add_tests
  ...（详细指令）
- 可选资源文件：skill/<技能名>/scripts/、skill/<技能名>/templates/ 等

## 四、功能要求（不涉及具体实现细节）
1. 扫描与缓存：首次调用时扫描 skill/ 下所有一级子目录，提取每个 SKILL.md 的 frontmatter 中的 name 和 description，并缓存结果。后续调用直接返回缓存，不重复读取磁盘。
2. 容错：单个 Skill 的 SKILL.md 缺失、格式错误或无 name 字段时，该 Skill 被忽略，不影响其他 Skill。skill/ 目录不存在或为空时，不报错，返回空列表/空字符串。
3. 完整正文加载：传入技能名，返回该技能 SKILL.md 中去掉 frontmatter 之后的所有内容。应缓存已加载的内容。
4. 资源文件加载：传入技能名和相对路径（如 "scripts/gen.py"），返回该文件的 UTF-8 文本内容。若文件不存在或不是普通文件，返回空。
5. 清单生成：基于已扫描的元数据，生成如下格式的 Markdown 文本（用于注入系统提示词）：
   ## Installed Skills (L1 Metadata)
   当用户请求匹配以下技能时，你必须按对应 SKILL.md 的完整指令执行：
   - **技能名**: 描述
   ...
   *（完整技能指令在匹配后自动加载）*
   如果没有 Skill，返回空字符串。
6. 单例访问：提供全局获取函数 get_skill_loader()，确保整个进程共用一个加载器实例。

## 五、集成点
- 系统提示词注入：在 src/message/llm_api_msg.py 的 _load_system_prompt() 中，调用 format_skills_prompt() 并将返回内容追加到系统提示词末尾。
- 按需加载完整 Skill：在 src/query/query_loop.py 的 run() 中，根据用户输入匹配技能名（匹配策略可简单用关键词或后续优化），然后调用 load_full_skill(skill_name) 获取完整指令，作为一条 user 消息插入 api_messages 中（位置在用户原始输入之后）。
- 资源文件：Skill 的完整指令中可以写“如需辅助脚本，请执行 <file_view path=".../skill/xxx/scripts/yyy.py"/>”，利用现有 file_view 工具读取，不需要特殊处理。

## 六、实现约束
- 纯同步实现，不使用 async/await
- 仅依赖 Python 标准库（pathlib, re, typing）及已有的 config_loader
- 编码：UTF-8
- 文件读写异常应静默处理（单个 Skill 损坏不中断整体）

## 七、验收标准（仅供内部测试）
1. 在 skill/add_tests/SKILL.md 中放一个带正确 frontmatter 的文件，调用元数据接口能返回包含 add_tests 的记录。
2. 调用完整正文接口返回的内容不含开头的 --- 块。
3. 存在资源文件时能正确读取内容，不存在时返回空。
4. 生成的提示词片段包含技能名和描述。
5. 重复调用不会重复读取磁盘（通过人工简单验证即可）。
