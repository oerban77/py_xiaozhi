"""V4A patch parsing and application.

Faithful, dependency-free re-implementation of the patching layer from
https://github.com/xyTom/coding-tools-mcp (``coding_tools_mcp/patching.py``),
which is Apache-2.0 licensed.

Supports the ``*** Begin Patch`` / ``*** End Patch`` envelope with
``*** Add File``, ``*** Update File``, ``*** Delete File`` and ``*** Move to``
operations, plus the line-addressed ``apply_changes`` edits.

Semantics kept from the original:
- Lines are split on ``\\n`` only, so the trailing empty element *is* the
  final newline and a line containing another Unicode line boundary stays one
  line for both the file and the patch.
- Hunk matching is graded: ``exact``, then ignoring trailing whitespace, then
  ignoring indentation width. The grade actually used is reported back.
- A hunk whose result is already present reports ``already_applied`` instead
  of failing, provided it carries anchoring context.
- BOM and CRLF/LF style are preserved; writes are atomic.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

END_OF_FILE_MARKER = "*** End of File"
REVISION_ALGORITHM = "sha256"

MATCH_GRADES = ("exact", "trailing_ws", "indent")
MATCH_GRADE_WARNINGS = {
    "trailing_ws": "context matched only after ignoring trailing whitespace",
    "indent": "context matched only after ignoring indentation width",
}
ALREADY_APPLIED_GRADES = ("exact", "trailing_ws")

NEARBY_CONTEXT_LINES = 6
NEARBY_TEXT_MAX_BYTES = 800
MAX_REPORTED_CANDIDATES = 8

_UNIFIED_HEADER = re.compile(r"^-\d+(,\d+)?\s+\+\d+(,\d+)?\s*(@@.*)?$")


class ToolFailure(Exception):
    """A tool-level failure with a stable machine code."""

    def __init__(
        self,
        code: str,
        message: str,
        category: str = "validation",
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.category = category
        self.details = details or {}


def content_revision(text: str) -> str:
    """SHA-256 of a file's UTF-8 bytes — the optimistic-concurrency token."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PatchHunk:
    lines: list[str]
    scope: str | None = None


@dataclass
class PatchOperation:
    kind: str  # add | update | delete
    path: str
    add_content: str | None = None
    hunks: list[PatchHunk] = field(default_factory=list)
    move_to: str | None = None


@dataclass(frozen=True)
class ParsedHunk:
    old: list[str]
    new: list[str]
    new_sources: list[int | None] = field(default_factory=list)
    scope: str | None = None
    eof_anchor: bool = False


@dataclass(frozen=True)
class MatchedHunk:
    hunk_index: int
    start: int
    end: int
    new: list[str]
    quality: str = "exact"
    scope_used: bool = False
    old: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UpdateOutcome:
    content: str
    changed_ranges: list[dict[str, int]]
    match_quality: str
    warnings: list[str]
    already_applied_hunks: list[int]
    applied_hunks: int


# ── text normalization ────────────────────────────────────────


def strip_bom(text: str) -> tuple[str, str]:
    if text.startswith("\ufeff"):
        return "\ufeff", text[len("\ufeff") :]
    return "", text


def detect_line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    if ending == "\n":
        return text
    return text.replace("\n", ending)


# ── patch parsing ─────────────────────────────────────────────


def _header_scope(line: str) -> str | None:
    """Return the searchable scope text a ``@@`` header carries, if any."""
    text = line[2:].strip()
    if text.endswith("@@"):
        text = text[:-2].strip()
    if not text or _UNIFIED_HEADER.match(text):
        return None
    return text


