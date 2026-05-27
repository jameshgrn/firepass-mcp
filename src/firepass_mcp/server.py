#!/usr/bin/env python3
"""FirePass MCP Server — Agentic coding harness for Kimi K2.6 Turbo via Fireworks AI.

Gives the model a tool loop so it can read/write files, run commands, and search code
autonomously until the task is done.

Configuration via environment variables:
    FIREWORKS_API_KEY  — Required. Your Fireworks AI API key.
    FIREPASS_MODEL     — Model ID (default: accounts/fireworks/routers/kimi-k2p6-turbo).
    FIREPASS_BASH_TIMEOUT — Shell command timeout in seconds (default: 60).
    FIREPASS_MAX_OUTPUT   — Max chars per tool result (default: 50000).
    FIREPASS_MAX_READ     — Max chars per file read (default: 100000).
"""

import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

from firepass_mcp.messages import (
    enforce_context_budget,
    parse_tool_calls,
)
from firepass_mcp.tools import (
    READONLY_BLOCKED_TOOLS,
    READONLY_TOOL_DEFS,
    TOOL_DEFS,
    clamp_max_iterations,
    clamp_max_review_rounds,
    exec_tool,
    format_tool_activity,
    normalize_cwd,
    readonly_tool_names,
    tool_names,
)

API_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
MODEL = os.environ.get("FIREPASS_MODEL", "accounts/fireworks/routers/kimi-k2p6-turbo")


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


async def _stream_response(
    client: httpx.AsyncClient, headers: dict, payload: dict
) -> dict:
    """Make a streaming API call, collect deltas into a complete message dict."""
    stream_payload = {**payload, "stream": True}
    content_parts: list[str] = []
    tool_calls: dict[int, dict] = {}

    async with client.stream(
        "POST", API_URL, headers=headers, json=stream_payload
    ) as resp:
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
            except json.JSONDecodeError as e:
                excerpt = data_str[:200]
                raise RuntimeError(
                    f"[API ERROR] invalid streaming JSON: {e}: {excerpt}"
                )
            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})

            if delta.get("content"):
                content_parts.append(delta["content"])

            for tc in delta.get("tool_calls", []):
                idx = tc.get("index")
                if idx is None:
                    continue
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


def _xml_escape(s: str) -> str:
    """Escape &, <, > for XML (in that order)."""
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    return s


def _xml_envelope(status: str, iterations: int, activity: list[str], body: str) -> str:
    """Wrap a result body and activity list in a firepass_run XML envelope."""
    lines = [
        f'<firepass_run status="{status}" iterations="{iterations}" tool_calls="{len(activity)}">',
        f"<result>{_xml_escape(body)}</result>",
        "<activity>",
    ]
    for entry in activity:
        lines.append(f"<call>{_xml_escape(entry)}</call>")
    lines.append("</activity>")
    lines.append("</firepass_run>")
    return "\n".join(lines)


def _retag_envelope(xml: str, new_tag: str) -> str:
    """Rename the outermost XML open/close tags to new_tag.

    Envelopes produced by `_xml_envelope` are anchored: the open tag
    starts at index 0, and the close tag ends at the final character.
    Body content is escaped via `_xml_escape`, so any occurrence of the
    old tag name inside the body has its `<` and `>` already turned
    into `&lt;` and `&gt;` — only the outer pair remains as raw tag
    boundaries. We rely on those two anchors instead of a substring
    `.replace()`, which would happen to work today but could match a
    body-text occurrence of the literal tag name if escaping were ever
    skipped or relaxed.
    """
    if not xml.startswith("<"):
        return xml

    # Read the open tag name: stops at the first space, slash, or '>'.
    cursor = 1
    while cursor < len(xml) and xml[cursor] not in (" ", ">", "/", "\t", "\n"):
        cursor += 1
    old_tag = xml[1:cursor]
    if not old_tag:
        return xml

    close_marker = f"</{old_tag}>"
    if not xml.endswith(close_marker):
        return xml

    head = f"<{new_tag}" + xml[cursor : len(xml) - len(close_marker)]
    return head + f"</{new_tag}>"


