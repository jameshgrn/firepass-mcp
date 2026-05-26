"""Focused tests for concrete review findings."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from firepass_mcp.messages import (
    _compact_tool_calls,
    _message_size,
    enforce_context_budget,
    parse_tool_calls,
)
from firepass_mcp.server import (
    _stream_response,
    agent_loop,
    firepass_researcher,
    firepass_reviewer,
    firepass_trio,
    firepass_worker,
)
from firepass_mcp.tools import (
    MAX_ITERATIONS_LIMIT,
    MAX_REVIEW_ROUNDS_LIMIT,
    READONLY_BLOCKED_TOOLS,
    READONLY_TOOL_DEFS,
    _validate_path,
    clamp_max_iterations,
    clamp_max_review_rounds,
    exec_tool,
    normalize_cwd,
)


# ---------------------------------------------------------------------------
# 1. bash cwd resolution
# ---------------------------------------------------------------------------


def test_bash_relative_cwd_resolved_to_absolute(monkeypatch, tmp_path):
    """Relative cwd must be resolved to an absolute path before subprocess runs."""
    recorded_cwd = []

    def fake_run(cmd, cwd):
        recorded_cwd.append(cwd)
        return "ok"

    monkeypatch.setattr("firepass_mcp.tools._run", fake_run)

    sandbox = str(tmp_path)
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    rel = "subdir"
    result = exec_tool("bash", {"command": "pwd", "cwd": rel}, sandbox)

    assert result == "ok"
    assert len(recorded_cwd) == 1
    assert Path(recorded_cwd[0]).is_absolute()
    assert Path(recorded_cwd[0]).resolve() == subdir.resolve()


def test_bash_absolute_cwd_unchanged(monkeypatch, tmp_path):
    """Absolute cwd stays absolute."""
    recorded_cwd = []

    def fake_run(cmd, cwd):
        recorded_cwd.append(cwd)
        return "ok"

    monkeypatch.setattr("firepass_mcp.tools._run", fake_run)

    sandbox = str(tmp_path)
    abs_cwd = str(tmp_path.resolve())
    result = exec_tool("bash", {"command": "pwd", "cwd": abs_cwd}, sandbox)

    assert result == "ok"
    assert recorded_cwd[0] == abs_cwd


def test_bash_cwd_escapes_sandbox_rejected(tmp_path):
    """cwd outside the sandbox must be rejected."""
    sandbox = str(tmp_path.resolve())
    bad_cwd = "/tmp"
    result = exec_tool("bash", {"command": "pwd", "cwd": bad_cwd}, sandbox)
    assert result.startswith("[ERROR]")
    assert "escapes" in result


# ---------------------------------------------------------------------------
# 2. jq stdin handling
# ---------------------------------------------------------------------------


def test_jq_input_json_uses_subprocess_stdin(monkeypatch, tmp_path):
    recorded = {}

    def fake_run(cmd, cwd, input_text=None):
        recorded["cmd"] = cmd
        recorded["cwd"] = cwd
        recorded["input_text"] = input_text
        return "ok"

    monkeypatch.setattr("firepass_mcp.tools._run", fake_run)

    input_json = '{"name":"firepass"}'
    result = exec_tool(
        "jq",
        {"expression": ".name", "input_json": input_json},
        str(tmp_path),
    )

    assert result == "ok"
    assert recorded == {
        "cmd": ["jq", ".name"],
        "cwd": str(tmp_path),
        "input_text": input_json,
    }


def test_jq_rejects_ambiguous_input_sources(tmp_path):
    result = exec_tool(
        "jq",
        {"expression": ".", "file": "data.json", "input_json": "{}"},
        str(tmp_path),
    )

    assert result.startswith("[ERROR] Invalid jq arguments")
    assert "exactly one" in result


def test_exec_tool_rejects_missing_required_argument(tmp_path):
    result = exec_tool("bash", {}, str(tmp_path))

    assert result.startswith("[ERROR] Invalid bash arguments")
    assert "missing required field 'command'" in result


def test_read_file_with_limit_stops_at_read_cap(tmp_path):
    """A huge limit must not force the server to materialize the whole file."""
    # 10k lines × ~200 bytes = ~2 MB, well over the 100 KB READ_CAP.
    # The reader is supposed to stop once it has filled the byte budget,
    # not consume the whole file just because `limit` is huge.
    target = tmp_path / "large.txt"
    line = ("x" * 200) + "\n"
    target.write_text(line * 10_000)

    result = exec_tool(
        "read_file",
        {"path": str(target), "limit": 1_000_000},
        str(tmp_path),
    )

    # Bounded output. The 100 KB cap is `READ_CAP` in tools.py.
    assert len(result) <= 100_000
    # Confirm output is non-trivial — i.e. we read *something*, not just an error.
    assert "x" * 200 in result


def test_glob_find_stops_after_result_cap(monkeypatch, tmp_path):
    """glob_find should cap traversal work, not only cap output after sorting."""
    consumed = 0

    def fake_glob(self, pattern):
        nonlocal consumed
        assert pattern == "**/*"
        for index in range(10_000):
            consumed += 1
            yield self / f"file-{index}.txt"

    monkeypatch.setattr(Path, "glob", fake_glob)

    result = exec_tool("glob_find", {"pattern": "**/*"}, str(tmp_path))

    assert consumed < 10_000
    assert len(result.splitlines()) <= 501


def test_agent_loop_reports_malformed_tool_call_shape(monkeypatch, tmp_path):
    async def fake_stream_response(client, headers, payload):
        return {"role": "assistant", "tool_calls": [{"id": "tc1"}]}

    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    monkeypatch.setattr("firepass_mcp.server._stream_response", fake_stream_response)

    result = asyncio.run(
        agent_loop(
            system="system",
            prompt="task",
            context=None,
            tools=[],
            cwd=str(tmp_path),
            max_iterations=1,
        )
    )

    assert 'status="tool_call_parse_error"' in result
    assert "tool call" in result
    assert "function" in result


def test_parse_tool_calls_rejects_missing_tool_call_id():
    parsed, errors = parse_tool_calls(
        [
            {
                "function": {
                    "name": "bash",
                    "arguments": '{"command":"pwd"}',
                }
            }
        ]
    )

    assert parsed == []
    assert errors == ["[ERROR] Malformed tool call 0: missing tool call id"]


def test_stream_response_does_not_mutate_payload():
    captured_payload: dict[str, object] = {}

    payload = {"model": "test-model", "messages": []}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(request.content))
        return httpx.Response(
            200,
            content='data: {"choices":[{"delta":{"content":"ok"}}]}\ndata: [DONE]\n',
        )

    async def run_stream() -> dict:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await _stream_response(client, {}, payload)

    result = asyncio.run(run_stream())

    assert result == {"role": "assistant", "content": "ok"}
    assert payload == {"model": "test-model", "messages": []}
    assert captured_payload["stream"] is True


# ---------------------------------------------------------------------------
# 3. Context budgeting
# ---------------------------------------------------------------------------


def test_message_size_counts_content():
    assert _message_size({"role": "user", "content": "hello"}) == 5


def test_message_size_counts_tool_call_arguments():
    msg = {
        "role": "assistant",
        "content": "hi",
        "tool_calls": [
            {
                "id": "tc1",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command":"ls"}'},
            }
        ],
    }
    assert _message_size(msg) == 2 + len('{"command":"ls"}')


def test_message_size_empty_tool_calls():
    msg = {"role": "assistant", "content": "", "tool_calls": []}
    assert _message_size(msg) == 0


def test_compact_tool_calls_replaces_arguments():
    msg = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "tc1",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command":"ls -la"}'},
            }
        ],
    }
    compacted, freed = _compact_tool_calls(msg)
    assert freed == len('{"command":"ls -la"}') - len("{}")
    assert compacted["tool_calls"][0]["function"]["arguments"] == "{}"
    assert msg["tool_calls"][0]["function"]["arguments"] == '{"command":"ls -la"}'


def test_compact_tool_calls_skips_already_empty():
    msg = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "tc1",
                "type": "function",
                "function": {"name": "bash", "arguments": "{}"},
            }
        ],
    }
    compacted, freed = _compact_tool_calls(msg)
    assert freed == 0
    assert compacted == msg


def testenforce_context_budget_truncates_tool_messages():
    # Make total well over CONTEXT_CAP so both tool messages must be truncated
    messages = [
        {"role": "system", "content": "x" * 100},
        {"role": "user", "content": "y" * 100},
        {"role": "tool", "tool_call_id": "tc1", "content": "a" * 200_000},
        {"role": "tool", "tool_call_id": "tc2", "content": "b" * 200_000},
    ]
    compacted = enforce_context_budget(messages)
    assert compacted[2]["content"] == "[truncated]"
    assert compacted[3]["content"] == "[truncated]"
    assert messages[2]["content"] == "a" * 200_000


def testenforce_context_budget_compacts_assistant_tool_calls():
    """When tool messages aren't enough, assistant tool_call arguments are compacted."""
    big_args = "x" * 150_000
    messages = [
        {"role": "system", "content": "s" * 50_000},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": big_args},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc1", "content": "[truncated]"},
    ]
    compacted = enforce_context_budget(messages)
    assert compacted[1]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert messages[1]["tool_calls"][0]["function"]["arguments"] == big_args