def parse_patch(patch: str) -> list[PatchOperation]:
    # split("\n") rather than splitlines(): a context line carrying a form feed
    # or U+2028 must stay one patch line so it can match the file line it came
    # from. The envelope closes on the last non-empty line because the patch
    # text's own trailing newline(s) become trailing empty elements here.
    lines = normalize_to_lf(patch).split("\n")
    while lines and not lines[-1]:
        lines.pop()
    if (
        not lines
        or lines[0].strip() != "*** Begin Patch"
        or lines[-1].strip() != "*** End Patch"
    ):
        raise ToolFailure(
            "PATCH_FAILED",
            "Patch must use *** Begin Patch / *** End Patch envelope.",
        )

    operations: list[PatchOperation] = []
    i = 1
    while i < len(lines) - 1:
        line = lines[i]
        if not line:
            i += 1
            continue
        if line.startswith("*** Add File: "):
            path = line[len("*** Add File: ") :].strip()
            content_lines: list[str] = []
            i += 1
            while i < len(lines) - 1 and not lines[i].startswith("*** "):
                content_lines.append(lines[i])
                i += 1
            operations.append(
                PatchOperation("add", path, add_content="\n".join(content_lines))
            )
            continue
        if line.startswith("*** Delete File: "):
            operations.append(
                PatchOperation("delete", line[len("*** Delete File: ") :].strip())
            )
            i += 1
            continue
        if line.startswith("*** Update File: "):
            path = line[len("*** Update File: ") :].strip()
            move_to = None
            i += 1
            if i < len(lines) - 1 and lines[i].startswith("*** Move to: "):
                move_to = lines[i][len("*** Move to: ") :].strip()
                i += 1
            hunks: list[PatchHunk] = []
            current: list[str] = []
            current_scope: str | None = None
            # `*** End of File` is the one `*** ` line that belongs to a hunk
            # rather than terminating the file block: it anchors the hunk at EOF.
            while i < len(lines) - 1 and (
                not lines[i].startswith("*** ")
                or lines[i].rstrip() == END_OF_FILE_MARKER
            ):
                if lines[i].startswith("@@"):
                    if current:
                        hunks.append(PatchHunk(current, current_scope))
                    current = []
                    current_scope = _header_scope(lines[i])
                elif lines[i].rstrip() == END_OF_FILE_MARKER:
                    current.append(END_OF_FILE_MARKER)
                else:
                    current.append(lines[i])
                i += 1
            if current:
                hunks.append(PatchHunk(current, current_scope))
            operations.append(
                PatchOperation("update", path, hunks=hunks, move_to=move_to)
            )
            continue
        raise ToolFailure(
            "PATCH_FAILED", f"Unrecognized patch line: {line}", category="validation"
        )
    return operations


def parse_update_hunk(hunk: PatchHunk | list[str]) -> ParsedHunk:
    raw_lines = hunk.lines if isinstance(hunk, PatchHunk) else list(hunk)
    scope = hunk.scope if isinstance(hunk, PatchHunk) else None
    old: list[str] = []
    new: list[str] = []
    new_sources: list[int | None] = []
    eof_anchor = False
    for raw in raw_lines:
        if raw == END_OF_FILE_MARKER:
            eof_anchor = True
            continue
        if not raw:
            # V4A spells an empty context line as a single space, which model
            # output and intermediate layers routinely strip to "".
            new_sources.append(len(old))
            old.append("")
            new.append("")
            continue
        marker = raw[0]
        value = raw[1:] if marker in {" ", "-", "+"} else raw
        if marker == " ":
            new_sources.append(len(old))
            old.append(value)
            new.append(value)
        elif marker == "-":
            old.append(value)
        elif marker == "+":
            new_sources.append(None)
            new.append(value)
        else:
            raise ToolFailure(
                "PATCH_FAILED",
                f"Invalid patch line (must start with ' ', '-', or '+'): {raw}",
            )
    return ParsedHunk(old, new, new_sources, scope, eof_anchor)


# ── hunk location ─────────────────────────────────────────────


def _grade_key(grade: str):
    if grade == "trailing_ws":
        return lambda value: value.rstrip()
    if grade == "indent":
        return lambda value: value.strip()
    return lambda value: value


def find_subsequence_all(
    lines: list[str], needle: list[str], *, grade: str = "exact"
) -> list[int]:
    if not needle:
        return []
    key = _grade_key(grade)
    needle_keys = [key(item) for item in needle]
    matches: list[int] = []
    limit = len(lines) - len(needle) + 1
    for start in range(limit):
        ok = True
        for offset, expected in enumerate(needle_keys):
            if key(lines[start + offset]) != expected:
                ok = False
                break
        if ok:
            matches.append(start)
    return matches


