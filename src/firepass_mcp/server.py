#!/usr/bin/env python3
"""FirePass MCP Server — Agentic coding harness for Kimi K2.5 Turbo via Fireworks AI.

Gives the model a tool loop so it can read/write files, run commands, and search code
autonomously until the task is done.

Configuration via environment variables:
    FIREWORKS_API_KEY  — Required. Your Fireworks AI API key.
    FIREPASS_MODEL     — Model ID (default: accounts/fireworks/routers/kimi-k2p5-turbo).
    FIREPASS_BASH_TIMEOUT — Shell command timeout in seconds (default: 60).
    FIREPASS_MAX_OUTPUT   — Max chars per tool result (default: 50000).
    FIREPASS_MAX_READ     — Max chars per file read (default: 100000).
"""

import json
import os
import shlex
import subprocess
from itertools import islice
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

API_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
MODEL = os.environ.get("FIREPASS_MODEL", "accounts/fireworks/routers/kimi-k2p5-turbo")
BASH_TIMEOUT = int(os.environ.get("FIREPASS_BASH_TIMEOUT", "60"))
OUTPUT_CAP = int(os.environ.get("FIREPASS_MAX_OUTPUT", "50000"))
READ_CAP = int(os.environ.get("FIREPASS_MAX_READ", "100000"))
WRITE_CAP = 1_000_000  # 1MB max write size
CONTEXT_CAP = 200_000  # Max characters for message context

# Dangerous ripgrep flags that could allow code execution
RIPGREP_BLOCKED_FLAGS = {"--pre", "--pre-glob", "-z", "--search-zip"}

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents. Returns numbered lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"},
                    "offset": {
                        "type": "integer",
                        "description": "Start line, 1-based (default 1)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max lines to read (default: all)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"},
                    "content": {"type": "string", "description": "File content"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in a file. old_text must match exactly once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"},
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to find (must match once)",
                    },
                    "new_text": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Run a shell command (timeout configurable via FIREPASS_BASH_TIMEOUT). "
                "Use for: git, python, uv, ruff, pytest, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command"},
                    "cwd": {
                        "type": "string",
                        "description": "Working directory (default: agent cwd)",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ripgrep",
            "description": "Fast regex search via rg. Returns file:line: match.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {
                        "type": "string",
                        "description": "File or dir (default: cwd)",
                    },
                    "flags": {
                        "type": "string",
                        "description": "Extra rg flags, e.g. '-i -l -C3 --type py -w'",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_find",
            "description": "Find files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob, e.g. '**/*.py'",
                    },
                    "path": {
                        "type": "string",
                        "description": "Base directory (default: cwd)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ast_grep",
            "description": (
                "Structural code search via ast-grep (sg). "
                "Matches code patterns, not text. "
                "Example: 'def $FUNC($$$ARGS)' or 'console.log($$$)'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "ast-grep pattern"},
                    "path": {
                        "type": "string",
                        "description": "File or dir (default: cwd)",
                    },
                    "lang": {
                        "type": "string",
                        "description": "Language: python, javascript, typescript, rust, go …",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jq",
            "description": "Query/transform JSON with jq.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "jq filter"},
                    "file": {"type": "string", "description": "JSON file path"},
                    "input_json": {
                        "type": "string",
                        "description": "JSON string (used if file omitted)",
                    },
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List directory contents with sizes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory (default: cwd)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tree",
            "description": "Directory tree. Excludes __pycache__, .git, node_modules, .venv.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Root dir (default: cwd)",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Max depth (default: 3)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": (
                "Signal task completion. MUST call when finished. "
                "The result is returned to the caller — keep it to a concise "
                "executive summary (one page max). List files changed, key "
                "findings, or decisions made. No verbose logs or full code dumps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "string",
                        "description": (
                            "One-page executive summary: what you did/found, "
                            "files changed, key decisions. Be concise."
                        ),
                    },
                },
                "required": ["result"],
            },
        },
    },
]

