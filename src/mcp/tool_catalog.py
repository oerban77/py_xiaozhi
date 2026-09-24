"""MCP tool catalog: grouping and display names for the settings UI.

Contract (to be followed during development)
-----------------
1. **Built-in tools** live in ``src/mcp/tools/<pkg>/register.py`` and are registered
   with ``McpTool("name", ...)`` or ``McpTool(name="name", ...)``; the catalog is
   built by scanning these files — **no hardcoded lists or titles**.
2. **Group id / group title** = the package directory name ``<pkg>`` (e.g. ``music``,
   ``weather``, ``camera``), matching ``tools/<pkg>`` on disk; the settings page
   shows the package name directly.
3. **Tool display name** = the last segment of the name
   (``music_player.pause`` → ``pause``).
4. **External plugins**: manifest.tools / source ``name=`` heuristics; the group
   title is the plugin id (or the manifest ``name`` field if present — the plugin
   describes itself, the host does not hardcode it).
5. **Disable** configuration: ``MCP_TOOLS.DISABLED`` (a list of full tool names).

Names registered at runtime but not found by the scan are merged into the catalog
(source=runtime).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Regexes: scanning register source
# ---------------------------------------------------------------------------

# McpTool("a.b", ...) or McpTool('a.b',
_MCP_TOOL_POS_NAME = re.compile(
    r"""McpTool\s*\(\s*['"]([a-zA-Z][a-zA-Z0-9_.]*)['"]"""
)
# McpTool(name="a.b"
_MCP_TOOL_KW_NAME = re.compile(
    r"""McpTool\s*\([^)]*?\bname\s*=\s*['"]([a-zA-Z][a-zA-Z0-9_.]*)['"]""",
    re.DOTALL,
)
# external / generic name="..."
_NAME_KWARG_RE = re.compile(
    r"""\bname\s*=\s*['"]([a-zA-Z0-9_.-]+)['"]"""
)
def _tools_package_dir() -> Path:
    return Path(__file__).resolve().parent / "tools"


def tool_group(name: str, *, fallback_pkg: str | None = None) -> str:
    """Group id: prefer the package name passed by the caller; otherwise use a name-prefix heuristic."""
    if fallback_pkg:
        return fallback_pkg
    if "." in name:
        parts = name.split(".")
        if name.startswith("self.") and len(parts) >= 2:
            return ".".join(parts[:2])  # self.application
        return parts[0]
    return "other"


def tool_label(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def group_label(group_id: str) -> str:
    return group_id


def _extract_mcp_tool_names(text: str) -> list[str]:
    """Return a list of tool names (deduplicated, order preserved)."""
    found: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        found.append(name)

    for m in _MCP_TOOL_POS_NAME.finditer(text):
        add(m.group(1))
    for m in _MCP_TOOL_KW_NAME.finditer(text):
        add(m.group(1))
    return found


def _scan_register_file(path: Path, pkg: str) -> list[dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []

    rows: list[dict[str, str]] = []
    for name in _extract_mcp_tool_names(text):
        rows.append(
            {
                "name": name,
                "group": pkg,
                "groupLabel": pkg,  # Do not hardcode Chinese; group title = directory name
                "label": tool_label(name),
                "source": "builtin",
            }
        )
    return rows


@lru_cache(maxsize=1)
def discover_builtin_catalog_rows() -> tuple[dict[str, str], ...]:
    """Scan ``src/mcp/tools/*/register.py``; the result is cached (in-process)."""
    root = _tools_package_dir()
    if not root.is_dir():
        return tuple()

    rows: list[dict[str, str]] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        reg = child / "register.py"
        if not reg.is_file():
            continue
        rows.extend(_scan_register_file(reg, child.name))
    # tuple of frozendict-like: tuple[dict] is used because dict is mutable; the cache returns a tuple copy
    return tuple(rows)


def clear_builtin_catalog_cache() -> None:
    discover_builtin_catalog_rows.cache_clear()


def builtin_catalog_rows() -> list[dict[str, str]]:
    """Built-in tool rows for the settings page (scans the tools package; not a hardcoded list)."""
    return [dict(r) for r in discover_builtin_catalog_rows()]


def normalize_disabled(names: Any) -> list[str]:
    if not names:
        return []
    if isinstance(names, str):
        names = [names]
    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        s = str(n).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def is_tool_enabled(name: str, disabled: list[str] | set[str] | None) -> bool:
    if not disabled:
        return True
    return name not in set(disabled)


# Backwards compatibility: dynamic group label table (external plugins write into it)
GROUP_LABELS: dict[str, str] = {}


def _plugins_dir_from_config() -> Path | None:
    try:
        from src.utils.config_manager import get_config
        from src.utils.resource_finder import get_user_data_dir

        raw = get_config().get_config("MCP_PLUGINS.DIR", None)
        if raw and str(raw).strip():
            return Path(str(raw).strip()).expanduser()
        return get_user_data_dir() / "mcp_plugins"
    except Exception:
        try:
            from src.utils.resource_finder import get_user_data_dir

            return get_user_data_dir() / "mcp_plugins"
        except Exception:
            return None


def _tools_from_manifest(
    manifest: dict[str, Any], plugin_id: str
) -> list[dict[str, str]]:
    raw = manifest.get("tools")
    if not raw or not isinstance(raw, list):
        return []
    display = str(manifest.get("name") or plugin_id)
    rows: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            label = tool_label(name)
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            label = str(
                item.get("title") or item.get("label") or tool_label(name)
            )
        else:
            continue
        if not name:
            continue
        rows.append(
            {
                "name": name,
                "group": plugin_id,
                "groupLabel": display,
                "label": label,
                "source": "plugin",
            }
        )
    return rows


def _tools_from_plugin_sources(
    path: Path, plugin_id: str, display: str
) -> list[dict[str, str]]:
    names: list[str] = []
    seen: set[str] = set()
    py_files: list[Path] = []
    if path.is_file() and path.suffix == ".py":
        py_files = [path]
    elif path.is_dir():
        for p in path.rglob("*.py"):
            if p.name.startswith("_"):
                continue
            if "lib" in p.parts or "native" in p.parts:
                continue
            py_files.append(p)
    for pf in py_files[:20]:
        try:
            text = pf.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        # Prefer McpTool(...) form
        for name in _extract_mcp_tool_names(text):
            if name not in seen:
                seen.add(name)
                names.append(name)
        if not names:
            for m in _NAME_KWARG_RE.finditer(text):
                n = m.group(1)
                if "." not in n and len(n) < 4:
                    continue
                if n not in seen:
                    seen.add(n)
                    names.append(n)
    return [
        {
            "name": n,
            "group": plugin_id,
            "groupLabel": display,
            "label": tool_label(n),
            "source": "plugin",
        }
        for n in names
    ]


def discover_plugin_catalog_rows() -> list[dict[str, str]]:
    root = _plugins_dir_from_config()
    if root is None or not root.is_dir():
        return []

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except Exception:
        return []

    for child in children:
        if child.name.startswith(".") or child.name.startswith("_"):
            continue
        plugin_id = child.stem if child.is_file() else child.name
        display = plugin_id
        manifest: dict[str, Any] = {}

        if child.is_dir():
            mf = child / "manifest.json"
            if mf.is_file():
                try:
                    manifest = json.loads(mf.read_text(encoding="utf-8"))
                    plugin_id = str(manifest.get("id") or plugin_id)
                    display = str(manifest.get("name") or plugin_id)
                except Exception:
                    manifest = {}
            GROUP_LABELS[plugin_id] = display
            found = _tools_from_manifest(manifest, plugin_id)
            if not found:
                found = _tools_from_plugin_sources(child, plugin_id, display)
            for r in found:
                if r["name"] in seen:
                    continue
                seen.add(r["name"])
                rows.append(r)
        elif child.suffix == ".py" and child.is_file():
            GROUP_LABELS[plugin_id] = plugin_id
            for r in _tools_from_plugin_sources(child, plugin_id, plugin_id):
                if r["name"] in seen:
                    continue
                seen.add(r["name"])
                rows.append(r)
    return rows


def full_catalog_rows(
    *,
    runtime_names: list[str] | None = None,
    extra_disabled: list[str] | None = None,
) -> list[dict[str, str]]:
    """Built-in (scanning tools/) + on-disk plugins + runtime + leftover disabled entries."""
    rows = builtin_catalog_rows()
    seen = {r["name"] for r in rows}
    pkg_labels = {r["group"]: r["groupLabel"] for r in rows}

    for r in discover_plugin_catalog_rows():
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        rows.append(r)

    def _append_unknown(name: str, source: str) -> None:
        if name in seen:
            return
        g, label = _runtime_group_for(name, pkg_labels)
        rows.append(
            {
                "name": name,
                "group": g,
                "groupLabel": label,
                "label": tool_label(name),
                "source": source,
            }
        )
        seen.add(name)

    for name in runtime_names or []:
        _append_unknown(name, "runtime")
    for name in normalize_disabled(extra_disabled):
        _append_unknown(name, "external")

    return rows


def _runtime_group_for(
    name: str, pkg_labels: dict[str, str]
) -> tuple[str, str]:
    """Runtime/leftover tools: assign them to a scanned built-in package group when possible."""
    builtins = discover_builtin_catalog_rows()
    for row in builtins:
        if name == row["name"]:
            return row["group"], row["groupLabel"]

    for row in builtins:
        bn = row["name"]
        if "." not in name or "." not in bn:
            continue
        np, bp = name.split("."), bn.split(".")
        if name.startswith("self.") and bn.startswith("self."):
            if len(np) >= 2 and len(bp) >= 2 and np[:2] == bp[:2]:
                return row["group"], row["groupLabel"]
        elif not name.startswith("self.") and np[0] == bp[0]:
            return row["group"], row["groupLabel"]

    g = tool_group(name)
    return g, pkg_labels.get(g, GROUP_LABELS.get(g, g))
