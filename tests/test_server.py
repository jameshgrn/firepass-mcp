"""Focused tests for concrete review findings."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from firepass_mcp.messages import (
    _compact_tool_calls,
    _enforce_context_budget,
    _message_size,
    parse_tool_calls,
)
from firepass_mcp.server import (
    _stream_response,
    agent_loop,
    firepass_worker,
)
from firepass_mcp.tools import (
    MAX_ITERATIONS_LIMIT,
    _clamp_max_iterations,
    _normalize_cwd,
    _validate_path,
    exec_tool,
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


def test_read_file_with_limit_stops_at_read_cap(monkeypatch, tmp_path):
    """A huge limit must not force the server to materialize the whole file."""
    target = tmp_path / "large.txt"
    target.touch()

    class CountingFile:
        def __init__(self):
            self.lines_read = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            if self.lines_read >= 10_000:
                raise StopIteration
            self.lines_read += 1
            return ("x" * 200) + "\n"

    counting_file = CountingFile()

    def fake_open(path, *args, **kwargs):
        assert Path(path) == target.resolve()
        return counting_file

    monkeypatch.setattr("builtins.open", fake_open)

    result = exec_tool(
        "read_file",
        {"path": str(target), "limit": 1_000_000},
        str(tmp_path),
    )

    assert len(result) <= 100_000
    assert counting_file.lines_read < 10_000


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

    assert result.startswith("[ERROR]")
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


def test_enforce_context_budget_truncates_tool_messages():
    # Make total well over CONTEXT_CAP so both tool messages must be truncated
    messages = [
        {"role": "system", "content": "x" * 100},
        {"role": "user", "content": "y" * 100},
        {"role": "tool", "tool_call_id": "tc1", "content": "a" * 200_000},
        {"role": "tool", "tool_call_id": "tc2", "content": "b" * 200_000},
    ]
    compacted = _enforce_context_budget(messages)
    assert compacted[2]["content"] == "[truncated]"
    assert compacted[3]["content"] == "[truncated]"
    assert messages[2]["content"] == "a" * 200_000


def test_enforce_context_budget_compacts_assistant_tool_calls():
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
    compacted = _enforce_context_budget(messages)
    assert compacted[1]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert messages[1]["tool_calls"][0]["function"]["arguments"] == big_args


def test_enforce_context_budget_preserves_message_validity():
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
    compacted = _enforce_context_budget(messages)
    assert compacted[0]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert compacted[0]["tool_calls"][0]["id"] == "tc1"
    assert compacted[0]["tool_calls"][0]["type"] == "function"
    assert compacted[1]["tool_call_id"] == "tc1"


def test_enforce_context_budget_rejects_noncompactable_overage():
    messages = [
        {"role": "system", "content": "x" * 150_000},
        {"role": "user", "content": "y" * 150_000},
    ]

    with pytest.raises(ValueError, match="Context budget still exceeds limit"):
        _enforce_context_budget(messages)


# ---------------------------------------------------------------------------
# 4. max_iterations clamping
# ---------------------------------------------------------------------------


def test_clamp_max_iterations_valid():
    assert _clamp_max_iterations(1) == 1
    assert _clamp_max_iterations(60) == 60
    assert _clamp_max_iterations(MAX_ITERATIONS_LIMIT) == MAX_ITERATIONS_LIMIT


def test_clamp_max_iterations_negative_raises():
    with pytest.raises(ValueError, match="must be > 0"):
        _clamp_max_iterations(-1)


def test_clamp_max_iterations_zero_raises():
    with pytest.raises(ValueError, match="must be > 0"):
        _clamp_max_iterations(0)


def test_clamp_max_iterations_huge_clamped():
    assert _clamp_max_iterations(999_999) == MAX_ITERATIONS_LIMIT


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
    assert _normalize_cwd(str(tmp_path)) == str(tmp_path.resolve())


def test_normalize_cwd_rejects_missing():
    with pytest.raises(ValueError, match="does not exist"):
        _normalize_cwd("/nonexistent/path/12345")