def testenforce_context_budget_preserves_message_validity():
    """Compacted messages still have required fields for API replay."""
    big_args = "z" * 250_000
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": big_args},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc1", "content": "ok"},
    ]
    compacted = enforce_context_budget(messages)
    assert compacted[0]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert compacted[0]["tool_calls"][0]["id"] == "tc1"
    assert compacted[0]["tool_calls"][0]["type"] == "function"
    assert compacted[1]["tool_call_id"] == "tc1"


def testenforce_context_budget_rejects_noncompactable_overage():
    messages = [
        {"role": "system", "content": "x" * 150_000},
        {"role": "user", "content": "y" * 150_000},
    ]

    with pytest.raises(ValueError, match="Context budget still exceeds limit"):
        enforce_context_budget(messages)


# ---------------------------------------------------------------------------
# 4. max_iterations clamping
# ---------------------------------------------------------------------------


def testclamp_max_iterations_valid():
    assert clamp_max_iterations(1) == 1
    assert clamp_max_iterations(60) == 60
    assert clamp_max_iterations(MAX_ITERATIONS_LIMIT) == MAX_ITERATIONS_LIMIT


def testclamp_max_iterations_negative_raises():
    with pytest.raises(ValueError, match="must be > 0"):
        clamp_max_iterations(-1)


