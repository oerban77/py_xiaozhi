"""Coding tools for py_xiaozhi.

A model-neutral coding tool set modelled on
https://github.com/xyTom/coding-tools-mcp (Apache-2.0), adapted to the
py_xiaozhi MCP tool framework: handlers are ``async (args) -> str`` and every
property is a boolean, an integer, or a string, so the array/object inputs of
``apply_changes`` travel as JSON strings.

Implemented (the subset that is meaningful for an on-device assistant):

- ``read_file``        UTF-8 ranges with a sha256 ``revision``
- ``list_dir``         directory listing, optionally recursive
- ``list_files``       glob-filtered file enumeration
- ``search_text``      text/regex search with context lines
- ``apply_patch``      V4A ``*** Begin Patch`` envelope (see patching.py)
- ``apply_changes``    line-addressed create/write/edit/delete/move/copy
- ``exec_command``     a bounded shell command (guarded by a safety blacklist)
- ``git_status``       ``git status --short``
- ``git_diff``         staged/unstaged/untracked diff
- ``git_log``          commit history
- ``git_show``         a commit's contents
- ``git_blame``        per-line authorship

Deliberately not ported: ``view_image`` (the camera tool already covers it),
interactive terminal sessions (``write_stdin``) and MCP-protocol introspection
tools (``server_info``, ``check_exec_environment``, ``request_permissions``).
Long-running one-shot commands are supported through bounded background jobs.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.logging import get_logger

from .patching import (
    REVISION_ALGORITHM,
    ChangeRequest,
    ToolFailure,
    apply_line_edits,
    apply_update_hunks_detailed,
    atomic_delete,
    atomic_move,
    atomic_write,
    content_revision,
    parse_changes,
    parse_patch,
)

logger = get_logger()

# ── bounds ────────────────────────────────────────────────────

MAX_READ_BYTES = 256 * 1024
MAX_LIST_ENTRIES = 200
MAX_SEARCH_RESULTS = 100
MAX_PREVIEW_BYTES = 512
MAX_GIT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
DEFAULT_TIMEOUT_MS = 30_000
MAX_TIMEOUT_MS = 120_000
MAX_COMMAND_JOBS = 4
MAX_COMMAND_JOB_OUTPUT_BYTES = 1024 * 1024
COMMAND_JOB_RETENTION_SECONDS = 900
MAX_SEARCH_FILES = 100
MAX_SEARCH_FILE_BYTES = 1024 * 1024
MAX_SEARCH_BATCH_BYTES = 16 * 1024 * 1024
BINARY_PROBE_BYTES = 4096
MAX_DEPTH = 8
IGNORED_ANALYSIS_EXTENSIONS = {".bin", ".hex"}
IGNORED_ANALYSIS_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".idea",
    ".vscode",
    "cache",
    "tmp",
    "temp",
}
PRIORITY_DIRS = (
    "src",
    "tests",
    "scripts",
    "config",
    "templates",
    "examples",
    "mcp",
    "models",
    "assets",
    "app",
)


def _is_ignored_analysis_dir(name: str) -> bool:
    return name in IGNORED_ANALYSIS_DIRS


def _path_priority(path: Path, root: Path) -> int:
    """Prefer source and test areas over docs/build noise for large repo scans."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return 0
    parts = relative.parts
    if not parts:
        return 0
    first = parts[0].lower()
    if first in PRIORITY_DIRS:
        base = 1000 - PRIORITY_DIRS.index(first) * 10
    elif first in {"docs", "document", "documentation"}:
        base = 200
    elif first.startswith("."):
        base = -1000
    else:
        base = 50
    if any(part.lower() in IGNORED_ANALYSIS_DIRS for part in parts):
        return -10000
    if any(part.lower() in {"build", "dist", "node_modules", "cache", "tmp", "temp"} for part in parts):
        return -10000
    return base


