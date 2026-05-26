# Changelog

All notable changes to firepass-mcp. Versions follow [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-05-26

### Added

- `firepass_trio` — fourth MCP tool that chains the existing three. Runs researcher → worker → reviewer; if the reviewer body contains `NEEDS-FIXES` and rounds remain, the worker re-runs with reviewer findings appended to context, then the reviewer re-runs. New parameter `max_review_rounds` (default 2) is clamped at `MAX_REVIEW_ROUNDS_LIMIT = 5`.
- XML response envelope on all four MCP tools. Replaces the previous `--- ACTIVITY LOG ---` ASCII footer. Single-tool shape is `<firepass_worker status="..." iterations="N" tool_calls="M"><result>…</result><activity><call>…</call>…</activity></firepass_worker>`. Trio shape nests `<research>` and `<rounds><round n="1"><implementation>…</implementation><review>…</review></round>…</rounds>` inside `<firepass_trio>`.
- Status taxonomy. Single tools emit `completed`, `max_iterations`, `api_error`, `context_overflow`, or `tool_call_parse_error`. The trio additionally emits `approved`, `needs_fixes`, `research_failed`, `implementation_failed`, `review_failed`.
- `tests/test_server.py` — first test suite in the project, 55 tests covering bash cwd resolution, jq stdin handling, glob/read traversal caps, parse-error reporting, iteration clamping across all four entry points, the `blocked_tools` runtime allowlist, two-phase context-budget edge cases, the XML envelope under every status, direct `_retag_envelope` unit tests, and trio loop-back paths.
- Development section in the README with the four commands: `uv sync`, `uv run pytest -q tests/test_server.py`, `uv run ruff check src tests`, `uv run ty check src`.
- `pytest>=9.0.3` as a dev dependency under `[dependency-groups] dev` in `pyproject.toml`.

### Changed

- Tool schemas and executors moved from `server.py` into `firepass_mcp/tools.py`. Every tool now has a frozen-dataclass argument contract enforced at runtime: unknown fields are rejected via `additionalProperties: false` and a typed `_parse_*` step per tool. The schema is the same JSON Schema dict that's surfaced to the model, so the contract is a single source of truth.
- Chat-message helpers moved into `firepass_mcp/messages.py`. `enforce_context_budget` now compacts in two phases: phase 1 truncates the oldest tool outputs to `[truncated]`; phase 2 compacts assistant `tool_call.arguments` to `{}`. If neither phase frees enough, it raises rather than silently exceeding.
- Iteration limits are clamped at `MAX_ITERATIONS_LIMIT = 200` in every entry point. Values ≤ 0 raise.
- `bash` cwd is validated against the sandbox before subprocess launch.
- `jq` input is now passed via subprocess stdin instead of `echo | jq` (no shell quoting).
- `glob_find` bounds traversal at 500 matches, not just output truncation after collection.
- `read_file` honors `READ_CAP` while streaming, so a huge `limit` no longer pulls the whole file.
- README rewritten: lists the actual 11 worker tools (8 read-only) by their real names, enumerates the six blocked ripgrep flags, describes the module split, documents `firepass_trio` and the XML envelope with examples, and notes the 200-iteration cap inline with the default.

### Fixed

- Empty `arguments` string from OpenAI streaming aggregation now parses as `{}` instead of raising `JSONDecodeError`.
- `_enforce_context_budget` no longer deep-copies the entire message list when the conversation is already under budget.
- Em-dash regression in the `edit_file` error message restored.

### Internal

- Hardened `_retag_envelope`: anchors on position 0 for the open tag and `endswith()` for the close tag. The earlier `.replace(..., 1)` form happened to work because `_xml_escape` strips body angle brackets, but the anchored form stays correct if escaping is ever skipped.

## [0.1.3] — earlier

- Skill documentation, default-value docstring fixes, version bump.

## [0.1.2] — earlier

- Reviewer role added; default iteration limit raised to 60 for all roles.

## [0.1.1] — earlier

- Initial PyPI publish; install instructions simplified.

## [0.1.0] — earlier

- Initial release: `firepass_worker` and `firepass_researcher` MCP tools backed by Kimi K2.6 Turbo on Fireworks AI.