# Researcher: no write_file, edit_file, or bash
RESEARCHER_TOOL_DEFS = [
    t
    for t in TOOL_DEFS
    if t["function"]["name"] not in ("write_file", "edit_file", "bash")
]

# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------


def _validate_path(path: str, cwd: str) -> Path:
    """Resolve path and verify it doesn't escape the working directory."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(cwd) / p
    resolved = p.resolve()
    cwd_resolved = Path(cwd).resolve()
    if not resolved.is_relative_to(cwd_resolved):
        raise ValueError(f"Path {path} escapes working directory {cwd}")
    return resolved


def _run(cmd: str | list[str], cwd: str) -> str:
    if isinstance(cmd, str):
        cmd = ["bash", "-c", cmd]
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=BASH_TIMEOUT
        )
        out = r.stdout
        if r.stderr:
            out += f"\n[stderr]\n{r.stderr}"
        if r.returncode != 0:
            out += f"\n[exit {r.returncode}]"
        return out[:OUTPUT_CAP]
    except subprocess.TimeoutExpired:
        return f"[ERROR] timed out after {BASH_TIMEOUT}s"
    except Exception as e:
        return f"[ERROR] {e}"


def exec_tool(name: str, args: dict, cwd: str) -> str:
    """Execute a tool call, return result string."""

    if name == "read_file":
        try:
            p = _validate_path(args["path"], cwd)
            off = max(args.get("offset", 1) - 1, 0)
            lim = args.get("limit")
            lines = []
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                if lim is not None:
                    # islice already skips to offset — lines are the final selection
                    lines = list(islice(f, off, off + lim))
                else:
                    # Skip to offset, then read with a character budget
                    for _ in islice(f, off):
                        pass
                    total_chars = 0
                    for line in f:
                        lines.append(line)
                        total_chars += len(line)
                        if total_chars > READ_CAP:
                            break
            numbered = [f"{i + off + 1:>6}|{line}" for i, line in enumerate(lines)]
            return "".join(numbered)[:READ_CAP]
        except Exception as e:
            return f"[ERROR] {e}"

    if name == "write_file":
        try:
            content = args["content"]
            if len(content) > WRITE_CAP:
                return f"[ERROR] Content size ({len(content)} bytes) exceeds maximum allowed ({WRITE_CAP} bytes)"
            p = _validate_path(args["path"], cwd)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return f"Wrote {len(content)} bytes to {args['path']}"
        except Exception as e:
            return f"[ERROR] {e}"

    if name == "edit_file":
        try:
            p = _validate_path(args["path"], cwd)
            content = p.read_text()
            old = args["old_text"]
            count = content.count(old)
            if count == 0:
                return f"[ERROR] old_text not found in {args['path']}"
            if count > 1:
                return (
                    f"[ERROR] old_text matches {count} locations — "
                    "must match exactly once. Add more surrounding context."
                )
            p.write_text(content.replace(old, args["new_text"], 1))
            return f"Edited {args['path']}"
        except Exception as e:
            return f"[ERROR] {e}"

    if name == "bash":
        cmd_cwd = args.get("cwd")
        if cmd_cwd:
            _validate_path(cmd_cwd, cwd)
        return _run(args["command"], cmd_cwd or cwd)

    if name == "ripgrep":
        flags = args.get("flags", "")
        try:
            path = str(_validate_path(args.get("path") or cwd, cwd))
        except ValueError as e:
            return f"[ERROR] {e}"
        # Build command as list to avoid shell injection via flags
        cmd = ["rg", "--no-heading", "-n"]
        if flags:
            flag_tokens = shlex.split(flags)
            for token in flag_tokens:
                # Block dangerous long flags
                if token in RIPGREP_BLOCKED_FLAGS or any(
                    token.startswith(f"{blocked}=") for blocked in RIPGREP_BLOCKED_FLAGS
                ):
                    return f"[ERROR] Blocked dangerous flag: {token}"
                # Block combined short flags containing 'z' (e.g. -iz, -nz)
                if token.startswith("-") and not token.startswith("--") and "z" in token:
                    return f"[ERROR] Blocked dangerous flag: {token} (contains -z)"
            cmd.extend(flag_tokens)
        cmd.append(args["pattern"])
        cmd.append(path)
        return _run(cmd, cwd)

    if name == "glob_find":
        try:
            base = _validate_path(args.get("path") or cwd, cwd)
            cwd_resolved = Path(cwd).resolve()
            matches = [
                m
                for m in sorted(base.glob(args["pattern"]))
                if m.resolve().is_relative_to(cwd_resolved)
            ]
            return "\n".join(str(m) for m in matches[:500]) or "(no matches)"
        except Exception as e:
            return f"[ERROR] {e}"

    if name == "ast_grep":
        try:
            path = str(_validate_path(args.get("path") or cwd, cwd))
        except ValueError as e:
            return f"[ERROR] {e}"
        lang = args.get("lang", "")
        cmd = ["sg", "--pattern", args["pattern"]]
        if lang:
            cmd.extend(["--lang", lang])
        cmd.append(path)
        return _run(cmd, cwd)

    if name == "jq":
        f = args.get("file")
        inp = args.get("input_json")
        expr = args["expression"]
        if f:
            try:
                validated_f = str(_validate_path(f, cwd))
            except ValueError as e:
                return f"[ERROR] {e}"
            return _run(["jq", expr, validated_f], cwd)
        if inp:
            return _run(f"echo {shlex.quote(inp)} | jq {shlex.quote(expr)}", cwd)
        return "[ERROR] provide file or input_json"

    if name == "list_dir":
        try:
            path = _validate_path(args.get("path") or cwd, cwd)
            return _run(["ls", "-lah", str(path)], cwd)
        except Exception as e:
            return f"[ERROR] {e}"

    if name == "tree":
        try:
            path = _validate_path(args.get("path") or cwd, cwd)
            depth = str(args.get("max_depth", 3))
            return _run(
                [
                    "tree",
                    "-L",
                    depth,
                    "-I",
                    "__pycache__|.git|node_modules|.venv|.mypy_cache",
                    str(path),
                ],
                cwd,
            )
        except Exception as e:
            return f"[ERROR] {e}"

    if name == "done":
        return args.get("result", "Done.")

    return f"[ERROR] unknown tool: {name}"


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


async def _stream_response(
    client: httpx.AsyncClient, headers: dict, payload: dict
) -> dict:
    """Make a streaming API call, collect deltas into a complete message dict."""
    payload["stream"] = True
    content_parts: list[str] = []
    tool_calls: dict[int, dict] = {}

    async with client.stream("POST", API_URL, headers=headers, json=payload) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode(errors="replace")[:500]
            raise RuntimeError(f"[API ERROR {resp.status_code}] {body}")
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})

            if delta.get("content"):
                content_parts.append(delta["content"])

            for tc in delta.get("tool_calls", []):
                idx = tc["index"]
                if idx not in tool_calls:
                    tool_calls[idx] = {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                if tc.get("id"):
                    tool_calls[idx]["id"] = tc["id"]
                fn = tc.get("function", {})
                if fn.get("name"):
                    tool_calls[idx]["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    tool_calls[idx]["function"]["arguments"] += fn["arguments"]

    msg: dict = {"role": "assistant"}
    content = "".join(content_parts)
    if content:
        msg["content"] = content
    if tool_calls:
        msg["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    return msg


def _activity_entry(fn: str, args: dict, cwd: str) -> str:
    """Format a single tool call as a compact activity log line."""
    if fn == "read_file":
        return f"[read]  {args.get('path', '?')}"
    if fn == "write_file":
        path = args.get("path", "?")
        size = len(args.get("content", ""))
        return f"[write] {path} ({size} bytes)"
    if fn == "edit_file":
        return f"[edit]  {args.get('path', '?')}"
    if fn == "bash":
        cmd = args.get("command", "?")
        if len(cmd) > 80:
            cmd = cmd[:77] + "..."
        return f"[bash]  {cmd}"
    if fn == "ripgrep":
        pat = args.get("pattern", "?")
        path = args.get("path") or cwd
        return f"[rg]    {pat!r} in {path}"
    if fn == "glob_find":
        return f"[glob]  {args.get('pattern', '?')} in {args.get('path') or cwd}"
    if fn == "ast_grep":
        return f"[sg]    {args.get('pattern', '?')}"
    if fn == "jq":
        return f"[jq]    {args.get('expression', '?')}"
    if fn == "list_dir":
        return f"[ls]    {args.get('path') or cwd}"
    if fn == "tree":
        return f"[tree]  {args.get('path') or cwd} (depth={args.get('max_depth', 3)})"
    return f"[{fn}]"


def _format_activity_footer(activity: list[str], iterations: int) -> str:
    """Build the structured activity footer appended to done() results."""
    if not activity:
        return ""
    lines = [
        "",
        "--- ACTIVITY LOG ---",
        f"Iterations: {iterations}  |  Tool calls: {len(activity)}",
    ]
    for entry in activity:
        lines.append(entry)
    lines.append("--- END ---")
    return "\n".join(lines)


def _enforce_context_budget(messages: list[dict]) -> None:
    """Truncate old tool messages if total context exceeds CONTEXT_CAP."""
    total = sum(len(msg.get("content", "")) for msg in messages)
    if total <= CONTEXT_CAP:
        return
    # Truncate oldest tool messages first, keeping recent context
    for msg in messages:
        if msg.get("role") == "tool" and msg["content"] != "[truncated]":
            freed = len(msg["content"]) - len("[truncated]")
            msg["content"] = "[truncated]"
            total -= freed
            if total <= CONTEXT_CAP:
                break


async def agent_loop(
    system: str,
    prompt: str,
    context: str | None,
    tools: list[dict],
    cwd: str,
    max_iterations: int,
) -> str:
    """Run a tool-calling loop until the model calls done() or stops calling tools."""

    messages: list[dict] = [{"role": "system", "content": system}]
    user_msg = f"Task:\n{prompt}"
    if context:
        user_msg = f"Context:\n{context}\n\n{user_msg}"
    user_msg += f"\n\nWorking directory: {cwd}"
    messages.append({"role": "user", "content": user_msg})

    api_key = os.environ.get("FIREWORKS_API_KEY", "")
    if not api_key:
        return "[ERROR] FIREWORKS_API_KEY not set. Get one at https://fireworks.ai"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    activity: list[str] = []

    async with httpx.AsyncClient(timeout=300) as client:
        for iteration in range(max_iterations):
            try:
                msg = await _stream_response(
                    client,
                    headers,
                    {
                        "model": MODEL,
                        "messages": messages,
                        "tools": tools,
                        "max_tokens": 16384,
                        "temperature": 0.2,
                    },
                )
            except (RuntimeError, httpx.HTTPError, httpx.StreamError, OSError) as e:
                return str(e) + _format_activity_footer(activity, iteration + 1)

            # Validate all tool calls parse correctly before appending assistant message
            tool_calls = msg.get("tool_calls", [])
            parse_errors = []
            parsed_calls = []
            for tc in tool_calls:
                fn = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                    parsed_calls.append((tc, fn, fn_args))
                except (json.JSONDecodeError, KeyError) as e:
                    parse_errors.append(f"[ERROR] Failed to parse {fn} arguments: {e}")

            if parse_errors:
                # Return error without appending malformed assistant message
                return "\n".join(parse_errors) + _format_activity_footer(
                    activity, iteration + 1
                )

            messages.append(msg)

            if not tool_calls:
                result = msg.get("content") or "(empty response)"
                return result + _format_activity_footer(activity, iteration + 1)

            for tc, fn, fn_args in parsed_calls:
                activity.append(_activity_entry(fn, fn_args, cwd))

                if fn == "done":
                    summary = fn_args.get("result", msg.get("content", "Done."))
                    return summary + _format_activity_footer(activity, iteration + 1)

                result = exec_tool(fn, fn_args, cwd)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result,
                    }
                )

            _enforce_context_budget(messages)

    return f"[Hit iteration limit ({max_iterations})]" + _format_activity_footer(
        activity, max_iterations
    )


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

WORKER_SYSTEM = """\
You are a senior software engineer inside an agentic coding harness.
You have tools to read, write, and edit files, run shell commands, \
and search code with ripgrep, ast-grep, jq, and glob.