def _filter_by_scope(
    lines: list[str],
    candidates: list[int],
    scope: str | None,
) -> tuple[list[int], bool]:
    if not scope or not candidates:
        return candidates, False
    wanted = scope.lower()
    kept = [
        start
        for start in candidates
        if wanted in "\n".join(lines[max(0, start - 40) : start]).lower()
    ]
    return (kept, True) if kept else (candidates, False)


def _filter_by_eof(
    lines: list[str], candidates: list[int], hunk: ParsedHunk
) -> list[int]:
    if not hunk.eof_anchor:
        return candidates
    end = len(lines) - len(hunk.old)
    return [start for start in candidates if start == end]


def _already_applied(lines: list[str], hunk: ParsedHunk) -> bool:
    """True when the hunk's *result* is already present in the file."""
    if hunk.old == hunk.new or not hunk.new:
        return False
    # A blank line is present in every newline-terminated file; it cannot prove
    # that a deletion whose result contains only blank context ever happened.
    if not any(line.strip() for line in hunk.new):
        return False
    anchored = any(source is not None for source in hunk.new_sources)
    if not anchored and len(hunk.new) < 2:
        return False
    for grade in ALREADY_APPLIED_GRADES:
        if find_subsequence_all(lines, hunk.new, grade=grade):
            return True
    return False


def _numbered_excerpt(lines: list[str], position: int, span: int) -> str:
    start = max(0, position - span)
    end = min(len(lines), position + span)
    rendered = "\n".join(
        f"{index + 1}: {lines[index]}" for index in range(start, end)
    )
    return rendered[:NEARBY_TEXT_MAX_BYTES]


def _not_found_failure(
    lines: list[str], hunk: ParsedHunk, index: int, path: str
) -> ToolFailure:
    needle = hunk.old or hunk.new
    position = 0
    if needle:
        positions = find_subsequence_all(lines, [needle[0]], grade="indent")
        position = positions[0] if positions else 0
    return ToolFailure(
        "PATCH_CONTEXT_NOT_FOUND",
        f"Patch context not found in {path} for hunk {index}.",
        details={
            "path": path,
            "hunk_index": index,
            "nearby_text": _numbered_excerpt(lines, position, NEARBY_CONTEXT_LINES),
        },
    )


def _ambiguous_failure(
    lines: list[str],
    candidates: list[int],
    hunk: ParsedHunk,
    index: int,
    path: str,
    grade: str,
) -> ToolFailure:
    shown = candidates[:MAX_REPORTED_CANDIDATES]
    return ToolFailure(
        "PATCH_CONTEXT_AMBIGUOUS",
        f"Patch context matched {len(candidates)} locations in {path} for hunk {index}.",
        details={
            "path": path,
            "hunk_index": index,
            "match_count": len(candidates),
            "scope": hunk.scope,
            "candidate_lines": shown,
            "candidates": [
                _numbered_excerpt(lines, start, 3) for start in shown
            ],
        },
    )


def _rebuild_new_lines(
    lines: list[str], start: int, hunk: ParsedHunk, grade: str
) -> list[str]:
    """Rebuild a hunk's result, reinstating context from the file on a downgrade.

    A whitespace-tolerant match must not rewrite the file's own whitespace with
    the hunk's spelling of it, so every context line comes from the file.
    """
    if grade == "exact":
        return list(hunk.new)
    rebuilt: list[str] = []
    for value, source in zip(hunk.new, hunk.new_sources):
        if source is None:
            rebuilt.append(value)
        else:
            rebuilt.append(lines[start + source])
    return rebuilt


