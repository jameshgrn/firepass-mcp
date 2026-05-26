"""Tool schemas and local tool execution for FirePass."""

import os
import shlex
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Generic, TypeAlias, TypeVar, cast

BASH_TIMEOUT = int(os.environ.get("FIREPASS_BASH_TIMEOUT", "60"))
OUTPUT_CAP = int(os.environ.get("FIREPASS_MAX_OUTPUT", "50000"))
READ_CAP = int(os.environ.get("FIREPASS_MAX_READ", "100000"))
WRITE_CAP = 1_000_000  # 1MB max write size
MAX_ITERATIONS_LIMIT = 200  # Hard ceiling for user-supplied max_iterations
MAX_REVIEW_ROUNDS_LIMIT = 5  # Hard ceiling for user-supplied max_review_rounds
GLOB_RESULT_CAP = 500

RIPGREP_BLOCKED_FLAGS = {"--pre", "--pre-glob", "-z", "--search-zip", "--replace", "-r"}

JsonObject: TypeAlias = dict[str, Any]
ToolDef: TypeAlias = dict[str, Any]


class ToolAccess(str, Enum):
    READ = "read"
    WRITE = "write"
    SHELL = "shell"
    CONTROL = "control"


class ToolArgumentError(ValueError):
    """Raised when raw model-supplied tool arguments violate a tool contract."""


@dataclass(frozen=True)
class ReadFileArgs:
    path: str
    offset: int
    limit: int | None


@dataclass(frozen=True)
class WriteFileArgs:
    path: str
    content: str


@dataclass(frozen=True)
class EditFileArgs:
    path: str
    old_text: str
    new_text: str


@dataclass(frozen=True)
class BashArgs:
    command: str
    cwd: str | None


@dataclass(frozen=True)
class RipgrepArgs:
    pattern: str
    path: str | None
    flags: str


@dataclass(frozen=True)
class GlobFindArgs:
    pattern: str
    path: str | None


@dataclass(frozen=True)
class AstGrepArgs:
    pattern: str
    path: str | None
    lang: str | None


@dataclass(frozen=True)
class JqFileArgs:
    expression: str
    file: str


@dataclass(frozen=True)
class JqInputArgs:
    expression: str
    input_json: str


JqArgs: TypeAlias = JqFileArgs | JqInputArgs


@dataclass(frozen=True)
class ListDirArgs:
    path: str | None


@dataclass(frozen=True)
class TreeArgs:
    path: str | None
    max_depth: int


@dataclass(frozen=True)
class DoneArgs:
    result: str


@dataclass(frozen=True)
class BoundedGlob:
    matches: list[Path]
    truncated_after: int | None


ArgsT = TypeVar("ArgsT")


@dataclass(frozen=True)
class ToolSpec(Generic[ArgsT]):
    name: str
    access: ToolAccess
    description: str
    parameters: JsonObject
    parse_args: Callable[[Mapping[str, Any]], ArgsT]
    run: Callable[[ArgsT, str], str]
    format_activity: Callable[[ArgsT, str], str]

    def definition(self) -> ToolDef:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _schema(
    properties: JsonObject,
    required: tuple[str, ...] = (),
) -> JsonObject:
    schema: JsonObject = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