def testclamp_max_iterations_zero_raises():
    with pytest.raises(ValueError, match="must be > 0"):
        clamp_max_iterations(0)


def testclamp_max_iterations_huge_clamped():
    assert clamp_max_iterations(999_999) == MAX_ITERATIONS_LIMIT


def test_firepass_worker_clamps_max_iterations_at_boundary(monkeypatch, tmp_path):
    recorded = {}

    async def fake_agent_loop(
        system,
        prompt,
        context,
        tools,
        cwd,
        max_iterations,
        blocked_tools=frozenset(),
    ):
        recorded["max_iterations"] = max_iterations
        recorded["cwd"] = cwd
        return "ok"

    monkeypatch.setattr("firepass_mcp.server.agent_loop", fake_agent_loop)

    result = asyncio.run(
        firepass_worker("task", str(tmp_path), max_iterations=MAX_ITERATIONS_LIMIT + 1)
    )

    assert result == "ok"
    assert recorded["max_iterations"] == MAX_ITERATIONS_LIMIT
    assert recorded["cwd"] == str(tmp_path.resolve())


# ---------------------------------------------------------------------------
# 5. Helpers
# ---------------------------------------------------------------------------


def test_validate_path_resolves_relative(tmp_path):
    sandbox = str(tmp_path)
    rel = "nested"
    resolved = _validate_path(rel, sandbox)
    assert resolved.is_absolute()
    assert resolved.resolve() == (tmp_path / "nested").resolve()


def test_normalize_cwd_requires_existing_dir(tmp_path):
    assert normalize_cwd(str(tmp_path)) == str(tmp_path.resolve())


def test_normalize_cwd_rejects_missing():
    with pytest.raises(ValueError, match="does not exist"):
        normalize_cwd("/nonexistent/path/12345")