Workflow:
1. Explore first — use ripgrep/glob/tree to understand the codebase before editing
2. Read files before editing them
3. Make surgical edits with edit_file; use write_file for new files only
4. Verify changes — run tests, linters, or type checkers after editing
5. Call done(result="...") when the task is complete

Rules:
- Write complete implementations — no placeholders or TODOs
- One logical change at a time
- Preserve existing code style and conventions
- Handle errors with clear messages — never swallow exceptions

Output:
Your done() result is returned to a supervising agent. Keep it to a ONE PAGE \
executive summary: files changed, what you did, key decisions. No full code dumps, \
no verbose logs. The supervisor can read the files if it needs details."""

RESEARCHER_SYSTEM = """\
You are a technical researcher inside a read-only agent harness.
You have tools to read files, search code with ripgrep/ast-grep/jq/glob, \
list directories, and view directory trees.
You CANNOT write, edit files, or run shell commands.

Workflow:
1. Use tree/list_dir to orient yourself in the codebase
2. Use ripgrep/ast_grep/glob to find relevant code
3. Read files to understand implementations
4. Call done(result="...") with your analysis

Rules:
- Decompose complex questions from first principles
- Consider multiple hypotheses before concluding
- Be direct and factual — no filler or hedging
- If uncertain, state what you know vs. what you don't

