"""Active workspace selection for coding tools and UI controls."""

from __future__ import annotations

from pathlib import Path

from src.utils.config_manager import get_config


def get_workspace_root() -> Path:
    """Return the configured workspace, falling back to the process directory."""
    configured = str(get_config().get_config("CODING.WORKSPACE", "") or "").strip()
    if configured:
        try:
            path = Path(configured).expanduser().resolve(strict=True)
            if path.is_dir():
                return path
        except (OSError, RuntimeError):
            pass
    return Path.cwd().resolve()


def normalize_workspace(path: str) -> str:
    """Validate and normalize a workspace path; empty resets to the process directory."""
    raw = (path or "").strip().strip('"')
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        active = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Workspace does not exist: {raw}") from exc
    if not active.is_dir():
        raise ValueError(f"Workspace is not a directory: {active}")
    return str(active)


def set_workspace(path: str) -> tuple[bool, str]:
    """Persist an existing directory as the coding workspace; empty resets to cwd."""
    try:
        normalized = normalize_workspace(path)
    except ValueError as exc:
        return False, str(exc)
    if not get_config().update_configs({"CODING.WORKSPACE": normalized}):
        return False, "Failed to save workspace"
    return True, str(Path(normalized).resolve() if normalized else Path.cwd().resolve())