def _extract_result_body(envelope: str) -> str:
    """Return the substring between the first <result> and the first </result>."""
    open_tag = "<result>"
    close_tag = "</result>"
    start = envelope.find(open_tag)
    if start == -1:
        return ""
    start += len(open_tag)
    end = envelope.find(close_tag, start)
    if end == -1:
        return ""
    return envelope[start:end]


async def agent_loop(
    system: str,
    prompt: str,
    context: str | None,
    tools: list[dict],
    cwd: str,
    max_iterations: int,
    blocked_tools: frozenset[str] = frozenset(),
) -> str:
    """Run a tool-calling loop until the model calls done() or stops calling tools."""

    messages: list[dict] = [{"role": "system", "content": system}]
    user_msg = f"Task:\n{prompt}"
    if context:
        user_msg = f"Context:\n{context}\n\n{user_msg}"
    user_msg += f"\n\nWorking directory: {cwd}"
    messages.append({"role": "user", "content": user_msg})

    activity: list[str] = []

    api_key = os.environ.get("FIREWORKS_API_KEY", "")
    if not api_key:
        return _xml_envelope(
            "api_error",
            0,
            activity,
            "[ERROR] FIREWORKS_API_KEY not set. Get one at https://fireworks.ai",
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=300) as client:
        for iteration in range(max_iterations):
            try:
                messages = enforce_context_budget(messages)
            except ValueError as e:
                return _xml_envelope(
                    "context_overflow", iteration + 1, activity, f"[ERROR] {e}"
                )

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
                return _xml_envelope("api_error", iteration + 1, activity, str(e))

            # Validate all tool calls parse correctly before appending assistant message
            tool_calls = msg.get("tool_calls", [])
            parsed_calls, parse_errors = parse_tool_calls(tool_calls)

            if parse_errors:
                # Return error without appending malformed assistant message
                return _xml_envelope(
                    "tool_call_parse_error",
                    iteration + 1,
                    activity,
                    "\n".join(parse_errors),
                )

            messages.append(msg)

            if not tool_calls:
                result = msg.get("content") or "(empty response)"
                return _xml_envelope("completed", iteration + 1, activity, result)

            for call in parsed_calls:
                # Enforce runtime tool allowlist
                if call.name in blocked_tools:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.tool_call_id,
                            "content": f"[ERROR] Tool '{call.name}' is not allowed in this mode",
                        }
                    )
                    continue

                activity.append(format_tool_activity(call.name, call.arguments, cwd))

                if call.name == "done":
                    summary = exec_tool(call.name, call.arguments, cwd)
                    return _xml_envelope("completed", iteration + 1, activity, summary)

                result = exec_tool(call.name, call.arguments, cwd)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.tool_call_id,
                        "content": result,
                    }
                )

    return _xml_envelope(
        "max_iterations",
        max_iterations,
        activity,
        f"[Hit iteration limit ({max_iterations})]",
    )


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

WORKER_TOOL_LIST = ", ".join(tool_names())
READONLY_TOOL_LIST = ", ".join(readonly_tool_names())

WORKER_SYSTEM = f"""\
You are a senior software engineer inside an agentic coding harness.
You have tools available: {WORKER_TOOL_LIST}.

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

RESEARCHER_SYSTEM = f"""\
You are a technical researcher inside a read-only agent harness.
You have read-only tools available: {READONLY_TOOL_LIST}.
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