# ---------------------------------------------------------------------------
# 6. Researcher / reviewer iteration clamping (parity with worker)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entrypoint",
    [firepass_worker, firepass_researcher, firepass_reviewer],
    ids=["worker", "researcher", "reviewer"],
)
def test_each_entrypoint_clamps_max_iterations(monkeypatch, tmp_path, entrypoint):
    """All three MCP entry points must clamp max_iterations identically."""
    recorded = {}

    async def fake_agent_loop(
        system,
        prompt,
        context,
        tools,
        cwd,
        max_iterations,
        blocked_tools=frozenset(),
    ):
        recorded["max_iterations"] = max_iterations
        return "ok"

    monkeypatch.setattr("firepass_mcp.server.agent_loop", fake_agent_loop)

    result = asyncio.run(
        entrypoint("task", str(tmp_path), max_iterations=MAX_ITERATIONS_LIMIT + 50)
    )

    assert result == "ok"
    assert recorded["max_iterations"] == MAX_ITERATIONS_LIMIT


# ---------------------------------------------------------------------------
# 7. Read-only allowlist enforcement
# ---------------------------------------------------------------------------


def test_readonly_tool_defs_exclude_mutating_tools():
    """READONLY_TOOL_DEFS must not surface bash/write_file/edit_file to the model."""
    names = {t["function"]["name"] for t in READONLY_TOOL_DEFS}
    assert names.isdisjoint(READONLY_BLOCKED_TOOLS)
    assert "bash" not in names
    assert "write_file" not in names
    assert "edit_file" not in names
    # Sanity: read-side tools are present.
    assert "read_file" in names
    assert "ripgrep" in names
    assert "done" in names


def test_agent_loop_blocks_tools_at_runtime(monkeypatch, tmp_path):
    """If the model ever emits a blocked tool, the loop replies with [ERROR] and
    does not execute it. Belt-and-suspenders for the schema-level filter."""
    call_count = {"n": 0}
    exec_calls: list[str] = []

    async def fake_stream_response(client, headers, payload):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First turn: emit a forbidden bash call.
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command":"rm -rf /"}',
                        },
                    }
                ],
            }
        # Second turn: end the loop.
        return {"role": "assistant", "content": "stopped", "tool_calls": []}

    def fake_exec_tool(name, args, cwd):
        exec_calls.append(name)
        return "ran"

    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    monkeypatch.setattr("firepass_mcp.server._stream_response", fake_stream_response)
    monkeypatch.setattr("firepass_mcp.server.exec_tool", fake_exec_tool)

    result = asyncio.run(
        agent_loop(
            system="system",
            prompt="task",
            context=None,
            tools=READONLY_TOOL_DEFS,
            cwd=str(tmp_path),
            max_iterations=3,
            blocked_tools=READONLY_BLOCKED_TOOLS,
        )
    )

    # bash never reached exec_tool.
    assert exec_calls == []
    assert "stopped" in result


# ---------------------------------------------------------------------------
# 8. parse_tool_calls — empty arguments handling
# ---------------------------------------------------------------------------


def test_parse_tool_calls_treats_empty_arguments_as_empty_object():
    """OpenAI streaming aggregates argument deltas into a string and can leave it
    empty for tool calls with no parameters. That must parse as {} not error."""
    parsed, errors = parse_tool_calls(
        [
            {
                "id": "tc1",
                "type": "function",
                "function": {"name": "done", "arguments": ""},
            }
        ]
    )

    assert errors == []
    assert len(parsed) == 1
    assert parsed[0].arguments == {}


# ---------------------------------------------------------------------------
# 9. enforce_context_budget — no-op when under cap
# ---------------------------------------------------------------------------


def test_enforce_context_budget_skips_copy_when_under_cap():
    """Under-budget call should return the original list unchanged (identity),
    not a deep copy. Cheap optimization; covered by a test so it can't regress."""
    messages = [
        {"role": "system", "content": "small"},
        {"role": "user", "content": "also small"},
    ]
    result = enforce_context_budget(messages)
    assert result is messages


# ---------------------------------------------------------------------------
# 10. XML envelope formatting
# ---------------------------------------------------------------------------


def test_xml_envelope_wraps_done_result(monkeypatch, tmp_path):
    """End-to-end through firepass_worker: done() yields a wrapped XML envelope."""

    async def fake_stream_response(client, headers, payload):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {
                        "name": "done",
                        "arguments": '{"result": "All done"}',
                    },
                }
            ],
        }

    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    monkeypatch.setattr("firepass_mcp.server._stream_response", fake_stream_response)

    result = asyncio.run(firepass_worker("task", str(tmp_path), max_iterations=1))

    assert result.startswith('<firepass_worker status="completed"')
    assert "<result>" in result
    assert result.endswith("</firepass_worker>")