def _locate_hunk(
    lines: list[str], hunk: ParsedHunk, index: int, path: str
) -> MatchedHunk | None:
    """Place one hunk, or None when its result is already in the file."""
    if not hunk.old:
        # A pure insertion: anchor at EOF when asked, else at the first match
        # of the new block (already applied) or the file end.
        if hunk.eof_anchor:
            return MatchedHunk(index, len(lines), len(lines), list(hunk.new), "exact")
        if _already_applied(lines, hunk):
            return None
        return MatchedHunk(index, len(lines), len(lines), list(hunk.new), "exact")

    for grade in MATCH_GRADES:
        candidates = find_subsequence_all(lines, hunk.old, grade=grade)
        if not candidates:
            continue
        candidates = _filter_by_eof(lines, candidates, hunk)
        if not candidates:
            continue
        candidates, scope_used = _filter_by_scope(lines, candidates, hunk.scope)
        if len(candidates) == 1:
            start = candidates[0]
            return MatchedHunk(
                index,
                start,
                start + len(hunk.old),
                _rebuild_new_lines(lines, start, hunk, grade),
                grade,
                scope_used,
                hunk.old,
            )
        raise _ambiguous_failure(lines, candidates, hunk, index, path, grade)

    if _already_applied(lines, hunk):
        return None
    raise _not_found_failure(lines, hunk, index, path)


def changed_ranges(matched: list[MatchedHunk]) -> list[dict[str, int]]:
    """Net 1-based inclusive ranges a set of hunks touched."""
    ranges: list[dict[str, int]] = []
    for item in sorted(matched, key=lambda value: value.start):
        start_line = item.start + 1
        end_line = item.start + len(item.new)
        added = sum(1 for line in item.new if line is not None)
        removed = len(item.old)
        ranges.append(
            {
                "start_line": start_line,
                "end_line": end_line,
                "added_lines": added,
                "removed_lines": removed,
            }
        )
    return ranges


def apply_update_hunks_detailed(
    content: str, hunks, path: str = "<patch>"
) -> UpdateOutcome:
    if not hunks:
        return UpdateOutcome(content, [], "exact", [], [], 0)
    bom, text = strip_bom(content)
    line_ending = detect_line_ending(text)
    normalized = normalize_to_lf(text)
    lines = normalized.split("\n")
    parsed = [parse_update_hunk(hunk) for hunk in hunks]

    matched: list[MatchedHunk] = []
    already_applied: list[int] = []
    warnings: list[str] = []
    for index, hunk in enumerate(parsed):
        placement = _locate_hunk(lines, hunk, index, path)
        if placement is None:
            already_applied.append(index)
            continue
        matched.append(placement)
        warning = MATCH_GRADE_WARNINGS.get(placement.quality)
        if warning:
            warnings.append(f"hunk {index}: {warning}")
        if placement.scope_used:
            warnings.append(f"hunk {index}: located using the @@ scope anchor")

    matched.sort(key=lambda item: item.start)
    for previous, current in zip(matched, matched[1:]):
        if previous.end > current.start:
            raise ToolFailure(
                "PATCH_HUNKS_OVERLAP",
                f"Patch hunks {previous.hunk_index} and {current.hunk_index} overlap in {path}.",
                details={
                    "path": path,
                    "hunk_indexes": [previous.hunk_index, current.hunk_index],
                    "retry_hint": "Merge the overlapping hunks into one hunk.",
                },
            )

    # Splicing back to front keeps every later placement's indices valid.
    application_order = sorted(matched, key=lambda item: item.start, reverse=True)
    updated_lines = list(lines)
    for matched_hunk in application_order:
        updated_lines = (
            updated_lines[: matched_hunk.start]
            + matched_hunk.new
            + updated_lines[matched_hunk.end :]
        )
    updated = "\n".join(updated_lines)
    quality = "exact"
    for item in matched:
        if MATCH_GRADES.index(item.quality) > MATCH_GRADES.index(quality):
            quality = item.quality
    if already_applied:
        warnings.append(
            "hunks already present in the file were skipped: "
            + ", ".join(str(index) for index in already_applied)
        )
    return UpdateOutcome(
        content=bom + restore_line_endings(updated, line_ending),
        changed_ranges=changed_ranges(list(reversed(application_order))),
        match_quality=quality,
        warnings=warnings,
        already_applied_hunks=already_applied,
        applied_hunks=len(matched),
    )


def apply_update_hunks(content: str, hunks, path: str = "<patch>") -> str:
    """Apply hunks and return the new file text."""
    return apply_update_hunks_detailed(content, hunks, path).content


# ── line-addressed edits (apply_changes) ─────────────────────


@dataclass(frozen=True)
class LineEdit:
    op: str
    start_line: int | None = None
    end_line: int | None = None
    line: int | None = None
    content: str | None = None