REVIEWER_SYSTEM = f"""\
You are a senior code reviewer inside a read-only agent harness.
You have read-only tools available: {READONLY_TOOL_LIST}.
You CANNOT write, edit files, or run shell commands.

Workflow:
1. Orient — use tree/list_dir/glob to understand project structure
2. Read the files or diff under review
3. Search for related code — callers, tests, type definitions, similar patterns
4. Evaluate in order: correctness → security → architecture → performance → style
5. Call done(result="...") with your review

Rules:
- Every issue must cite file:line and explain *why* it matters
- Distinguish blocking issues from nits — label severity (bug, security, design, nit)
- Suggest concrete fixes, not vague complaints
- Acknowledge what's done well — don't only list problems
- Check for: error handling gaps, missing edge cases, resource leaks, \
race conditions, injection vectors, API misuse
- If tests exist, verify they cover the changed code paths

Output:
Your done() result is returned to a supervising agent. Structure it as:

**Summary**: 1-2 sentence overall assessment.
**Blocking**: Issues that must be fixed (bug, security, correctness).
**Suggestions**: Non-blocking improvements (design, performance, style).
**Good**: What's done well.

Cite file:line for every item. No full code dumps.

Begin your output with the literal line "VERDICT: APPROVE" or "VERDICT: NEEDS-FIXES" — nothing else on that line. The trio loop reads this line verbatim."""

# ---------------------------------------------------------------------------
# MCP entry points
# ---------------------------------------------------------------------------

mcp = FastMCP("firepass-mcp")


@mcp.tool()
async def firepass_worker(
    prompt: str,
    cwd: str,
    context: str = "",
    max_iterations: int = 60,
) -> str:
    """Run a coding task with FirePass worker (Kimi K2.6 Turbo + tool loop).

    The worker can read/write/edit files, run bash, search with ripgrep/ast-grep/jq,
    and iterate autonomously until done.

    Args:
        prompt: The coding task.
        cwd: Working directory to sandbox file access to.
        context: Optional file contents, errors, or specs to pre-load.
        max_iterations: Max tool-call rounds (default 60).
    """
    normalized_cwd = normalize_cwd(cwd)
    clamped_iterations = clamp_max_iterations(max_iterations)
    result = await agent_loop(
        WORKER_SYSTEM,
        prompt,
        context or None,
        TOOL_DEFS,
        normalized_cwd,
        clamped_iterations,
    )
    return _retag_envelope(result, "firepass_worker")


@mcp.tool()
async def firepass_researcher(
    prompt: str,
    cwd: str,
    context: str = "",
    max_iterations: int = 60,
) -> str:
    """Run a research task with FirePass researcher (Kimi K2.6 Turbo + read-only tool loop).

    The researcher can read files, search with ripgrep/ast-grep/jq/glob,
    and iterate autonomously. No file writes or shell commands.

    Args:
        prompt: Research question or analysis task.
        cwd: Working directory to sandbox file access to.
        context: Optional file contents, docs, or code to pre-load.
        max_iterations: Max tool-call rounds (default 60).
    """
    normalized_cwd = normalize_cwd(cwd)
    clamped_iterations = clamp_max_iterations(max_iterations)
    result = await agent_loop(
        RESEARCHER_SYSTEM,
        prompt,
        context or None,
        READONLY_TOOL_DEFS,
        normalized_cwd,
        clamped_iterations,
        blocked_tools=READONLY_BLOCKED_TOOLS,
    )
    return _retag_envelope(result, "firepass_researcher")


@mcp.tool()
async def firepass_reviewer(
    prompt: str,
    cwd: str,
    context: str = "",
    max_iterations: int = 60,
) -> str:
    """Run a code review with FirePass reviewer (Kimi K2.6 Turbo + read-only tool loop).

    The reviewer can read files, search with ripgrep/ast-grep/jq/glob,
    and iterate autonomously. No file writes or shell commands.
    Returns structured review: blocking issues, suggestions, and what's done well.

    Args:
        prompt: What to review — files, a diff, a PR description, or a specific concern.
        cwd: Working directory to sandbox file access to.
        context: Optional diff, file contents, or PR description to pre-load.
        max_iterations: Max tool-call rounds (default 60).
    """
    normalized_cwd = normalize_cwd(cwd)
    clamped_iterations = clamp_max_iterations(max_iterations)
    result = await agent_loop(
        REVIEWER_SYSTEM,
        prompt,
        context or None,
        READONLY_TOOL_DEFS,
        normalized_cwd,
        clamped_iterations,
        blocked_tools=READONLY_BLOCKED_TOOLS,
    )
    return _retag_envelope(result, "firepass_reviewer")


