"""Chat message compaction and tool-call parsing helpers."""

import json
from dataclasses import dataclass
from typing import Any, cast

CONTEXT_CAP = 200_000  # Max characters for message context


@dataclass(frozen=True)
class ParsedToolCall:
    tool_call_id: str
    name: str
    arguments: dict[str, Any]


def _message_size(msg: dict) -> int:
    """Count characters in message content and assistant tool_call arguments."""
    size = len(msg.get("content", ""))
    if msg.get("role") == "assistant":
        for tc in msg.get("tool_calls", []):
            size += len(tc.get("function", {}).get("arguments", ""))
    return size


def _copy_message(msg: dict) -> dict:
    """Copy a chat message, including nested tool-call dictionaries."""
    copied = dict(msg)
    if "tool_calls" in copied:
        copied["tool_calls"] = [
            {**tc, "function": dict(tc.get("function", {}))}
            for tc in copied["tool_calls"]
        ]
    return copied


def _compact_tool_calls(msg: dict) -> tuple[dict, int]:
    """Return a copy with bulky assistant tool_call arguments replaced.

    Returns the number of characters freed.
    """
    compacted = _copy_message(msg)
    freed = 0
    for tc in compacted.get("tool_calls", []):
        fn = tc.get("function", {})
        old_args = fn.get("arguments", "")
        if old_args and old_args != "{}":
            freed += len(old_args) - len("{}")
            fn["arguments"] = "{}"
    return compacted, freed


def _enforce_context_budget(messages: list[dict]) -> list[dict]:
    """Return messages compacted to fit the context budget.

    Tool outputs and assistant tool-call arguments are replay evidence, so they
    can be compacted. System/user/assistant text is not blindly truncated because
    that would alter the actual task or model response.
    """
    compacted_messages = [_copy_message(msg) for msg in messages]
    total = sum(_message_size(msg) for msg in compacted_messages)
    if total <= CONTEXT_CAP:
        return compacted_messages

    # Phase 1: truncate oldest tool messages first
    for msg in compacted_messages:
        if msg.get("role") == "tool" and msg.get("content") != "[truncated]":
            freed = len(msg["content"]) - len("[truncated]")
            msg["content"] = "[truncated]"
            total -= freed
            if total <= CONTEXT_CAP:
                return compacted_messages

    # Phase 2: compact old assistant tool_calls (arguments only)
    for index, msg in enumerate(compacted_messages):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            compacted, freed = _compact_tool_calls(msg)
            compacted_messages[index] = compacted
            total -= freed
            if total <= CONTEXT_CAP:
                return compacted_messages

    raise ValueError(
        "Context budget still exceeds limit after compacting tool results "
        f"({total} chars > {CONTEXT_CAP}). Reduce prompt or context size."
    )


def parse_tool_calls(raw_tool_calls: object) -> tuple[list[ParsedToolCall], list[str]]:
    """Parse assistant tool calls into executable calls or validation errors."""
    if not isinstance(raw_tool_calls, list):
        return [], [
            f"[ERROR] Expected tool_calls list, got {type(raw_tool_calls).__name__}"
        ]

    parsed: list[ParsedToolCall] = []
    errors: list[str] = []

    for index, raw_call in enumerate(raw_tool_calls):
        if not isinstance(raw_call, dict):
            errors.append(
                f"[ERROR] Malformed tool call {index}: expected object, "
                f"got {type(raw_call).__name__}"
            )
            continue

        call = cast(dict[str, Any], raw_call)
        function = call.get("function")
        if not isinstance(function, dict):
            errors.append(
                f"[ERROR] Malformed tool call {index}: missing function object"
            )
            continue

        name = function.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"[ERROR] Malformed tool call {index}: missing function name")
            continue

        raw_args = function.get("arguments")
        if not isinstance(raw_args, str):
            errors.append(
                f"[ERROR] Malformed tool call {index}: function arguments must be a JSON string"
            )
            continue

        try:
            arguments = json.loads(raw_args)
        except json.JSONDecodeError as e:
            errors.append(f"[ERROR] Failed to parse {name} arguments: {e}")
            continue

        if not isinstance(arguments, dict):
            errors.append(
                f"[ERROR] Expected dict arguments for {name}, got {type(arguments).__name__}"
            )
            continue

        tool_call_id = call.get("id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            errors.append(f"[ERROR] Malformed tool call {index}: missing tool call id")
            continue

        parsed.append(
            ParsedToolCall(
                tool_call_id=tool_call_id,
                name=name,
                arguments=arguments,
            )
        )

    return parsed, errors
