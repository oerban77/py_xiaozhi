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
the long-lived command pool (``write_stdin``/``read_output``/``kill_command``)
and the MCP-protocol introspection tools (``server_info``,
``check_exec_environment``, ``request_permissions``) — py_xiaozhi is not a
standalone MCP server, so session machinery and permission negotiation do not
apply.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
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
BINARY_PROBE_BYTES = 4096
MAX_DEPTH = 8

# Office/binary document suffixes handled via the documents tool extractors when
# they arrive as chat attachments (read_file itself only accepts UTF-8 text).
_BINARY_DOCUMENT_SUFFIXES = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
}

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


# ── workspace resolution ──────────────────────────────────────


def _workspace_root() -> Path:
    """The root all relative paths resolve against."""
    try:
        from src.utils.config_manager import get_config

        cfg = get_config()
        configured = (cfg.get_config("CODING", {}) or {}).get("WORKSPACE", "")
        if configured:
            root = Path(configured).expanduser()
            if root.is_dir():
                return root.resolve()
    except Exception:
        pass
    return Path.cwd().resolve()


def _is_within(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve(path: str, root: Path, *, must_exist: bool = False) -> Path:
    """Resolve a workspace-relative path, rejecting escapes."""
    if not path or not isinstance(path, str):
        raise ToolFailure("INVALID_ARGUMENT", "path is required", category="validation")
    if "\x00" in path:
        raise ToolFailure("INVALID_ARGUMENT", "path contains NUL bytes", category="security")
    if os.path.isabs(path):
        raise ToolFailure(
            "ABSOLUTE_PATH_DENIED",
            f"Paths must be workspace-relative, not absolute: {path}",
            category="security",
        )
    if Path(path).parts and Path(path).parts[0] == "..":
        raise ToolFailure(
            "PATH_OUTSIDE_WORKSPACE",
            f"Path escapes the workspace: {path}",
            category="security",
        )

    resolved = (root / path).resolve()
    if not _is_within(root, resolved):
        raise ToolFailure(
            "PATH_OUTSIDE_WORKSPACE",
            f"Path escapes the workspace: {path}",
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


def _read_attached_document(path: Path) -> str:
    """Read a chat-attached document as text, extracting Office/PDF content."""
    if path.suffix.lower() not in _BINARY_DOCUMENT_SUFFIXES:
        return _read_text(path)

    # .docx/.xlsx/.pdf are binary: reuse the documents tool extractors so the
    # LLM receives readable text instead of a BINARY_FILE rejection.
    # _read_file runs in a worker thread, so call the sync implementation.
    from src.mcp.tools.documents.service import _document_manage_sync

    return _document_manage_sync({"action": "read", "path": str(path)})


def _line_slice(text: str, start_line: int, end_line: int) -> tuple[str, int]:
    lines = text.split("\n")
    total = len(lines) - 1 if text.endswith("\n") else len(lines)
    start = max(1, start_line)
    stop = total if end_line <= 0 else min(total, end_line)
    if stop < start:
        return "", total
    selected = lines[start - 1 : stop]
    return "\n".join(selected), total


# ── read_file ─────────────────────────────────────────────────


_PENDING_DOCUMENT_PROVIDER: Callable[[], tuple[str, str] | None] | None = None


def set_pending_document_provider(
    provider: Callable[[], tuple[str, str] | None] | None,
) -> None:
    """Inject the chat-attachment provider (set by McpServer.add_common_tools)."""
    global _PENDING_DOCUMENT_PROVIDER
    _PENDING_DOCUMENT_PROVIDER = provider


def _read_file(args: dict) -> str:
    root = _workspace_root()
    pending = _PENDING_DOCUMENT_PROVIDER() if _PENDING_DOCUMENT_PROVIDER else None
    if pending is not None:
        # A document attached in the desktop chat takes precedence over the
        # workspace path: read it directly so the LLM can analyze the content.
        path = Path(pending[0]).expanduser().resolve()
        if not path.is_file():
            raise ToolFailure(
                "NOT_FOUND", f"Attached file is unavailable: {path}", category="not_found"
            )
        args = dict(args)
        args["path"] = str(path)
        root = path.parent
        text = _read_attached_document(path)
        # An attachment is a one-shot read: hand the whole document to the LLM
        # so it can answer questions about it, instead of paginating it.
        start_line = 1
        end_line = 0
        max_lines = 0
        max_bytes = MAX_READ_BYTES * 4
    else:
        path = _resolve(str(args.get("path", "")), root, must_exist=True)
        if path.is_dir():
            raise ToolFailure(
                "IS_DIRECTORY", f"Is a directory: {path}", category="validation"
            )
        text = _read_text(path)
        start_line = int(args.get("start_line") or 1)
        end_line = int(args.get("end_line") or 0)
        max_lines = int(args.get("max_lines") or 0)
        max_bytes = int(args.get("max_bytes") or MAX_READ_BYTES)

    total_bytes = len(text.encode("utf-8"))
    revision = content_revision(text)
    content, total_lines = _line_slice(text, start_line, end_line)

    if max_lines > 0:
        allowed = content.split("\n")[:max_lines]
        content = "\n".join(allowed)
    if len(content.encode("utf-8")) > max_bytes:
        content = content.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        content += f"\n... (truncated at {max_bytes} bytes)"

    shown_end = start_line + len(content.split("\n")) - 1
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
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                filenames = [f for f in filenames if not f.startswith(".")]
            for name in sorted(dirnames):
                entries.append(f"{Path(dirpath, name).relative_to(root)}/")
            for name in sorted(filenames):
                entries.append(str(Path(dirpath, name).relative_to(root)))
            if len(entries) >= max_entries:
                break
    else:
        for entry in sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if not include_hidden and entry.name.startswith("."):
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
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            filenames = [f for f in filenames if not f.startswith(".")]
        for name in filenames:
            full = Path(dirpath, name)
            relative = str(full.relative_to(root))
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
    matches = sorted(set(matches))[:max_results]
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
    context_lines = int(args.get("context_lines") or 0)
    max_results = int(args.get("max_results") or MAX_SEARCH_RESULTS)
    max_preview = int(args.get("max_preview_bytes") or MAX_PREVIEW_BYTES)

    flags = 0 if case_sensitive else re.IGNORECASE
    needle = re.compile(query, flags) if use_regex else re.compile(re.escape(query), flags)

    globs = [p.strip() for p in glob_patterns.replace(",", "\n").split("\n") if p.strip()]
    results: list[str] = []
    scanned = 0

    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in {".git", "__pycache__"}]
        for name in filenames:
            if globs and not any(fnmatch.fnmatch(name, pat) for pat in globs):
                continue
            full = Path(dirpath, name)
            if full.is_symlink() and not _is_within(root, full.resolve()):
                continue
            try:
                text = _read_text(full)
            except (ToolFailure, OSError):
                continue
            scanned += 1
            lines = text.split("\n")
            relative = str(full.relative_to(root))
            for index, line in enumerate(lines):
                if needle.search(line):
                    start = max(0, index - context_lines)
                    end = min(len(lines), index + context_lines + 1)
                    excerpt = "\n".join(lines[start:end])[:max_preview]
                    results.append(
                        f"{relative}:{index + 1}: {excerpt}"
                    )
                    if len(results) >= max_results:
                        results.append(f"... (stopped after {max_results} matches in {scanned} files)")
                        return "\n".join(results)
    if not results:
        return f"(no matches for {query!r} under {rel})"
    return "\n".join(results)


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


def _exec_command(args: dict) -> str:
    command = str(args.get("cmd", "")).strip()
    if not command:
        raise ToolFailure("INVALID_ARGUMENT", "cmd is required", category="validation")
    if not is_command_safe(command):
        raise ToolFailure(
            "PERMISSION_REQUIRED",
            f"Command rejected by the safety policy: {command}",
            category="security",
        )

    root = _workspace_root()
    workdir = str(args.get("workdir") or args.get("cwd") or "")
    cwd = _resolve(workdir or ".", root, must_exist=True) if workdir else root
    timeout_ms = min(int(args.get("timeout_ms") or DEFAULT_TIMEOUT_MS), MAX_TIMEOUT_MS)
    max_output = int(args.get("max_output_bytes") or MAX_OUTPUT_BYTES)
    stdin_text = str(args.get("stdin") or "")

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
            f"Command exceeded the {timeout_ms} ms budget and was terminated.\n"
            "Reduce the scope of the command or raise timeout_ms."
        )
    except OSError as exc:
        return f"status=failed\nexit_code=-1\n{exc}"

    stdout = (result.stdout or "")[:max_output]
    stderr = (result.stderr or "")[:max_output]
    status = "exited" if result.returncode == 0 else "failed"
    parts = [
        f"status={status}",
        f"exit_code={result.returncode}",
    ]
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