@dataclass(frozen=True)
class ChangeRequest:
    action: str
    path: str
    content: str | None = None
    edits: tuple[LineEdit, ...] = ()
    destination: str | None = None
    revision: str | None = None


CHANGE_ACTIONS = ("create", "write", "edit", "delete", "move", "copy")
EDIT_OPERATIONS = ("replace", "delete", "insert_after", "insert_before")
CONTENT_OPERATIONS = frozenset({"replace", "insert_after", "insert_before"})
MAX_CHANGES_PER_CALL = 100
MAX_EDITS_PER_CHANGE = 200


def _parse_edit(raw: Any, index: int) -> LineEdit:
    if not isinstance(raw, dict):
        raise ToolFailure(
            "INVALID_ARGUMENT", f"edits[{index}] must be an object", category="validation"
        )
    op = str(raw.get("op") or "").strip()
    if op not in EDIT_OPERATIONS:
        raise ToolFailure(
            "INVALID_ARGUMENT",
            f"edits[{index}].op must be one of {', '.join(EDIT_OPERATIONS)}",
            category="validation",
        )
    start_line = raw.get("start_line")
    end_line = raw.get("end_line")
    line = raw.get("line")
    content = raw.get("content")
    if op in CONTENT_OPERATIONS and not isinstance(content, str):
        raise ToolFailure(
            "INVALID_ARGUMENT",
            f"edits[{index}].content is required for op '{op}'",
            category="validation",
        )
    if op in {"replace", "delete"} and not isinstance(start_line, int):
        raise ToolFailure(
            "INVALID_ARGUMENT",
            f"edits[{index}].start_line is required for op '{op}'",
            category="validation",
        )
    if op in {"insert_after", "insert_before"} and not isinstance(line, int):
        raise ToolFailure(
            "INVALID_ARGUMENT",
            f"edits[{index}].line is required for op '{op}'",
            category="validation",
        )
    return LineEdit(
        op=op,
        start_line=start_line,
        end_line=end_line if isinstance(end_line, int) else start_line,
        line=line,
        content=content if isinstance(content, str) else "",
    )


def parse_changes(raw) -> list[ChangeRequest]:
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            raise ToolFailure(
                "INVALID_ARGUMENT", "changes is required", category="validation"
            )
        try:
            raw = json.loads(raw)
        except Exception as exc:
            raise ToolFailure(
                "INVALID_ARGUMENT",
                f"changes must be valid JSON: {exc}",
                category="validation",
            ) from exc
    if not isinstance(raw, list):
        raise ToolFailure(
            "INVALID_ARGUMENT", "changes must be an array", category="validation"
        )
    if not raw:
        raise ToolFailure(
            "PATCH_FAILED", "No files were modified.", category="validation"
        )
    if len(raw) > MAX_CHANGES_PER_CALL:
        raise ToolFailure(
            "INVALID_ARGUMENT",
            f"At most {MAX_CHANGES_PER_CALL} changes per call",
            category="validation",
        )
    return [_parse_change(entry, index) for index, entry in enumerate(raw)]


def _parse_change(entry, index: int) -> ChangeRequest:
    if not isinstance(entry, dict):
        raise ToolFailure(
            "INVALID_ARGUMENT", f"changes[{index}] must be an object", category="validation"
        )
    action = str(entry.get("action") or "").strip().lower()
    if action not in CHANGE_ACTIONS:
        raise ToolFailure(
            "INVALID_ARGUMENT",
            f"changes[{index}].action must be one of {', '.join(CHANGE_ACTIONS)}",
            category="validation",
        )
    path = str(entry.get("path") or "").strip()
    if not path:
        raise ToolFailure(
            "INVALID_ARGUMENT", f"changes[{index}].path is required", category="validation"
        )
    content = entry.get("content")
    content = content if isinstance(content, str) else None
    edits_raw = entry.get("edits") or []
    if action == "edit" and not edits_raw:
        raise ToolFailure(
            "INVALID_ARGUMENT",
            f"changes[{index}].edits is required for action 'edit'",
            category="validation",
        )
    if isinstance(edits_raw, str):
        try:
            edits_raw = json.loads(edits_raw)
        except Exception as exc:
            raise ToolFailure(
                "INVALID_ARGUMENT",
                f"changes[{index}].edits must be valid JSON: {exc}",
                category="validation",
            ) from exc
    if not isinstance(edits_raw, list):
        raise ToolFailure(
            "INVALID_ARGUMENT",
            f"changes[{index}].edits must be an array",
            category="validation",
        )
    if len(edits_raw) > MAX_EDITS_PER_CHANGE:
        raise ToolFailure(
            "INVALID_ARGUMENT",
            f"At most {MAX_EDITS_PER_CHANGE} edits per change",
            category="validation",
        )
    edits = tuple(_parse_edit(item, j) for j, item in enumerate(edits_raw))
    destination = entry.get("destination")
    destination = destination if isinstance(destination, str) else None
    revision = entry.get("revision")
    revision = revision if isinstance(revision, str) else None
    return ChangeRequest(action, path, content, edits, destination, revision)