def test_xml_envelope_escapes_result_body(monkeypatch, tmp_path):
    """Special XML characters in the model-generated body must be escaped."""

    async def fake_stream_response(client, headers, payload):
        return {"role": "assistant", "content": "Use <div> & enjoy"}

    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    monkeypatch.setattr("firepass_mcp.server._stream_response", fake_stream_response)

    result = asyncio.run(
        agent_loop(
            system="system",
            prompt="task",
            context=None,
            tools=[],
            cwd=str(tmp_path),
            max_iterations=1,
        )
    )

    assert "<result>Use &lt;div&gt; &amp; enjoy</result>" in result
    assert "<firepass_run" in result


def test_xml_envelope_status_max_iterations(monkeypatch, tmp_path):
    """When the loop exhausts max_iterations, status='max_iterations'."""

    async def fake_stream_response(client, headers, payload):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": '{"command": "echo hi"}',
                    },
                }
            ],
        }

    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    monkeypatch.setattr("firepass_mcp.server._stream_response", fake_stream_response)
    monkeypatch.setattr("firepass_mcp.server.exec_tool", lambda name, args, cwd: "ok")

    result = asyncio.run(
        agent_loop(
            system="system",
            prompt="task",
            context=None,
            tools=[],
            cwd=str(tmp_path),
            max_iterations=1,
        )
    )

    assert 'status="max_iterations"' in result


def test_xml_envelope_status_api_error(monkeypatch, tmp_path):
    """When _stream_response raises, status='api_error'."""

    async def fake_stream_response(client, headers, payload):
        raise RuntimeError("[API ERROR 500] service unavailable")

    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    monkeypatch.setattr("firepass_mcp.server._stream_response", fake_stream_response)

    result = asyncio.run(
        agent_loop(
            system="system",
            prompt="task",
            context=None,
            tools=[],
            cwd=str(tmp_path),
            max_iterations=1,
        )
    )

    assert 'status="api_error"' in result


def test_xml_envelope_status_context_overflow(monkeypatch, tmp_path):
    """When enforce_context_budget raises, status='context_overflow'."""

    async def fake_stream_response(client, headers, payload):
        return {"role": "assistant", "content": "ok"}

    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    monkeypatch.setattr("firepass_mcp.server._stream_response", fake_stream_response)

    huge_system = "x" * 500_000

    result = asyncio.run(
        agent_loop(
            system=huge_system,
            prompt="task",
            context=None,
            tools=[],
            cwd=str(tmp_path),
            max_iterations=1,
        )
    )

    assert 'status="context_overflow"' in result


def test_xml_envelope_status_tool_call_parse_error(monkeypatch, tmp_path):
    """Malformed tool_call shapes yield status='tool_call_parse_error'."""

    async def fake_stream_response(client, headers, payload):
        return {"role": "assistant", "tool_calls": [{"id": "tc1"}]}

    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    monkeypatch.setattr("firepass_mcp.server._stream_response", fake_stream_response)

    result = asyncio.run(
        agent_loop(
            system="system",
            prompt="task",
            context=None,
            tools=[],
            cwd=str(tmp_path),
            max_iterations=1,
        )
    )

    assert 'status="tool_call_parse_error"' in result
    assert "tool call" in result
    assert "function" in result


# ---------------------------------------------------------------------------
# 11. clamp_max_review_rounds helper
# ---------------------------------------------------------------------------


def test_clamp_max_review_rounds_helper():
    assert clamp_max_review_rounds(1) == 1
    assert clamp_max_review_rounds(2) == 2
    assert clamp_max_review_rounds(MAX_REVIEW_ROUNDS_LIMIT) == MAX_REVIEW_ROUNDS_LIMIT
    with pytest.raises(ValueError, match="must be > 0"):
        clamp_max_review_rounds(-1)
    with pytest.raises(ValueError, match="must be > 0"):
        clamp_max_review_rounds(0)
    assert clamp_max_review_rounds(999_999) == MAX_REVIEW_ROUNDS_LIMIT


# ---------------------------------------------------------------------------
# 12. firepass_trio orchestration
# ---------------------------------------------------------------------------