def _build_trio_response(
    status: str, rounds: int, research_xml: str, rounds_xml: list[str]
) -> str:
    lines = [
        f'<firepass_trio status="{status}" rounds="{rounds}">',
        _retag_envelope(research_xml, "research"),
        "<rounds>",
    ]
    lines.extend(rounds_xml)
    lines.append("</rounds>")
    lines.append("</firepass_trio>")
    return "\n".join(lines)


async def _run_trio_chain(
    prompt: str,
    cwd: str,
    context: str,
    max_iterations: int,
    max_review_rounds: int,
) -> str:
    """Orchestrate researcher → worker → reviewer with optional fix loops."""
    # 1. Researcher
    research_xml = await firepass_researcher(
        prompt, cwd, context, max_iterations=max_iterations
    )
    if 'status="completed"' not in research_xml:
        return _build_trio_response("research_failed", 0, research_xml, [])

    # 2. First implementation
    research_body = _extract_result_body(research_xml)
    worker_context = f"Researcher findings:\n{research_body}"
    impl_xml = await firepass_worker(
        prompt, cwd, worker_context, max_iterations=max_iterations
    )
    if 'status="completed"' not in impl_xml:
        return _build_trio_response("implementation_failed", 0, research_xml, [])

    # 3. Review loop
    rounds_xml: list[str] = []
    rounds_used = 0

    while True:
        review_xml = await firepass_reviewer(
            prompt, cwd, impl_xml, max_iterations=max_iterations
        )
        rounds_used += 1

        round_xml = (
            f'<round n="{rounds_used}">\n'
            f"{_retag_envelope(impl_xml, 'implementation')}\n"
            f"{_retag_envelope(review_xml, 'review')}\n"
            f"</round>"
        )
        rounds_xml.append(round_xml)

        if 'status="completed"' not in review_xml:
            return _build_trio_response(
                "review_failed", rounds_used, research_xml, rounds_xml
            )

        if (
            "NEEDS-FIXES" not in _extract_result_body(review_xml)
            or rounds_used >= max_review_rounds
        ):
            status = (
                "needs_fixes"
                if (
                    "NEEDS-FIXES" in _extract_result_body(review_xml)
                    and rounds_used >= max_review_rounds
                )
                else "approved"
            )
            return _build_trio_response(status, rounds_used, research_xml, rounds_xml)

        # Next round: append reviewer feedback to context and re-run worker
        reviewer_body = _extract_result_body(review_xml)
        worker_context = f"{worker_context}\n\nReviewer feedback:\n{reviewer_body}"
        impl_xml = await firepass_worker(
            prompt, cwd, worker_context, max_iterations=max_iterations
        )
        if 'status="completed"' not in impl_xml:
            return _build_trio_response(
                "implementation_failed", rounds_used, research_xml, rounds_xml
            )


@mcp.tool()
async def firepass_trio(
    prompt: str,
    cwd: str,
    context: str = "",
    max_iterations: int = 60,
    max_review_rounds: int = 2,
) -> str:
    """Run a full FirePass trio: research → implement → review → (fix loop).

    Args:
        prompt: The coding task.
        cwd: Working directory to sandbox file access to.
        context: Optional file contents, errors, or specs to pre-load.
        max_iterations: Max tool-call rounds per sub-agent (default 60).
        max_review_rounds: Max worker+reviewer fix rounds (default 2).
    """
    normalized_cwd = normalize_cwd(cwd)
    clamped_iterations = clamp_max_iterations(max_iterations)
    clamped_rounds = clamp_max_review_rounds(max_review_rounds)
    return await _run_trio_chain(
        prompt,
        normalized_cwd,
        context,
        clamped_iterations,
        clamped_rounds,
    )


def main():
    mcp.run()


if __name__ == "__main__":
    main()