def apply_line_edits(content: str, edits, path: str) -> str:
    """Apply line-addressed edits. Line numbers refer to the file as read."""
    bom, text = strip_bom(content)
    line_ending = detect_line_ending(text)
    lines = normalize_to_lf(text).split("\n")
    total_lines = len(lines) - 1  # the trailing element is the final newline

    spans: list[tuple[int, int, list[str]]] = []
    for edit in edits:
        if edit.op == "replace":
            start, end = edit.start_line, edit.end_line
            new_lines = edit.content.split("\n") if edit.content else []
        elif edit.op == "delete":
            start, end = edit.start_line, edit.end_line
            new_lines = []
        elif edit.op == "insert_after":
            start = edit.line + 1
            end = edit.line
            new_lines = edit.content.split("\n") if edit.content else []
        else:  # insert_before
            start = edit.line
            end = edit.line - 1
            new_lines = edit.content.split("\n") if edit.content else []

        if start < 1 or end > total_lines or start > end + 1:
            raise ToolFailure(
                "INVALID_ARGUMENT",
                f"Line range {start}-{end} is outside the file "
                f"({total_lines} lines) in {path}",
                category="validation",
                details={"path": path, "total_lines": total_lines},
            )
        spans.append((start, end, new_lines))

    spans.sort(key=lambda item: item[0])
    for previous, current in zip(spans, spans[1:]):
        if previous[1] >= current[0]:
            raise ToolFailure(
                "PATCH_HUNKS_OVERLAP",
                f"Edits overlap in {path}: ranges {previous[0]}-{previous[1]} "
                f"and {current[0]}-{current[1]}",
                details={"path": path},
            )

    for start, end, new_lines in sorted(spans, key=lambda item: item[0], reverse=True):
        lines = lines[:start] + new_lines + lines[end + 1 :]

    return bom + restore_line_endings("\n".join(lines), line_ending)


# ── atomic commit ─────────────────────────────────────────────


def atomic_write(path: Path, content: str, mode: int | None = None) -> None:
    """Write via a temp file in the same directory plus an atomic replace."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".coding-tools-patch-", dir=str(parent), text=True
    )
    tmp_path = Path(tmp_name)
    backup: Path | None = None
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            backup = path.with_name(
                path.name + ".coding-tools-backup-" + os.urandom(6).hex()
            )
            path.replace(backup)
        if mode is not None:
            os.chmod(tmp_path, mode)
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        if backup is not None and backup.exists() and not path.exists():
            backup.replace(path)
        raise
    finally:
        if backup is not None and backup.exists():
            try:
                backup.unlink()
            except OSError:
                pass


def atomic_delete(path: Path) -> None:
    backup = path.with_name(path.name + ".coding-tools-backup-" + os.urandom(6).hex())
    path.replace(backup)
    try:
        backup.unlink()
    except OSError:
        pass


def atomic_move(source: Path, destination: Path, mode: int | None = None) -> None:
    """Move preserving mode, without overwriting an existing destination."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".coding-tools-patch-", dir=str(destination.parent), text=True
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(source.read_text(encoding="utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if mode is None:
            mode = source.stat().st_mode
        os.chmod(tmp_path, mode)
        tmp_path.replace(destination)
        source.unlink()
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