def test_firepass_trio_approve_first_round(monkeypatch, tmp_path):
    """All sub-calls succeed immediately; trio status is approved with one round."""
    call_counts = {"researcher": 0, "worker": 0, "reviewer": 0}

    async def fake_researcher(prompt, cwd, context, max_iterations):
        call_counts["researcher"] += 1
        return '<firepass_researcher status="completed" iterations="1" tool_calls="0"><result>findings</result><activity></activity></firepass_researcher>'

    async def fake_worker(prompt, cwd, context, max_iterations):
        call_counts["worker"] += 1
        return '<firepass_worker status="completed" iterations="1" tool_calls="0"><result>code</result><activity></activity></firepass_worker>'

    async def fake_reviewer(prompt, cwd, context, max_iterations):
        call_counts["reviewer"] += 1
        return '<firepass_reviewer status="completed" iterations="1" tool_calls="0"><result>APPROVE</result><activity></activity></firepass_reviewer>'

    monkeypatch.setattr("firepass_mcp.server.firepass_researcher", fake_researcher)
    monkeypatch.setattr("firepass_mcp.server.firepass_worker", fake_worker)
    monkeypatch.setattr("firepass_mcp.server.firepass_reviewer", fake_reviewer)

    result = asyncio.run(firepass_trio("task", str(tmp_path)))

    assert 'status="approved"' in result
    assert 'rounds="1"' in result
    assert result.count("<round ") == 1
    assert call_counts["researcher"] == 1
    assert call_counts["worker"] == 1
    assert call_counts["reviewer"] == 1
    assert "<research " in result
    assert "<implementation " in result
    assert "<review " in result


def test_firepass_trio_loops_on_needs_fixes_then_approves(monkeypatch, tmp_path):
    """First reviewer says NEEDS-FIXES, second says APPROVE; two rounds, worker gets feedback."""
    call_counts = {"researcher": 0, "worker": 0, "reviewer": 0}
    worker_contexts: list[str] = []

    async def fake_researcher(prompt, cwd, context, max_iterations):
        call_counts["researcher"] += 1
        return '<firepass_researcher status="completed" iterations="1" tool_calls="0"><result>findings</result><activity></activity></firepass_researcher>'

    async def fake_worker(prompt, cwd, context, max_iterations):
        call_counts["worker"] += 1
        worker_contexts.append(context)
        return '<firepass_worker status="completed" iterations="1" tool_calls="0"><result>code</result><activity></activity></firepass_worker>'

    reviewer_calls = [0]

    async def fake_reviewer(prompt, cwd, context, max_iterations):
        call_counts["reviewer"] += 1
        reviewer_calls[0] += 1
        if reviewer_calls[0] == 1:
            return '<firepass_reviewer status="completed" iterations="1" tool_calls="0"><result>NEEDS-FIXES: fix X</result><activity></activity></firepass_reviewer>'
        return '<firepass_reviewer status="completed" iterations="1" tool_calls="0"><result>APPROVE</result><activity></activity></firepass_reviewer>'

    monkeypatch.setattr("firepass_mcp.server.firepass_researcher", fake_researcher)
    monkeypatch.setattr("firepass_mcp.server.firepass_worker", fake_worker)
    monkeypatch.setattr("firepass_mcp.server.firepass_reviewer", fake_reviewer)

    result = asyncio.run(firepass_trio("task", str(tmp_path), max_review_rounds=3))

    assert 'status="approved"' in result
    assert 'rounds="2"' in result
    assert result.count("<round ") == 2
    assert call_counts["researcher"] == 1
    assert call_counts["worker"] == 2
    assert call_counts["reviewer"] == 2
    # Second worker invocation must include the first reviewer's feedback in context
    assert "NEEDS-FIXES: fix X" in worker_contexts[1]