def _path_sort_key(path: Path, root: Path) -> tuple[int, str]:
    """Order analysis results by project relevance before alphabetical order."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return (-_path_priority(path, root), str(relative).lower())


@dataclass
class _CommandJob:
    process: subprocess.Popen
    started_at: float
    windows_job_handle: Any = None
    cancelled: bool = False
    output: bytearray = field(default_factory=bytearray)
    output_base: int = 0
    total_output: int = 0
    readers_done: int = 0
    completed_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_COMMAND_JOBS: dict[str, _CommandJob] = {}
_COMMAND_JOBS_LOCK = threading.Lock()


def _create_windows_job_object():
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        return None

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    information = ExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    if not kernel32.SetInformationJobObject(
        handle, 9, ctypes.byref(information), ctypes.sizeof(information)
    ):
        kernel32.CloseHandle(handle)
        return None
    return handle


def _close_windows_job_object(handle) -> None:
    if os.name == "nt" and handle:
        import ctypes

        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)


def _assign_process_to_windows_job(process: subprocess.Popen, handle):
    if os.name != "nt" or not handle:
        return handle
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    if kernel32.AssignProcessToJobObject(handle, process._handle):
        return handle
    _close_windows_job_object(handle)
    return None

# Commands that must never be run, whatever the arguments. The hardware tool
# uses the same list; a coding assistant has no business running these either.
BLACKLIST_PATTERNS = [
    r"\bformat\b\s+[A-Za-z]:",
    r"\bdel\b\s+/s\s",
    r"\bdel\b\s+.*[/\\]s\b.*\*",
    r"\brmdir\b\s+/s",
    r"\brm\b\s+-rf?\s+/",
    r"\brm\b\s+-rf?\s+.*\*",
    r"\bmkfs(?:\.\w+)?\b",
    r"\bdd\b\s+if=.*of=/dev/",
    r"\breg\b\s+delete\b",
    r"\breg\b\s+add\b.*/f\b",
    r"netsh\s+.*firewall\s+.*disable",
    r"\bufw\b\s+disable",
    r"net\s+user\s+.*/add",
    r"\buseradd\b",
    r"\bpasswd\b",
    r"shutdown\b",
    r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;:",  # fork bomb
]
_BLACKLIST = [re.compile(pattern, re.IGNORECASE) for pattern in BLACKLIST_PATTERNS]


def is_command_safe(command: str) -> bool:
    return not any(pattern.search(command) for pattern in _BLACKLIST)


def _is_pdf_page_dump_command(command: str) -> bool:
    normalized = command.lower()
    has_pdf = ".pdf" in normalized
    has_pdf_reader = any(
        name in normalized for name in ("pypdf", "pypdf2", "pdfreader", "pdfplumber", "fitz")
    )
    has_page_access = re.search(r"\.pages\s*\[", normalized) is not None
    has_text_extraction = any(
        name in normalized for name in ("extract_text", "extracttext", "get_text")
    )
    return has_pdf and has_pdf_reader and has_page_access and has_text_extraction


# ── workspace resolution ──────────────────────────────────────


def _workspace_root() -> Path:
    """The root all relative paths resolve against."""
    try:
        from src.utils.workspace import get_workspace_root

        return get_workspace_root()
    except Exception:
        return Path.cwd().resolve()


def _is_within(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _is_ignored_analysis_file(path: Path) -> bool:
    return path.suffix.lower() in IGNORED_ANALYSIS_EXTENSIONS


def _resolve(path: str, root: Path, *, must_exist: bool = False) -> Path:
    """Resolve a workspace-relative path, rejecting escapes."""
    if not path or not isinstance(path, str):
        raise ToolFailure("INVALID_ARGUMENT", "path is required", category="validation")
    if "\x00" in path:
        raise ToolFailure("INVALID_ARGUMENT", "path contains NUL bytes", category="security")
    if os.path.isabs(path):
        resolved = Path(path).resolve()
    else:
        if Path(path).parts and Path(path).parts[0] == "..":
            raise ToolFailure(
                "PATH_OUTSIDE_WORKSPACE",
                f"Path escapes the workspace: {path}",
                category="security",
            )
        resolved = (root / path).resolve()
    if not _is_within(root, resolved):
        raise ToolFailure(
            "WORKSPACE_MISMATCH",
            f"Path is outside the active coding workspace: {path}. If the user explicitly requested this project, call set_workspace once with its directory, then use workspace-relative paths. Do not retry this path.",
            category="security",
        )
    # A symlink that points outside the tree is an escape too.
    if resolved.is_symlink() and not _is_within(root, resolved):
        raise ToolFailure("SYMLINK_ESCAPE", f"Symlink escapes the workspace: {path}", category="security")
    if must_exist and not resolved.exists():
        raise ToolFailure("NOT_FOUND", f"No such file or directory: {path}", category="not_found")
    return resolved


def _read_text(path: Path) -> str:
    """Read UTF-8 text, rejecting binary content."""
    data = path.read_bytes()
    if b"\x00" in data[:BINARY_PROBE_BYTES]:
        raise ToolFailure("BINARY_FILE", f"File is binary: {path}", category="validation")
    return data.decode("utf-8", errors="replace")


def _line_slice(text: str, start_line: int, end_line: int) -> tuple[str, int]:
    lines = text.split("\n")
    total = len(lines) - 1 if text.endswith("\n") else len(lines)
    start = max(1, start_line)
    stop = total if end_line <= 0 else min(total, end_line)
    if stop < start:
        return "", total
    selected = lines[start - 1 : stop]
    return "\n".join(selected), total


def _read_file_window(
    path: Path,
    start_line: int,
    end_line: int,
    max_lines: int,
    max_bytes: int,
) -> tuple[str, int, str, int]:
    digest = hashlib.sha256()
    selected_lines: list[str] = []
    selected_bytes = 0
    truncated = False
    total_lines = 0
    line_limit = start_line + max_lines - 1 if max_lines > 0 else 0

    with path.open("rb") as stream:
        probe = stream.read(BINARY_PROBE_BYTES)
        if b"\x00" in probe:
            raise ToolFailure(
                "BINARY_FILE", f"File is binary: {path}", category="validation"
            )
        stream.seek(0)
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.decode("utf-8", errors="replace")
            digest.update(line.encode("utf-8"))
            total_lines = line_number
            if line_number < start_line:
                continue
            if end_line > 0 and line_number > end_line:
                continue
            if line_limit and line_number > line_limit:
                continue
            if truncated:
                continue

            if line.endswith("\n"):
                line = line[:-1]
            line_bytes = line.encode("utf-8")
            separator_bytes = 1 if selected_lines else 0
            available = max_bytes - selected_bytes - separator_bytes
            if len(line_bytes) > available:
                if available > 0:
                    selected_lines.append(
                        line_bytes[:available].decode("utf-8", errors="ignore")
                    )
                    selected_bytes += available + separator_bytes
                truncated = True
                continue

            selected_lines.append(line)
            selected_bytes += len(line_bytes) + separator_bytes

    content = "\n".join(selected_lines)
    if truncated:
        content += f"\n... (truncated at {max_bytes} bytes)"
    return content, total_lines, digest.hexdigest(), len(selected_lines)


# ── read_file ─────────────────────────────────────────────────


def _read_file(args: dict) -> str:
    root = _workspace_root()
    path = _resolve(str(args.get("path", "")), root, must_exist=True)
    if path.is_dir():
        raise ToolFailure(
            "IS_DIRECTORY", f"Is a directory: {path}", category="validation"
        )
    if _is_ignored_analysis_file(path):
        raise ToolFailure(
            "IGNORED_FILE_TYPE",
            f"Skipped {path.suffix} firmware artifact. Analyze source/config files instead.",
            category="validation",
        )
    try:
        start_line = int(args.get("start_line", 1))
        end_line = int(args.get("end_line", 0))
        max_lines = int(args.get("max_lines", 0))
        max_bytes = int(args.get("max_bytes", MAX_READ_BYTES))
    except (TypeError, ValueError):
        raise ToolFailure(
            "INVALID_ARGUMENT", "read_file range limits must be integers."
        ) from None
    if start_line < 1 or end_line < 0 or max_lines < 0 or max_bytes < 1:
        raise ToolFailure(
            "INVALID_ARGUMENT",
            "start_line must be >= 1; end_line and max_lines must be >= 0; max_bytes must be >= 1.",
        )
    max_bytes = min(max_bytes, MAX_READ_BYTES)
    content, total_lines, revision, shown_lines = _read_file_window(
        path, start_line, end_line, max_lines, max_bytes
    )

    shown_end = start_line + shown_lines - 1
    banner = (
        f"[Showing lines {start_line}-{shown_end} of {total_lines} "
        f"revision={revision}]"
    )
    body = content if content else "(empty)"
    return f"{banner}\n{body}"


# ── list_dir / list_files ─────────────────────────────────────


def _entry_kind(entry: Path) -> str:
    if entry.is_dir():
        return "dir"
    if entry.is_symlink():
        return "link"
    return "file"


def _list_dir(args: dict) -> str:
    root = _workspace_root()
    rel = str(args.get("path", "") or ".")
    path = _resolve(rel, root, must_exist=True)
    if not path.is_dir():
        raise ToolFailure("NOT_A_DIRECTORY", f"Not a directory: {rel}", category="validation")

    recursive = bool(args.get("recursive"))
    include_hidden = bool(args.get("include_hidden"))
    max_entries = int(args.get("max_entries") or MAX_LIST_ENTRIES)
    max_depth = int(args.get("max_depth") or MAX_DEPTH)

    entries: list[str] = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(path):
            depth = len(Path(dirpath).relative_to(path).parts)
            if depth >= max_depth:
                dirnames[:] = []
                continue
            if not include_hidden:
                dirnames[:] = [
                    d for d in dirnames if not d.startswith(".") and not _is_ignored_analysis_dir(d)
                ]
            else:
                dirnames[:] = [d for d in dirnames if not _is_ignored_analysis_dir(d)]
            dirnames[:] = sorted(
                dirnames,
                key=lambda d: _path_sort_key(Path(dirpath, d), root),
            )
            filenames = [
                name
                for name in filenames
                if not _is_ignored_analysis_file(Path(dirpath, name))
            ]
            if not include_hidden:
                filenames = [f for f in filenames if not f.startswith(".")]
            for name in dirnames:
                relative = str(Path(dirpath, name).relative_to(root)).replace("\\", "/")
                entries.append(f"{relative}/")
            for name in sorted(
                filenames,
                key=lambda f: _path_sort_key(Path(dirpath, f), root),
            ):
                relative = str(Path(dirpath, name).relative_to(root)).replace("\\", "/")
                entries.append(relative)
            if len(entries) >= max_entries:
                break
    else:
        for entry in sorted(path.iterdir(), key=lambda e: _path_sort_key(e, root)):
            if _is_ignored_analysis_dir(entry.name):
                continue
            if not include_hidden and entry.name.startswith("."):
                continue
            if entry.is_file() and _is_ignored_analysis_file(entry):
                continue
            display = str(entry.relative_to(root))
            entries.append(f"{display}/" if entry.is_dir() else display)
            if len(entries) >= max_entries:
                break

    if not entries:
        return f"(empty directory: {rel})"
    truncated = len(entries) - max_entries if len(entries) > max_entries else 0
    body = "\n".join(entries[:max_entries])
    if truncated:
        body += f"\n... ({truncated} more entries omitted)"
    return body


def _list_files(args: dict) -> str:
    root = _workspace_root()
    rel = str(args.get("path", "") or ".")
    path = _resolve(rel, root, must_exist=True)
    patterns = str(args.get("glob") or args.get("patterns") or "*")
    exclude = str(args.get("exclude_patterns") or "")
    include_hidden = bool(args.get("include_hidden"))
    max_results = int(args.get("max_results") or MAX_LIST_ENTRIES)

    pattern_list = [p.strip() for p in patterns.replace(",", "\n").split("\n") if p.strip()]
    exclude_list = [p.strip() for p in exclude.replace(",", "\n").split("\n") if p.strip()]

    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(path):
        if not include_hidden:
            dirnames[:] = [
                d for d in dirnames if not d.startswith(".") and not _is_ignored_analysis_dir(d)
            ]
            filenames = [f for f in filenames if not f.startswith(".")]
        else:
            dirnames[:] = [d for d in dirnames if not _is_ignored_analysis_dir(d)]
        for name in sorted(filenames, key=lambda f: _path_sort_key(Path(dirpath, f), root)):
            full = Path(dirpath, name)
            if _is_ignored_analysis_file(full):
                continue
            relative = str(full.relative_to(root)).replace("\\", "/")
            if exclude_list and any(
                fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(relative, pat)
                for pat in exclude_list
            ):
                continue
            if any(fnmatch.fnmatch(name, pat) for pat in pattern_list):
                matches.append(relative)
            elif any(fnmatch.fnmatch(relative, pat) for pat in pattern_list):
                matches.append(relative)
        if len(matches) >= max_results:
            break

    if not matches:
        return f"(no files matching {patterns} under {rel})"
    matches = sorted(set(matches), key=lambda p: _path_sort_key(Path(root / p), root))[:max_results]
    return "\n".join(matches)


# ── search_text ───────────────────────────────────────────────


def _search_text(args: dict) -> str:
    root = _workspace_root()
    query = str(args.get("query", ""))
    if not query:
        raise ToolFailure("INVALID_ARGUMENT", "query is required", category="validation")
    rel = str(args.get("path", "") or ".")
    path = _resolve(rel, root, must_exist=True)
    use_regex = bool(args.get("regex"))
    case_sensitive = bool(args.get("case_sensitive"))
    glob_patterns = str(args.get("glob") or args.get("include_globs") or "")
    try:
        context_lines = int(args.get("context_lines", 0))
        max_results = int(args.get("max_results", MAX_SEARCH_RESULTS))
        max_preview = int(args.get("max_preview_bytes", MAX_PREVIEW_BYTES))
        max_files = int(args.get("max_files", MAX_SEARCH_FILES))
        file_offset = int(args.get("file_offset", 0))
    except (TypeError, ValueError):
        raise ToolFailure(
            "INVALID_ARGUMENT", "search limits and file_offset must be integers."
        ) from None
    if context_lines < 0 or max_results < 1 or max_preview < 1 or max_files < 1 or file_offset < 0:
        raise ToolFailure(
            "INVALID_ARGUMENT",
            "context_lines/file_offset must be >= 0; max_results, max_preview_bytes and max_files must be >= 1.",
        )
    max_results = min(max_results, MAX_SEARCH_RESULTS)
    max_preview = min(max_preview, MAX_PREVIEW_BYTES)
    max_files = min(max_files, MAX_SEARCH_FILES)

    flags = 0 if case_sensitive else re.IGNORECASE
    needle = re.compile(query, flags) if use_regex else re.compile(re.escape(query), flags)

    globs = [p.strip() for p in glob_patterns.replace(",", "\n").split("\n") if p.strip()]
    files: list[Path] = []
    candidate_index = 0
    more_files = False
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not d.startswith(".")
            and d not in {".git", "__pycache__"}
            and not _is_ignored_analysis_dir(d)
        )
        for name in sorted(filenames):
            if globs and not any(fnmatch.fnmatch(name, pat) for pat in globs):
                continue
            full = Path(dirpath, name)
            if _is_ignored_analysis_file(full):
                continue
            if full.is_symlink() and not _is_within(root, full.resolve()):
                continue
            if candidate_index < file_offset:
                candidate_index += 1
                continue
            if len(files) >= max_files:
                more_files = True
                break
            files.append(full)
            candidate_index += 1
        if more_files:
            break

    files.sort(key=lambda p: _path_priority(p, root), reverse=True)

    results: list[str] = []
    scanned = 0
    scanned_bytes = 0
    skipped_large = 0
    stopped_for_budget = False
    results_limited = False
    for full in files:
        try:
            size = full.stat().st_size
            if size > MAX_SEARCH_FILE_BYTES:
                skipped_large += 1
                scanned += 1
                continue
            if scanned_bytes + size > MAX_SEARCH_BATCH_BYTES:
                stopped_for_budget = True
                break
            text = _read_text(full)
        except (ToolFailure, OSError):
            scanned += 1
            continue
        scanned += 1
        scanned_bytes += size
        lines = text.split("\n")
        relative = str(full.relative_to(root)).replace("\\", "/")
        for index, line in enumerate(lines):
            if needle.search(line):
                start = max(0, index - context_lines)
                end = min(len(lines), index + context_lines + 1)
                excerpt = "\n".join(lines[start:end])[:max_preview]
                results.append(f"{relative}:{index + 1}: {excerpt}")
                if len(results) >= max_results:
                    results_limited = True
                    break
        if len(results) >= max_results:
            break

    next_offset = file_offset + scanned
    has_more = stopped_for_budget or more_files or scanned < len(files)
    if not results:
        body = f"(no matches for {query!r} in {scanned} files)"
    else:
        body = "\n".join(results)
    if skipped_large:
        body += f"\n... skipped {skipped_large} file(s) larger than {MAX_SEARCH_FILE_BYTES} bytes"
    if has_more:
        body += f"\n... search batch limit reached; continue with file_offset={next_offset}"
    if results_limited:
        body += "\n... result limit reached; narrow the query or search a smaller path."
    return body


# ── apply_patch ───────────────────────────────────────────────


def _patch_evidence(text: str, ranges: list[dict], quality: str = "exact") -> dict:
    lines = text.split("\n")
    return {
        "revision": content_revision(text),
        "revision_algorithm": REVISION_ALGORITHM,
        "total_lines": len(lines) - 1 if text.endswith("\n") else len(lines),
        "changed_ranges": ranges,
        "match_quality": quality,
    }


def _apply_patch(args: dict) -> str:
    patch_text = str(args.get("patch", ""))
    dry_run = bool(args.get("dry_run"))
    if not patch_text.strip():
        raise ToolFailure("INVALID_ARGUMENT", "patch is required", category="validation")

    root = _workspace_root()
    operations = parse_patch(patch_text)
    if not operations:
        raise ToolFailure("PATCH_FAILED", "No files were modified.", category="validation")

    staged: dict[str, dict] = {}
    summaries: list[str] = []
    affected: dict[str, dict] = {}
    warnings: list[str] = []
    additions = 0
    removals = 0
    already_applied = 0

    for op in operations:
        target = _resolve(op.path, root, must_exist=op.kind in {"update", "delete"})
        if op.move_to:
            _resolve(op.move_to, root)

        if op.kind == "add":
            if target.exists():
                raise ToolFailure(
                    "PATCH_FAILED", f"Cannot add file that already exists: {op.path}"
                )
            content = op.add_content or ""
            staged[str(target)] = {
                "path": target,
                "content": content,
                "mode": None,
                "action": "write",
            }
            affected[op.path] = {
                "path": op.path,
                "operation": "add",
                **_patch_evidence(content, [{"start_line": 1, "end_line": len(content.split("\n")), "added_lines": len(content.split("\n")), "removed_lines": 0}]),
            }
            summaries.append(f"A {op.path}")
            additions += len(content.split("\n"))

        elif op.kind == "delete":
            if target.is_dir():
                raise ToolFailure("PATCH_FAILED", f"Cannot delete a directory: {op.path}")
            prior = staged.get(str(target))
            baseline_text = prior["content"] if prior else _read_text(target)
            staged[str(target)] = {
                "path": target,
                "content": None,
                "mode": target.stat().st_mode if target.exists() else None,
                "action": "delete",
            }
            affected[op.path] = {"path": op.path, "operation": "delete", "total_lines": 0}
            summaries.append(f"D {op.path}")
            removals += len(baseline_text.split("\n"))

        else:  # update
            if target.is_dir():
                raise ToolFailure("PATCH_FAILED", f"Cannot update a directory: {op.path}")
            prior = staged.get(str(target))
            if prior is not None and prior["content"] is None:
                raise ToolFailure("PATCH_FAILED", f"Cannot update a deleted file: {op.path}")
            content = prior["content"] if prior else _read_text(target)
            outcome = apply_update_hunks_detailed(content, op.hunks, op.path)
            updated = outcome.content
            warnings.extend(f"{op.path}: {item}" for item in outcome.warnings)
            additions += sum(1 for hunk in op.hunks for line in hunk.lines if line.startswith("+"))
            removals += sum(1 for hunk in op.hunks for line in hunk.lines if line.startswith("-"))
            if outcome.applied_hunks == 0 and outcome.already_applied_hunks:
                already_applied += 1
            evidence = _patch_evidence(updated, outcome.changed_ranges, outcome.match_quality)
            destination = _resolve(op.move_to, root) if op.move_to else target
            staged[str(destination)] = {
                "path": destination,
                "content": updated,
                "mode": target.stat().st_mode if target.exists() else None,
                "action": "write",
            }
            if op.move_to and destination != target:
                staged[str(target)] = {
                    "path": target,
                    "content": None,
                    "mode": None,
                    "action": "delete",
                }
                affected[op.path] = {
                    "path": op.path,
                    "operation": "move",
                    "destination": op.move_to,
                    **evidence,
                }
                summaries.append(f"M {op.path} -> {op.move_to}")
            else:
                affected[op.path] = {"path": op.path, "operation": "update", **evidence}
                summaries.append(f"{'=' if outcome.applied_hunks == 0 and outcome.already_applied_hunks else 'M'} {op.path}")

    if not affected:
        raise ToolFailure("PATCH_FAILED", "No files were modified.", category="validation")

    if dry_run:
        return json.dumps(
            {
                "dry_run": True,
                "already_applied": already_applied == len(operations) and already_applied > 0,
                "summary": "\n".join(summaries),
                "affected_files": list(affected.values()),
                "additions": additions,
                "removals": removals,
                "warnings": warnings,
            },
            ensure_ascii=False,
            indent=2,
        )

    # Commit everything, rolling back on the first failure.
    committed: list[dict] = []
    for entry in staged.values():
        try:
            if entry["action"] == "delete":
                atomic_delete(entry["path"])
            else:
                atomic_write(entry["path"], entry["content"], entry["mode"])
            committed.append(entry)
        except Exception:
            for done in committed:
                try:
                    if done["action"] == "delete":
                        atomic_write(done["path"], done["content"], done["mode"])
                    else:
                        atomic_delete(done["path"])
                except OSError:
                    pass
            raise

    return json.dumps(
        {
            "dry_run": False,
            "already_applied": already_applied == len(operations) and already_applied > 0,
            "revision_algorithm": REVISION_ALGORITHM,
            "summary": "\n".join(summaries),
            "affected_files": list(affected.values()),
            "additions": additions,
            "removals": removals,
            "warnings": warnings,
        },
        ensure_ascii=False,
        indent=2,
    )


# ── apply_changes ─────────────────────────────────────────────


def _apply_changes(args: dict) -> str:
    raw = args.get("changes")
    dry_run = bool(args.get("dry_run"))
    changes = parse_changes(raw)

    root = _workspace_root()
    seen: set[str] = set()
    staged: dict[str, dict] = {}
    affected: dict[str, dict] = {}
    summaries: list[str] = []
    additions = 0
    removals = 0
    unchanged = 0

    for change in changes:
        # A path may appear once per call, as path or as destination.
        resolved_path = _resolve(change.path, root, must_exist=change.action != "create")
        key = str(resolved_path).lower()
        if key in seen:
            raise ToolFailure(
                "INVALID_ARGUMENT",
                f"Duplicate path in one call: {change.path}",
                category="validation",
            )
        seen.add(key)
        if change.destination:
            dest = _resolve(change.destination, root)
            if str(dest).lower() in seen:
                raise ToolFailure(
                    "INVALID_ARGUMENT",
                    f"Duplicate destination in one call: {change.destination}",
                    category="validation",
                )
            seen.add(str(dest).lower())

        if change.action == "create":
            if resolved_path.exists():
                raise ToolFailure(
                    "PATCH_FAILED", f"Cannot create an existing file: {change.path}"
                )
            content = change.content or ""
            staged[str(resolved_path)] = {
                "path": resolved_path,
                "content": content,
                "mode": None,
                "action": "write",
            }
            affected[change.path] = {
                "path": change.path,
                "operation": "create",
                **_patch_evidence(content, []),
            }
            summaries.append(f"A {change.path}")
            additions += len(content.split("\n"))
            continue

        # Every action besides create needs the revision read_file published.
        if not resolved_path.exists():
            raise ToolFailure("NOT_FOUND", f"No such file: {change.path}", category="not_found")
        current = _read_text(resolved_path)
        actual = content_revision(current)
        if not change.revision:
            raise ToolFailure(
                "REVISION_REQUIRED",
                f"revision is required to modify {change.path}",
                details={"path": change.path, "current_revision": actual},
            )
        if change.revision != actual:
            raise ToolFailure(
                "REVISION_MISMATCH",
                f"revision for {change.path} is stale",
                details={"path": change.path, "current_revision": actual},
            )

        if change.action == "write":
            content = change.content or ""
            if content == current:
                unchanged += 1
            staged[str(resolved_path)] = {
                "path": resolved_path,
                "content": content,
                "mode": resolved_path.stat().st_mode,
                "action": "write",
            }
            affected[change.path] = {
                "path": change.path,
                "operation": "write",
                **_patch_evidence(content, []),
            }
            summaries.append(f"W {change.path}")
            additions += len(content.split("\n"))
            removals += len(current.split("\n"))

        elif change.action == "edit":
            updated = apply_line_edits(current, change.edits, change.path)
            if updated == current:
                unchanged += 1
            staged[str(resolved_path)] = {
                "path": resolved_path,
                "content": updated,
                "mode": resolved_path.stat().st_mode,
                "action": "write",
            }
            affected[change.path] = {
                "path": change.path,
                "operation": "edit",
                **_patch_evidence(updated, []),
            }
            summaries.append(f"E {change.path}")
            additions += sum(
                len((edit.content or "").split("\n"))
                for edit in change.edits
                if edit.op in {"replace", "insert_after", "insert_before"}
            )
            removals += sum(
                (edit.end_line or 0) - (edit.start_line or 0) + 1
                for edit in change.edits
                if edit.op in {"replace", "delete"}
            )

        elif change.action == "delete":
            staged[str(resolved_path)] = {
                "path": resolved_path,
                "content": None,
                "mode": None,
                "action": "delete",
            }
            affected[change.path] = {"path": change.path, "operation": "delete", "total_lines": 0}
            summaries.append(f"D {change.path}")
            removals += len(current.split("\n"))

        else:  # move | copy
            if not change.destination:
                raise ToolFailure(
                    "INVALID_ARGUMENT",
                    f"destination is required for action '{change.action}'",
                    category="validation",
                )
            if dest.exists():
                raise ToolFailure(
                    "PATCH_FAILED", f"Cannot {change.action} over an existing file: {change.destination}"
                )
            if change.action == "move":
                staged[str(dest)] = {
                    "path": dest,
                    "content": current,
                    "mode": resolved_path.stat().st_mode,
                    "action": "write",
                }
                staged[str(resolved_path)] = {
                    "path": resolved_path,
                    "content": None,
                    "mode": None,
                    "action": "delete",
                }
                affected[change.path] = {
                    "path": change.path,
                    "operation": "move",
                    "destination": change.destination,
                    **_patch_evidence(current, []),
                }
                summaries.append(f"M {change.path} -> {change.destination}")
            else:
                staged[str(dest)] = {
                    "path": dest,
                    "content": current,
                    "mode": resolved_path.stat().st_mode,
                    "action": "write",
                }
                affected[change.path] = {
                    "path": change.path,
                    "operation": "copy",
                    "destination": change.destination,
                    **_patch_evidence(current, []),
                }
                summaries.append(f"C {change.path} -> {change.destination}")

    if not affected:
        raise ToolFailure("PATCH_FAILED", "No files were modified.", category="validation")

    payload = {
        "dry_run": dry_run,
        "already_applied": unchanged == len(changes) and unchanged > 0,
        "revision_algorithm": REVISION_ALGORITHM,
        "summary": "\n".join(summaries),
        "affected_files": list(affected.values()),
        "additions": additions,
        "removals": removals,
        "warnings": [],
    }
    if dry_run:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    committed: list[dict] = []
    for entry in staged.values():
        try:
            if entry["action"] == "delete":
                atomic_delete(entry["path"])
            else:
                atomic_write(entry["path"], entry["content"], entry["mode"])
            committed.append(entry)
        except Exception:
            for done in committed:
                try:
                    if done["action"] == "delete":
                        atomic_write(done["path"], done["content"], done["mode"])
                    else:
                        atomic_delete(done["path"])
                except OSError:
                    pass
            raise

    return json.dumps(payload, ensure_ascii=False, indent=2)


# ── exec_command ──────────────────────────────────────────────


def _capture_job_output(job: _CommandJob, stream) -> None:
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            with job.lock:
                job.output.extend(chunk)
                job.total_output += len(chunk)
                overflow = len(job.output) - MAX_COMMAND_JOB_OUTPUT_BYTES
                if overflow > 0:
                    del job.output[:overflow]
                    job.output_base += overflow
    finally:
        try:
            stream.close()
        except OSError:
            pass
        with job.lock:
            job.readers_done += 1


def _prune_command_jobs() -> None:
    now = time.monotonic()
    with _COMMAND_JOBS_LOCK:
        for job_id, job in list(_COMMAND_JOBS.items()):
            if job.process.poll() is not None and job.completed_at is None:
                with job.lock:
                    if job.readers_done == 2:
                        job.completed_at = now
                        if job.windows_job_handle:
                            _close_windows_job_object(job.windows_job_handle)
                            job.windows_job_handle = None
            if job.completed_at is not None and now - job.completed_at > COMMAND_JOB_RETENTION_SECONDS:
                _COMMAND_JOBS.pop(job_id, None)
        completed = sorted(
            (
                (job.completed_at or now, job_id)
                for job_id, job in _COMMAND_JOBS.items()
                if job.process.poll() is not None
            )
        )
        while len(_COMMAND_JOBS) > 64 and completed:
            _, job_id = completed.pop(0)
            _COMMAND_JOBS.pop(job_id, None)


def _start_command_job(command: str, cwd: Path, stdin_text: str) -> str:
    _prune_command_jobs()
    with _COMMAND_JOBS_LOCK:
        active = sum(job.process.poll() is None for job in _COMMAND_JOBS.values())
        if active >= MAX_COMMAND_JOBS:
            return f"status=busy\nAt most {MAX_COMMAND_JOBS} background commands may run at once."

        process_options = {}
        if os.name == "nt":
            windows_job_handle = _create_windows_job_object()
            process_options["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            windows_job_handle = None
            process_options["start_new_session"] = True
        try:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                **process_options,
            )
        except Exception:
            _close_windows_job_object(windows_job_handle)
            raise
        windows_job_handle = _assign_process_to_windows_job(
            process, windows_job_handle
        )
        job = _CommandJob(
            process=process,
            started_at=time.monotonic(),
            windows_job_handle=windows_job_handle,
        )
        job_id = uuid.uuid4().hex[:16]
        _COMMAND_JOBS[job_id] = job

    for stream in (process.stdout, process.stderr):
        threading.Thread(
            target=_capture_job_output,
            args=(job, stream),
            daemon=True,
        ).start()

    def feed_stdin() -> None:
        try:
            if stdin_text:
                process.stdin.write(stdin_text.encode("utf-8"))
                process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    threading.Thread(target=feed_stdin, daemon=True).start()
    return json.dumps(
        {
            "job_id": job_id,
            "status": "running",
            "output_offset": 0,
            "message": "Poll exec_command with job_id and output_offset to collect output.",
        },
        ensure_ascii=False,
    )


def _terminate_command_job(job: _CommandJob) -> None:
    process = job.process
    job.cancelled = True
    if os.name == "nt" and job.windows_job_handle:
        handle = job.windows_job_handle
        job.windows_job_handle = None
        _close_windows_job_object(handle)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
        return
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=2,
                check=False,
            )
            if result.returncode != 0:
                process.kill()
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            try:
                process.terminate()
            except OSError:
                pass


def _poll_command_job(args: dict) -> str:
    job_id = str(args.get("job_id") or "").strip()
    with _COMMAND_JOBS_LOCK:
        job = _COMMAND_JOBS.get(job_id)
    if job is None:
        return f"status=not_found\nNo active or retained command job: {job_id}"

    if bool(args.get("cancel")) and job.process.poll() is None:
        _terminate_command_job(job)

    try:
        requested_offset = max(0, int(args.get("output_offset") or 0))
        max_output = min(
            max(1, int(args.get("max_output_bytes") or MAX_OUTPUT_BYTES)),
            MAX_OUTPUT_BYTES,
        )
    except (TypeError, ValueError):
        return "status=invalid_argument\noutput_offset and max_output_bytes must be integers."

    return_code = job.process.poll()
    completed = False
    with job.lock:
        if return_code is not None and job.readers_done == 2 and job.completed_at is None:
            job.completed_at = time.monotonic()
        if requested_offset > job.total_output:
            return f"status=invalid_argument\noutput_offset exceeds {job.total_output}."
        start_offset = max(requested_offset, job.output_base)
        start_index = start_offset - job.output_base
        end_index = min(len(job.output), start_index + max_output)
        output = bytes(job.output[start_index:end_index])
        next_offset = start_offset + len(output)
        dropped = max(0, job.output_base - requested_offset)
        readers_done = job.readers_done
        total_output = job.total_output
        completed = return_code is not None and readers_done == 2
        cancelled = job.cancelled
        if completed and job.windows_job_handle:
            _close_windows_job_object(job.windows_job_handle)
            job.windows_job_handle = None

    payload = {
        "job_id": job_id,
        "status": (
            "cancelled" if completed and cancelled
            else "completed" if completed
            else "cancelling" if cancelled
            else "running"
        ),
        "exit_code": return_code if completed else None,
        "output": output.decode("utf-8", errors="replace"),
        "output_offset": next_offset,
        "total_output_bytes": total_output,
    }
    if dropped:
        payload["warning"] = f"{dropped} earlier output bytes were dropped from the bounded buffer."
    return json.dumps(payload, ensure_ascii=False)


def _sync_command_timeout_ms(requested_ms: int) -> int:
    try:
        from src.utils.config_manager import get_config

        call_timeout = float(get_config().get_config("MCP_TOOLS.CALL_TIMEOUT", 45))
    except Exception:
        call_timeout = 45.0
    if call_timeout <= 0:
        return min(requested_ms, MAX_TIMEOUT_MS)
    remaining_ms = max(1, int(call_timeout * 1000) - 5000)
    return min(requested_ms, MAX_TIMEOUT_MS, remaining_ms)


def _exec_command(args: dict) -> str:
    job_id = str(args.get("job_id") or "").strip()
    if job_id:
        return _poll_command_job(args)

    command = str(args.get("cmd", "")).strip()
    if not command:
        raise ToolFailure("INVALID_ARGUMENT", "cmd is required", category="validation")
    if not is_command_safe(command):
        raise ToolFailure(
            "PERMISSION_REQUIRED",
            f"Command rejected by the safety policy: {command}",
            category="security",
        )
    if _is_pdf_page_dump_command(command):
        return (
            "USE_DOCUMENT_TOOL: This command reads one PDF page at a time and can "
            "cause a long sequence of MCP calls. It was not executed. Use "
            "manage_document(action='read', path=<pdf>, entry_number=<requested "
            "entry>) to return the exact numbered section in one bounded call. "
            "For other PDF content, use page_start/page_end continuation."
        )

    root = _workspace_root()
    workdir = str(args.get("workdir") or args.get("cwd") or "")
    cwd = _resolve(workdir or ".", root, must_exist=True) if workdir else root
    timeout_ms = min(int(args.get("timeout_ms") or DEFAULT_TIMEOUT_MS), MAX_TIMEOUT_MS)
    max_output = min(
        max(1, int(args.get("max_output_bytes") or MAX_OUTPUT_BYTES)),
        MAX_OUTPUT_BYTES,
    )
    stdin_text = str(args.get("stdin") or "")

    if bool(args.get("background")):
        return _start_command_job(command, cwd, stdin_text)

    timeout_ms = _sync_command_timeout_ms(timeout_ms)
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            capture_output=True,
            text=True,
            input=stdin_text or None,
            timeout=timeout_ms / 1000,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (
            "status=timeout\n"
            f"Command exceeded the effective {timeout_ms} ms MCP-safe budget.\n"
            "Retry with background=true and poll using the returned job_id."
        )
    except OSError as exc:
        return f"status=failed\nexit_code=-1\n{exc}"

    stdout = (result.stdout or "")[:max_output]
    stderr = (result.stderr or "")[:max_output]
    status = "exited" if result.returncode == 0 else "failed"
    parts = [f"status={status}", f"exit_code={result.returncode}"]
    if stdout:
        parts.append(f"--- stdout ---\n{stdout.rstrip()}")
    if stderr:
        parts.append(f"--- stderr ---\n{stderr.rstrip()}")
    return "\n".join(parts)


# ── git tools ─────────────────────────────────────────────────


def _git(args: list[str], cwd: Path, max_bytes: int = MAX_GIT_BYTES) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        raise ToolFailure("GIT_ERROR", "git is not installed", category="runtime")
    except subprocess.TimeoutExpired:
        raise ToolFailure("GIT_ERROR", "git command timed out", category="runtime")
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "git failed").strip()
        raise ToolFailure("GIT_ERROR", message, category="runtime")
    output = (result.stdout or "").rstrip("\n")
    return output[:max_bytes]


def _git_root(root: Path, rel: str) -> Path:
    """Resolve a path argument to the directory git must run in."""
    path = _resolve(rel or ".", root, must_exist=True)
    return path if path.is_dir() else path.parent


def _git_status(args: dict) -> str:
    root = _workspace_root()
    path = _git_root(root, str(args.get("path", "") or "."))
    include_untracked = bool(args.get("include_untracked", True))
    max_entries = int(args.get("max_entries") or MAX_LIST_ENTRIES)

    flags = ["--short", "--branch"]
    if include_untracked:
        flags.append("--untracked-files=all")
    else:
        flags.append("--untracked-files=no")
    output = _git(["status", *flags], path)
    lines = output.split("\n")
    if len(lines) > max_entries:
        lines = lines[:max_entries] + [f"... ({len(lines) - max_entries} more entries omitted)"]
    return "\n".join(lines)


def _git_diff(args: dict) -> str:
    root = _workspace_root()
    path = _git_root(root, str(args.get("path", "") or "."))
    staged = bool(args.get("staged"))
    unstaged = args.get("unstaged")
    unstaged = True if unstaged is None else bool(unstaged)
    include_untracked = bool(args.get("include_untracked", True))
    context_lines = int(args.get("context_lines") or 3)
    max_bytes = int(args.get("max_bytes") or MAX_GIT_BYTES)

    parts: list[str] = []
    if staged:
        parts.append(
            _git(
                ["diff", "--cached", f"--unified={context_lines}", "--"], path
            )
        )
    if unstaged:
        parts.append(
            _git(["diff", f"--unified={context_lines}", "--"], path)
        )
    if include_untracked and unstaged:
        try:
            untracked = _git(
                ["ls-files", "--others", "--exclude-standard"], path
            ).split("\n")
        except ToolFailure:
            untracked = []
        for entry in [e for e in untracked if e][:100]:
            full = path / entry
            if full.is_file():
                content = full.read_text(encoding="utf-8", errors="replace")
                parts.append(
                    f"diff --git a/{entry} b/{entry}\n"
                    "new file mode 100644\n"
                    f"--- /dev/null\n+++ b/{entry}\n"
                    + "\n".join(f"+{line}" for line in content.split("\n"))
                )

    body = "\n".join(part for part in parts if part).rstrip()
    if not body:
        return "(no changes)"
    return body[:max_bytes]


def _git_log(args: dict) -> str:
    root = _workspace_root()
    path = _git_root(root, str(args.get("path", "") or "."))
    ref = str(args.get("ref") or "")
    max_count = int(args.get("max_count") or 20)
    skip = int(args.get("skip") or 0)

    command = [
        "log",
        f"--max-count={max_count}",
        f"--skip={skip}",
        "--pretty=format:%h %an <%ae> %ad%n%s%n",
        "--date=short",
    ]
    if ref:
        command.append(ref)
    return _git(command, path)


def _git_show(args: dict) -> str:
    root = _workspace_root()
    rev = str(args.get("rev", "")).strip()
    if not rev:
        raise ToolFailure("INVALID_ARGUMENT", "rev is required", category="validation")
    path = _git_root(root, str(args.get("path", "") or "."))
    include_diff = bool(args.get("include_diff", True))
    context_lines = int(args.get("context_lines") or 3)
    max_bytes = int(args.get("max_bytes") or MAX_GIT_BYTES)

    command = ["show", "--no-color", f"--unified={context_lines}"]
    if not include_diff:
        command.append("--stat")
    command += [rev, "--"]
    return _git(command, path, max_bytes)


def _git_blame(args: dict) -> str:
    root = _workspace_root()
    rel = str(args.get("path", ""))
    path = _git_root(root, rel)
    target = _resolve(rel, root, must_exist=True)
    if target.is_dir():
        raise ToolFailure("IS_DIRECTORY", f"Is a directory: {rel}", category="validation")
    rev = str(args.get("rev") or "")
    start_line = int(args.get("start_line") or 1)
    end_line = int(args.get("end_line") or 0)
    max_lines = int(args.get("max_lines") or 200)

    command = ["blame", "--line-porcelain"]
    if rev:
        command += [rev]
    end = end_line if end_line > 0 else start_line
    command += ["-L", f"{start_line},{end}", "--", target.name]
    output = _git(command, target.parent if target.is_file() else target)

    lines: list[str] = []
    for line in output.split("\n"):
        if line.startswith("author ") or line.startswith("summary "):
            lines.append(line)
    if not lines:
        return "(no blame output)"
    return "\n".join(lines[: max_lines * 2])


# ── async wrappers ────────────────────────────────────────────


def _run_sync(fn, args: dict) -> str:
    try:
        return fn(args)
    except ToolFailure as failure:
        return f"{failure.code}: {failure.message}"
    except Exception as exc:  # noqa: BLE001 — tools must never crash the server
        logger.error(f"coding tool failed: {exc}", exc_info=True)
        return f"INTERNAL_ERROR: {exc}"


async def read_file(args: dict) -> str:
    return await asyncio.to_thread(_run_sync, _read_file, args)


async def list_dir(args: dict) -> str:
    return await asyncio.to_thread(_run_sync, _list_dir, args)


async def list_files(args: dict) -> str:
    return await asyncio.to_thread(_run_sync, _list_files, args)


async def search_text(args: dict) -> str:
    return await asyncio.to_thread(_run_sync, _search_text, args)


async def apply_patch(args: dict) -> str:
    return await asyncio.to_thread(_run_sync, _apply_patch, args)


async def apply_changes(args: dict) -> str:
    return await asyncio.to_thread(_run_sync, _apply_changes, args)


async def exec_command(args: dict) -> str:
    return await asyncio.to_thread(_run_sync, _exec_command, args)


async def git_status(args: dict) -> str:
    return await asyncio.to_thread(_run_sync, _git_status, args)


async def git_diff(args: dict) -> str:
    return await asyncio.to_thread(_run_sync, _git_diff, args)


async def git_log(args: dict) -> str:
    return await asyncio.to_thread(_run_sync, _git_log, args)


async def git_show(args: dict) -> str:
    return await asyncio.to_thread(_run_sync, _git_show, args)


async def git_blame(args: dict) -> str:
    return await asyncio.to_thread(_run_sync, _git_blame, args)