Output:
Your done() result is returned to a supervising agent. Keep it to a ONE PAGE \
executive summary: key findings, file locations, conclusions. No full file dumps — \
cite file:line references. The supervisor can read the files itself."""

# ---------------------------------------------------------------------------
# MCP entry points
# ---------------------------------------------------------------------------

mcp = FastMCP("firepass-mcp")


@mcp.tool()
async def firepass_worker(
    prompt: str,
    cwd: str = "",
    context: str = "",
    max_iterations: int = 50,
) -> str:
    """Run a coding task with FirePass worker (Kimi K2.5 Turbo + tool loop).

    The worker can read/write/edit files, run bash, search with ripgrep/ast-grep/jq,
    and iterate autonomously until done.

    Args:
        prompt: The coding task.
        cwd: Working directory (default: home).
        context: Optional file contents, errors, or specs to pre-load.
        max_iterations: Max tool-call rounds (default 50).
    """
    return await agent_loop(
        WORKER_SYSTEM,
        prompt,
        context or None,
        TOOL_DEFS,
        cwd or os.path.expanduser("~"),
        max_iterations,
    )


@mcp.tool()
async def firepass_researcher(
    prompt: str,
    cwd: str = "",
    context: str = "",
    max_iterations: int = 30,
) -> str:
    """Run a research task with FirePass researcher (Kimi K2.5 Turbo + read-only tool loop).

    The researcher can read files, search with ripgrep/ast-grep/jq/glob,
    and iterate autonomously. No file writes or shell commands.

    Args:
        prompt: Research question or analysis task.
        cwd: Working directory (default: home).
        context: Optional file contents, docs, or code to pre-load.
        max_iterations: Max tool-call rounds (default 30).
    """
    return await agent_loop(
        RESEARCHER_SYSTEM,
        prompt,
        context or None,
        RESEARCHER_TOOL_DEFS,
        cwd or os.path.expanduser("~"),
        max_iterations,
    )


def main():
    mcp.run()


if __name__ == "__main__":
    main()
