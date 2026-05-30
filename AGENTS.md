# MyClaude — Agent Instructions

## Entry & Launch

- **Run**: `python -m src.myclaude` (root `src/myclaude.py`)
- **With proxy** (corp env): `start.bat` sets `http_proxy`, `https_proxy`, `NODE_TLS_REJECT_UNAUTHORIZED=0`
- **Deps**: `pip_install.bat` → `openai`, `rich`, `prompt-toolkit`, `PyYAML`
- **Role flag**: `-r mycode` (only `mycode` supported; others rejected)
- **CLI commands**: `/quit`, `/clear`, `/help`, `/tokens`, `/t N`, `/r mem`, `/pt`, `/h2m`, `/cs`, `/save`

## Config

- `config/config.yaml` — global settings (model params, paths, cli, memory backend)
- `config/model_key.yaml` — API keys and provider config (DeepSeek, MiniMax, memory sub-models)
- `config/memory/memory1.yaml` / `memory2.yaml` — per-backend settings (loaded automatically based on `memory.backend`)
- All config merged into `SimpleNamespace` at `global_cfg = load_config()` (module-level singleton in `src/utility/config_loader.py`)

## Testing & Linting

- **Test**: `pytest` (config in `pytest.ini`, testpaths = `src code_output`)
- **Lint**: `ruff check .` (rules in `ruff.toml`: E/F/W/I/N/UP/PL/RUF/SIM/B/A/COM/C4)
- **No typecheck or pre-commit hooks configured**

## Architecture

- **Sync only** — no async/await in core loop. `stream_chat()` returns `(content, is_truncated, reasoning_content)`.
- **Flow**: `myclaude.py → CLI → QueryLoop.run() → chat_llm → parse_tools → execute_tools → loop`
- **Display decoupling**: `QueryLoop` takes callbacks from CLI (`print_info`, `print_llm_rsp`, `print_tool_call`, etc.). No direct `console.print()` in business logic.
- **Memory backends**: `memory_1` (FAISS vector), `memory_2` (LLM-based recall). Factory in `src/memory/factory.py`. Default: `memory_2`. Falls back to `NoopMemory` on failure.
- **Session logs**: Written as Markdown or HTML to `log/` dir. Log format set in `config.yaml log.format`.

## LLM Tool Protocol (XML-based)

Tools parsed from LLM output via regex in `src/llm_tool/tool_executor.py`:
- `<file_view path="..." limit="N" offset="N"/>`
- `<create path="..." summary="...">content</create>`
- `<str_replace path="..." summary="..."><old>...</old><new>...</new></str_replace>`
- `<bash>command</bash>`
- `<use_skill name="..."/>`
- `<done>summary</done>`

## Critical Rules (from sys_prompt)

1. **Tools and `<done>` must be in SEPARATE turns** — never in the same response
2. **All file paths must be absolute** — no relative paths or bare filenames
3. **`summary` attribute required** on `<create>` and `<str_replace>` (≤50 chars)
4. **Never overwrite existing files** — `file_create` blocks on existing non-empty files; use `file_view → str_replace` instead
5. **`<str_replace>` before file_view is forbidden** — `<old>` must be verbatim from prior `file_view`
6. **`<new>` must close with `</new>`** — never `</old>` or other tags
7. **No `role="system"` mid-conversation** — MiniMax API rejects it. Use `role="user"` with prefix text
8. **`<done>` regex is lenient**: `(?:</done>|$)` — allows missing close tag
9. **Tool results are dicts** `{"role": "user", "content": "..."}`, never lists
10. **Windows native only** — no `ls`/`grep`/`rm`/`curl` etc. Use `dir`/`findstr`/`del`/PowerShell equivalents

## Path Conventions

| Purpose | Directory | Example |
|---------|-----------|---------|
| Source code | `D:/AI/MyClaude/src/<subdir>/` | `src/query/chat_llm.py` |
| Specs/docs | `D:/AI/MyClaude/spec/` | `spec/spider_spec.md` |
| Temp/test output | `D:/AI/MyClaude/code_output/` | `code_output/demo.py` |
| Skills | `D:/AI/MyClaude/skill/<name>/SKILL.md` | `skill/add_tests/SKILL.md` |
| Logs | `D:/AI/MyClaude/log/` | auto-generated |
| Memory data | `D:/AI/MyClaude/memory_storage/` | auto-managed |

- Code files must go in a **subdirectory** of `src/` — never in `src/` root
- Subdirectories named with lowercase+underscore: `src/cli/`, `src/query/`, `src/llm_tool/`, `src/utility/`, `src/memory/`, `src/message/`, `src/tools/`

## Git

- `origin` → Gitee, `github` → GitHub
- Push: `git push` (default Gitee), `git push github master`
- `log/`, `code_output/`, `context/`, `spec/`, `memory_storage/`, `tests/` content gitignored (dirs preserved via `.gitkeep`)

## A2A_EX Subsystem

- `src/A2A_EX/` contains experimental A2A protocol implementation (orchestrator, system tests, agent cards)
- Not wired into main CLI. Run via `python -m src.A2A_EX.system_test.main` etc.