def test_firepass_trio_exhausts_rounds(monkeypatch, tmp_path):
    """Reviewer always returns NEEDS-FIXES; trio exhausts max_review_rounds."""
    call_counts = {"researcher": 0, "worker": 0, "reviewer": 0}

    async def fake_researcher(prompt, cwd, context, max_iterations):
        call_counts["researcher"] += 1
        return '<firepass_researcher status="completed" iterations="1" tool_calls="0"><result>findings</result><activity></activity></firepass_researcher>'

    async def fake_worker(prompt, cwd, context, max_iterations):
        call_counts["worker"] += 1
        return '<firepass_worker status="completed" iterations="1" tool_calls="0"><result>code</result><activity></activity></firepass_worker>'

    async def fake_reviewer(prompt, cwd, context, max_iterations):
        call_counts["reviewer"] += 1
        return '<firepass_reviewer status="completed" iterations="1" tool_calls="0"><result>NEEDS-FIXES</result><activity></activity></firepass_reviewer>'

    monkeypatch.setattr("firepass_mcp.server.firepass_researcher", fake_researcher)
    monkeypatch.setattr("firepass_mcp.server.firepass_worker", fake_worker)
    monkeypatch.setattr("firepass_mcp.server.firepass_reviewer", fake_reviewer)

    result = asyncio.run(firepass_trio("task", str(tmp_path), max_review_rounds=2))

    assert 'status="needs_fixes"' in result
    assert 'rounds="2"' in result
    assert result.count("<round ") == 2
    assert call_counts["researcher"] == 1
    assert call_counts["worker"] == 2
    assert call_counts["reviewer"] == 2


def test_firepass_trio_clamps_max_review_rounds(monkeypatch, tmp_path):
    """max_review_rounds above the ceiling is clamped to MAX_REVIEW_ROUNDS_LIMIT."""
    recorded = {}

    async def fake_researcher(prompt, cwd, context, max_iterations):
        return '<firepass_researcher status="completed" iterations="1" tool_calls="0"><result>findings</result><activity></activity></firepass_researcher>'

    async def fake_worker(prompt, cwd, context, max_iterations):
        return '<firepass_worker status="completed" iterations="1" tool_calls="0"><result>code</result><activity></activity></firepass_worker>'

    async def fake_reviewer(prompt, cwd, context, max_iterations):
        return '<firepass_reviewer status="completed" iterations="1" tool_calls="0"><result>APPROVE</result><activity></activity></firepass_reviewer>'

    async def fake_run_trio_chain(
        prompt, cwd, context, max_iterations, max_review_rounds
    ):
        recorded["max_review_rounds"] = max_review_rounds
        return '<firepass_trio status="approved" rounds="1"><research></research><rounds></rounds></firepass_trio>'

    monkeypatch.setattr("firepass_mcp.server.firepass_researcher", fake_researcher)
    monkeypatch.setattr("firepass_mcp.server.firepass_worker", fake_worker)
    monkeypatch.setattr("firepass_mcp.server.firepass_reviewer", fake_reviewer)
    monkeypatch.setattr("firepass_mcp.server._run_trio_chain", fake_run_trio_chain)

    asyncio.run(firepass_trio("task", str(tmp_path), max_review_rounds=99))

    assert recorded["max_review_rounds"] == MAX_REVIEW_ROUNDS_LIMIT


def test_firepass_trio_rejects_zero_review_rounds(monkeypatch, tmp_path):
    """max_review_rounds=0 must raise ValueError, matching iteration clamp behavior."""
    with pytest.raises(ValueError, match="must be > 0"):
        asyncio.run(firepass_trio("task", str(tmp_path), max_review_rounds=0))


def test_firepass_trio_research_failure_short_circuits(monkeypatch, tmp_path):
    """If researcher fails, trio status is research_failed and worker is never called."""
    call_counts = {"researcher": 0, "worker": 0}

    async def fake_researcher(prompt, cwd, context, max_iterations):
        call_counts["researcher"] += 1
        return '<firepass_researcher status="api_error" iterations="0" tool_calls="0"><result>fail</result><activity></activity></firepass_researcher>'

    async def fake_worker(prompt, cwd, context, max_iterations):
        call_counts["worker"] += 1
        return '<firepass_worker status="completed" iterations="1" tool_calls="0"><result>code</result><activity></activity></firepass_worker>'

    monkeypatch.setattr("firepass_mcp.server.firepass_researcher", fake_researcher)
    monkeypatch.setattr("firepass_mcp.server.firepass_worker", fake_worker)

    result = asyncio.run(firepass_trio("task", str(tmp_path)))

    assert 'status="research_failed"' in result
    assert 'rounds="0"' in result
    assert call_counts["researcher"] == 1
    assert call_counts["worker"] == 0