def _reject_unknown_args(args: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(args) - allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise ToolArgumentError(f"unexpected field(s): {joined}")


def _require_str(args: Mapping[str, Any], key: str) -> str:
    if key not in args:
        raise ToolArgumentError(f"missing required field '{key}'")
    value = args[key]
    if not isinstance(value, str):
        raise ToolArgumentError(f"field '{key}' must be a string")
    return value


def _optional_str(args: Mapping[str, Any], key: str) -> str | None:
    if key not in args or args[key] is None:
        return None
    value = args[key]
    if not isinstance(value, str):
        raise ToolArgumentError(f"field '{key}' must be a string when provided")
    return value


def _optional_int(args: Mapping[str, Any], key: str, default: int) -> int:
    if key not in args or args[key] is None:
        return default
    value = args[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolArgumentError(f"field '{key}' must be an integer when provided")
    return value


def _parse_read_file_args(args: Mapping[str, Any]) -> ReadFileArgs:
    _reject_unknown_args(args, {"path", "offset", "limit"})
    offset = _optional_int(args, "offset", 1)
    if offset < 1:
        raise ToolArgumentError("field 'offset' must be >= 1")

    limit = None
    if "limit" in args and args["limit"] is not None:
        limit = _optional_int(args, "limit", 0)
        if limit < 0:
            raise ToolArgumentError("field 'limit' must be >= 0")

    return ReadFileArgs(path=_require_str(args, "path"), offset=offset, limit=limit)


def _parse_write_file_args(args: Mapping[str, Any]) -> WriteFileArgs:
    _reject_unknown_args(args, {"path", "content"})
    return WriteFileArgs(
        path=_require_str(args, "path"),
        content=_require_str(args, "content"),
    )


def _parse_edit_file_args(args: Mapping[str, Any]) -> EditFileArgs:
    _reject_unknown_args(args, {"path", "old_text", "new_text"})
    return EditFileArgs(
        path=_require_str(args, "path"),
        old_text=_require_str(args, "old_text"),
        new_text=_require_str(args, "new_text"),
    )


def _parse_bash_args(args: Mapping[str, Any]) -> BashArgs:
    _reject_unknown_args(args, {"command", "cwd"})
    return BashArgs(
        command=_require_str(args, "command"),
        cwd=_optional_str(args, "cwd"),
    )


def _parse_ripgrep_args(args: Mapping[str, Any]) -> RipgrepArgs:
    _reject_unknown_args(args, {"pattern", "path", "flags"})
    return RipgrepArgs(
        pattern=_require_str(args, "pattern"),
        path=_optional_str(args, "path"),
        flags=_optional_str(args, "flags") or "",
    )


def _parse_glob_find_args(args: Mapping[str, Any]) -> GlobFindArgs:
    _reject_unknown_args(args, {"pattern", "path"})
    return GlobFindArgs(
        pattern=_require_str(args, "pattern"),
        path=_optional_str(args, "path"),
    )


def _parse_ast_grep_args(args: Mapping[str, Any]) -> AstGrepArgs:
    _reject_unknown_args(args, {"pattern", "path", "lang"})
    return AstGrepArgs(
        pattern=_require_str(args, "pattern"),
        path=_optional_str(args, "path"),
        lang=_optional_str(args, "lang"),
    )


def _parse_jq_args(args: Mapping[str, Any]) -> JqArgs:
    _reject_unknown_args(args, {"expression", "file", "input_json"})
    expression = _require_str(args, "expression")
    file = _optional_str(args, "file")
    input_json = _optional_str(args, "input_json")

    if (file is None) == (input_json is None):
        raise ToolArgumentError("provide exactly one of 'file' or 'input_json'")
    if file is not None:
        return JqFileArgs(expression=expression, file=file)
    if input_json is not None:
        return JqInputArgs(expression=expression, input_json=input_json)
    raise AssertionError("unreachable jq argument state")


def _parse_list_dir_args(args: Mapping[str, Any]) -> ListDirArgs:
    _reject_unknown_args(args, {"path"})
    return ListDirArgs(path=_optional_str(args, "path"))


def _parse_tree_args(args: Mapping[str, Any]) -> TreeArgs:
    _reject_unknown_args(args, {"path", "max_depth"})
    max_depth = _optional_int(args, "max_depth", 3)
    if max_depth < 1:
        raise ToolArgumentError("field 'max_depth' must be >= 1")
    return TreeArgs(path=_optional_str(args, "path"), max_depth=max_depth)


def _parse_done_args(args: Mapping[str, Any]) -> DoneArgs:
    _reject_unknown_args(args, {"result"})
    return DoneArgs(result=_require_str(args, "result"))


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


def normalize_cwd(cwd: str) -> str:
    """Require an existing directory and normalize it to an absolute path."""
    if not cwd:
        raise ValueError("cwd is required")

    resolved = Path(cwd).expanduser().resolve()
    if not resolved.exists():
        raise ValueError(f"Working directory does not exist: {cwd}")
    if not resolved.is_dir():
        raise ValueError(f"Working directory is not a directory: {cwd}")
    return str(resolved)


def clamp_max_iterations(value: int) -> int:
    """Validate and clamp max_iterations to a safe range."""
    if value <= 0:
        raise ValueError(f"max_iterations must be > 0, got {value}")
    if value > MAX_ITERATIONS_LIMIT:
        return MAX_ITERATIONS_LIMIT
    return value


def clamp_max_review_rounds(value: int) -> int:
    """Validate and clamp max_review_rounds to a safe range."""
    if value <= 0:
        raise ValueError(f"max_review_rounds must be > 0, got {value}")
    if value > MAX_REVIEW_ROUNDS_LIMIT:
        return MAX_REVIEW_ROUNDS_LIMIT
    return value


def _run(cmd: str | list[str], cwd: str, input_text: str | None = None) -> str:
    if isinstance(cmd, str):
        cmd = ["bash", "-c", cmd]
    try:
        r = subprocess.run(
            cmd,
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=BASH_TIMEOUT,
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


def _read_numbered_file(path: Path, offset: int, limit: int | None) -> str:
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")
    if limit == 0:
        return ""

    selected = 0
    total_chars = 0
    chunks: list[str] = []

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_number, line in enumerate(f, start=1):
            if line_number < offset:
                continue
            if limit is not None and selected >= limit:
                break

            numbered = f"{line_number:>6}|{line}"
            remaining = READ_CAP - total_chars
            if remaining <= 0:
                break
            if len(numbered) > remaining:
                chunks.append(numbered[:remaining])
                break

            chunks.append(numbered)
            total_chars += len(numbered)
            selected += 1

    return "".join(chunks)


def _bounded_glob(base: Path, pattern: str, cwd: str) -> BoundedGlob:
    cwd_resolved = Path(cwd).resolve()
    matches: list[Path] = []

    for match in base.glob(pattern):
        if match.resolve().is_relative_to(cwd_resolved):
            if len(matches) >= GLOB_RESULT_CAP:
                return BoundedGlob(
                    matches=sorted(matches), truncated_after=GLOB_RESULT_CAP
                )
            matches.append(match)

    return BoundedGlob(matches=sorted(matches), truncated_after=None)


def _run_read_file(args: ReadFileArgs, cwd: str) -> str:
    path = _validate_path(args.path, cwd)
    if not path.is_file():
        return f"[ERROR] Not a regular file: {args.path}"
    return _read_numbered_file(path, offset=args.offset, limit=args.limit)


def _run_write_file(args: WriteFileArgs, cwd: str) -> str:
    if len(args.content) > WRITE_CAP:
        return (
            f"[ERROR] Content size ({len(args.content)} bytes) exceeds maximum "
            f"allowed ({WRITE_CAP} bytes)"
        )
    path = _validate_path(args.path, cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args.content)
    return f"Wrote {len(args.content)} bytes to {args.path}"


def _run_edit_file(args: EditFileArgs, cwd: str) -> str:
    path = _validate_path(args.path, cwd)
    if not path.is_file():
        return f"[ERROR] Not a regular file: {args.path}"
    if path.stat().st_size > WRITE_CAP:
        return f"[ERROR] File too large to edit ({path.stat().st_size} bytes, max {WRITE_CAP})"

    content = path.read_text()
    count = content.count(args.old_text)
    if count == 0:
        return f"[ERROR] old_text not found in {args.path}"
    if count > 1:
        return (
            f"[ERROR] old_text matches {count} locations — "
            "must match exactly once. Add more surrounding context."
        )

    path.write_text(content.replace(args.old_text, args.new_text, 1))
    return f"Edited {args.path}"


def _run_bash(args: BashArgs, cwd: str) -> str:
    cmd_cwd = str(_validate_path(args.cwd, cwd)) if args.cwd is not None else cwd
    return _run(args.command, cmd_cwd)


def _run_ripgrep(args: RipgrepArgs, cwd: str) -> str:
    path = str(_validate_path(args.path or cwd, cwd))
    cmd = ["rg", "--no-heading", "-n"]
    if args.flags:
        flag_tokens = shlex.split(args.flags)
        for token in flag_tokens:
            if token in RIPGREP_BLOCKED_FLAGS or any(
                token.startswith(f"{blocked}=") for blocked in RIPGREP_BLOCKED_FLAGS
            ):
                return f"[ERROR] Blocked dangerous flag: {token}"
            if token.startswith("-") and not token.startswith("--") and "z" in token:
                return f"[ERROR] Blocked dangerous flag: {token} (contains -z)"
        cmd.extend(flag_tokens)
    cmd.append(args.pattern)
    cmd.append(path)
    return _run(cmd, cwd)


def _run_glob_find(args: GlobFindArgs, cwd: str) -> str:
    base = _validate_path(args.path or cwd, cwd)
    result = _bounded_glob(base, args.pattern, cwd)
    if not result.matches:
        return "(no matches)"

    lines = [str(match) for match in result.matches]
    if result.truncated_after is not None:
        lines.append(f"[truncated after {result.truncated_after} matches]")
    return "\n".join(lines)


def _run_ast_grep(args: AstGrepArgs, cwd: str) -> str:
    path = str(_validate_path(args.path or cwd, cwd))
    cmd = ["sg", "--pattern", args.pattern]
    if args.lang:
        cmd.extend(["--lang", args.lang])
    cmd.append(path)
    return _run(cmd, cwd)


def _run_jq(args: JqArgs, cwd: str) -> str:
    if isinstance(args, JqFileArgs):
        return _run(["jq", args.expression, str(_validate_path(args.file, cwd))], cwd)
    return _run(["jq", args.expression], cwd, input_text=args.input_json)


def _run_list_dir(args: ListDirArgs, cwd: str) -> str:
    path = _validate_path(args.path or cwd, cwd)
    return _run(["ls", "-lah", str(path)], cwd)


def _run_tree(args: TreeArgs, cwd: str) -> str:
    path = _validate_path(args.path or cwd, cwd)
    return _run(
        [
            "tree",
            "-L",
            str(args.max_depth),
            "-I",
            "__pycache__|.git|node_modules|.venv|.mypy_cache",
            str(path),
        ],
        cwd,
    )


def _run_done(args: DoneArgs, cwd: str) -> str:
    return args.result


def _read_file_activity(args: ReadFileArgs, cwd: str) -> str:
    return f"[read]  {args.path}"


def _write_file_activity(args: WriteFileArgs, cwd: str) -> str:
    return f"[write] {args.path} ({len(args.content)} bytes)"


def _edit_file_activity(args: EditFileArgs, cwd: str) -> str:
    return f"[edit]  {args.path}"


def _bash_activity(args: BashArgs, cwd: str) -> str:
    command = args.command
    if len(command) > 80:
        command = command[:77] + "..."
    return f"[bash]  {command}"


def _ripgrep_activity(args: RipgrepArgs, cwd: str) -> str:
    return f"[rg]    {args.pattern!r} in {args.path or cwd}"


def _glob_find_activity(args: GlobFindArgs, cwd: str) -> str:
    return f"[glob]  {args.pattern} in {args.path or cwd}"


def _ast_grep_activity(args: AstGrepArgs, cwd: str) -> str:
    return f"[sg]    {args.pattern}"


def _jq_activity(args: JqArgs, cwd: str) -> str:
    return f"[jq]    {args.expression}"


def _list_dir_activity(args: ListDirArgs, cwd: str) -> str:
    return f"[ls]    {args.path or cwd}"


def _tree_activity(args: TreeArgs, cwd: str) -> str:
    return f"[tree]  {args.path or cwd} (depth={args.max_depth})"


def _done_activity(args: DoneArgs, cwd: str) -> str:
    return "[done]"


TOOL_SPECS: tuple[ToolSpec[Any], ...] = (
    ToolSpec(
        name="read_file",
        access=ToolAccess.READ,
        description="Read file contents. Returns numbered lines.",
        parameters=_schema(
            {
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
            ("path",),
        ),
        parse_args=_parse_read_file_args,
        run=_run_read_file,
        format_activity=_read_file_activity,
    ),
    ToolSpec(
        name="write_file",
        access=ToolAccess.WRITE,
        description="Create or overwrite a file.",
        parameters=_schema(
            {
                "path": {"type": "string", "description": "Absolute file path"},
                "content": {"type": "string", "description": "File content"},
            },
            ("path", "content"),
        ),
        parse_args=_parse_write_file_args,
        run=_run_write_file,
        format_activity=_write_file_activity,
    ),
    ToolSpec(
        name="edit_file",
        access=ToolAccess.WRITE,
        description="Replace exact text in a file. old_text must match exactly once.",
        parameters=_schema(
            {
                "path": {"type": "string", "description": "Absolute file path"},
                "old_text": {
                    "type": "string",
                    "description": "Exact text to find (must match once)",
                },
                "new_text": {"type": "string", "description": "Replacement text"},
            },
            ("path", "old_text", "new_text"),
        ),
        parse_args=_parse_edit_file_args,
        run=_run_edit_file,
        format_activity=_edit_file_activity,
    ),
    ToolSpec(
        name="bash",
        access=ToolAccess.SHELL,
        description=(
            "Run a shell command (timeout configurable via FIREPASS_BASH_TIMEOUT). "
            "Use for: git, python, uv, ruff, pytest, etc."
        ),
        parameters=_schema(
            {
                "command": {"type": "string", "description": "Shell command"},
                "cwd": {
                    "type": "string",
                    "description": "Working directory (default: agent cwd)",
                },
            },
            ("command",),
        ),
        parse_args=_parse_bash_args,
        run=_run_bash,
        format_activity=_bash_activity,
    ),
    ToolSpec(
        name="ripgrep",
        access=ToolAccess.READ,
        description="Fast regex search via rg. Returns file:line: match.",
        parameters=_schema(
            {
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
            ("pattern",),
        ),
        parse_args=_parse_ripgrep_args,
        run=_run_ripgrep,
        format_activity=_ripgrep_activity,
    ),
    ToolSpec(
        name="glob_find",
        access=ToolAccess.READ,
        description="Find files matching a glob pattern.",
        parameters=_schema(
            {
                "pattern": {"type": "string", "description": "Glob, e.g. '**/*.py'"},
                "path": {
                    "type": "string",
                    "description": "Base directory (default: cwd)",
                },
            },
            ("pattern",),
        ),
        parse_args=_parse_glob_find_args,
        run=_run_glob_find,
        format_activity=_glob_find_activity,
    ),
    ToolSpec(
        name="ast_grep",
        access=ToolAccess.READ,
        description=(
            "Structural code search via ast-grep (sg). Matches code patterns, "
            "not text. Example: 'def $FUNC($$$ARGS)' or 'console.log($$$)'"
        ),
        parameters=_schema(
            {
                "pattern": {"type": "string", "description": "ast-grep pattern"},
                "path": {
                    "type": "string",
                    "description": "File or dir (default: cwd)",
                },
                "lang": {
                    "type": "string",
                    "description": "Language: python, javascript, typescript, rust, go",
                },
            },
            ("pattern",),
        ),
        parse_args=_parse_ast_grep_args,
        run=_run_ast_grep,
        format_activity=_ast_grep_activity,
    ),
    ToolSpec(
        name="jq",
        access=ToolAccess.READ,
        description="Query/transform JSON with jq.",
        parameters=_schema(
            {
                "expression": {"type": "string", "description": "jq filter"},
                "file": {"type": "string", "description": "JSON file path"},
                "input_json": {
                    "type": "string",
                    "description": "JSON string (used if file omitted)",
                },
            },
            ("expression",),
        ),
        parse_args=_parse_jq_args,
        run=_run_jq,
        format_activity=_jq_activity,
    ),
    ToolSpec(
        name="list_dir",
        access=ToolAccess.READ,
        description="List directory contents with sizes.",
        parameters=_schema(
            {
                "path": {
                    "type": "string",
                    "description": "Directory (default: cwd)",
                },
            }
        ),
        parse_args=_parse_list_dir_args,
        run=_run_list_dir,
        format_activity=_list_dir_activity,
    ),
    ToolSpec(
        name="tree",
        access=ToolAccess.READ,
        description="Directory tree. Excludes __pycache__, .git, node_modules, .venv.",
        parameters=_schema(
            {
                "path": {
                    "type": "string",
                    "description": "Root dir (default: cwd)",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Max depth (default: 3)",
                },
            }
        ),
        parse_args=_parse_tree_args,
        run=_run_tree,
        format_activity=_tree_activity,
    ),
    ToolSpec(
        name="done",
        access=ToolAccess.CONTROL,
        description=(
            "Signal task completion. MUST call when finished. The result is "
            "returned to the caller - keep it to a concise executive summary "
            "(one page max). List files changed, key findings, or decisions made. "
            "No verbose logs or full code dumps."
        ),
        parameters=_schema(
            {
                "result": {
                    "type": "string",
                    "description": (
                        "One-page executive summary: what you did/found, files "
                        "changed, key decisions. Be concise."
                    ),
                },
            },
            ("result",),
        ),
        parse_args=_parse_done_args,
        run=_run_done,
        format_activity=_done_activity,
    ),
)

TOOL_REGISTRY: dict[str, ToolSpec[Any]] = {spec.name: spec for spec in TOOL_SPECS}
READONLY_ALLOWED_ACCESS = frozenset({ToolAccess.READ, ToolAccess.CONTROL})
READONLY_BLOCKED_TOOLS = frozenset(
    spec.name for spec in TOOL_SPECS if spec.access not in READONLY_ALLOWED_ACCESS
)
TOOL_DEFS = [spec.definition() for spec in TOOL_SPECS]
READONLY_TOOL_DEFS = [
    spec.definition() for spec in TOOL_SPECS if spec.access in READONLY_ALLOWED_ACCESS
]


def tool_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in TOOL_SPECS)


def readonly_tool_names() -> tuple[str, ...]:
    return tuple(
        spec.name for spec in TOOL_SPECS if spec.access in READONLY_ALLOWED_ACCESS
    )


def _coerce_arg_mapping(args: object) -> Mapping[str, Any]:
    if not isinstance(args, Mapping):
        raise ToolArgumentError("expected object")
    if any(not isinstance(key, str) for key in args):
        raise ToolArgumentError("argument object keys must be strings")
    return cast(Mapping[str, Any], args)


def format_tool_activity(name: str, args: object, cwd: str) -> str:
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return f"[{name}]"
    try:
        raw_args = _coerce_arg_mapping(args)
        parsed_args = spec.parse_args(raw_args)
    except ToolArgumentError:
        return f"[{name}] invalid arguments"
    return spec.format_activity(parsed_args, cwd)


def exec_tool(name: str, args: object, cwd: str) -> str:
    """Execute a validated tool call, returning a result string."""
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return f"[ERROR] unknown tool: {name}"

    try:
        raw_args = _coerce_arg_mapping(args)
        parsed_args = spec.parse_args(raw_args)
    except ToolArgumentError as e:
        return f"[ERROR] Invalid {name} arguments: {e}"

    try:
        return spec.run(parsed_args, cwd)
    except Exception as e:
        return f"[ERROR] {e}"
