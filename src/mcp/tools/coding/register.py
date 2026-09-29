"""Registration for the coding tools (coding-tools-mcp style)."""

from typing import Callable

from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .service import (
    apply_changes,
    apply_patch,
    exec_command,
    git_blame,
    git_diff,
    git_log,
    git_show,
    git_status,
    list_dir,
    list_files,
    read_file,
    search_text,
    set_pending_document_provider,
)


def register_coding_tools(
    add_tool: Callable[[McpTool], None],
    pending_document_provider: Callable[[], tuple[str, str] | None] | None = None,
) -> None:
    """Register the coding tool set.

    pending_document_provider: when supplied, ``read_file`` reads a chat-attached
    document instead of a workspace path (the desktop app queues the file).
    """

    set_pending_document_provider(pending_document_provider)

    add_tool(
        McpTool(
            name="read_file",
            description=(
                "Read a UTF-8 text file as line ranges. Returns the content with a "
                "sha256 revision that apply_changes needs to modify the file. "
                "Binary files are rejected. When the desktop app has queued an "
                "attached document, this tool reads that file instead; in that "
                "case call it with no arguments (or any path) and answer the "
                "user's question about the document."
            ),
            properties=PropertyList(
                [
                    Property("path", PropertyType.STRING, default_value=""),
                    Property("start_line", PropertyType.INTEGER, default_value=1, min_value=1),
                    Property("end_line", PropertyType.INTEGER, default_value=0, min_value=0),
                    Property("max_lines", PropertyType.INTEGER, default_value=0, min_value=0),
                    Property("max_bytes", PropertyType.INTEGER, default_value=262144, min_value=1),
                ]
            ),
            callback=read_file,
        )
    )

    add_tool(
        McpTool(
            name="list_dir",
            description="List the entries of a directory, optionally recursively.",
            properties=PropertyList(
                [
                    Property("path", PropertyType.STRING, default_value="."),
                    Property("recursive", PropertyType.BOOLEAN, default_value=False),
                    Property("max_depth", PropertyType.INTEGER, default_value=8, min_value=1),
                    Property("max_entries", PropertyType.INTEGER, default_value=200, min_value=1),
                    Property("include_hidden", PropertyType.BOOLEAN, default_value=False),
                ]
            ),
            callback=list_dir,
        )
    )

    add_tool(
        McpTool(
            name="list_files",
            description=(
                "Enumerate files under a directory filtered by glob patterns, "
                "for example '*.py'."
            ),
            properties=PropertyList(
                [
                    Property("path", PropertyType.STRING, default_value="."),
                    Property("glob", PropertyType.STRING, default_value="*"),
                    Property("exclude_patterns", PropertyType.STRING, default_value=""),
                    Property("include_hidden", PropertyType.BOOLEAN, default_value=False),
                    Property("max_results", PropertyType.INTEGER, default_value=200, min_value=1),
                ]
            ),
            callback=list_files,
        )
    )

    add_tool(
        McpTool(
            name="search_text",
            description=(
                "Search for text or a regular expression in the files under a "
                "directory and return matching lines with file and line number."
            ),
            properties=PropertyList(
                [
                    Property("query", PropertyType.STRING),
                    Property("path", PropertyType.STRING, default_value="."),
                    Property("regex", PropertyType.BOOLEAN, default_value=False),
                    Property("case_sensitive", PropertyType.BOOLEAN, default_value=False),
                    Property("glob", PropertyType.STRING, default_value=""),
                    Property("context_lines", PropertyType.INTEGER, default_value=0, min_value=0),
                    Property("max_results", PropertyType.INTEGER, default_value=100, min_value=1),
                    Property("max_preview_bytes", PropertyType.INTEGER, default_value=512, min_value=1),
                ]
            ),
            callback=search_text,
        )
    )

    add_tool(
        McpTool(
            name="apply_patch",
            description=(
                "Apply a V4A patch to create, update, delete or move files. "
                "The patch text must be wrapped in '*** Begin Patch' and "
                "'*** End Patch'. Use '*** Add File: <path>' with '+' lines, "
                "'*** Update File: <path>' with ' '/'-'/'+' hunks, "
                "'*** Delete File: <path>' and '*** Move to: <path>'. "
                "Set dry_run to validate without writing."
            ),
            properties=PropertyList(
                [
                    Property("patch", PropertyType.STRING),
                    Property("dry_run", PropertyType.BOOLEAN, default_value=False),
                ]
            ),
            callback=apply_patch,
        )
    )

    add_tool(
        McpTool(
            name="apply_changes",
            description=(
                "Apply line-addressed file changes. 'changes' is a JSON array; each "
                "entry has an action (create, write, edit, delete, move, copy), a "
                "path, and for every action except create the sha256 revision that "
                "read_file reported. 'edit' takes an array of edits: "
                '{"op":"replace","start_line":n,"end_line":m,"content":"..."}, '
                '{"op":"delete","start_line":n,"end_line":m}, '
                '{"op":"insert_after","line":n,"content":"..."} or '
                '{"op":"insert_before","line":n,"content":"..."}. '
                "Line numbers refer to the file as read_file reported them."
            ),
            properties=PropertyList(
                [
                    Property("changes", PropertyType.STRING),
                    Property("dry_run", PropertyType.BOOLEAN, default_value=False),
                ]
            ),
            callback=apply_changes,
        )
    )

    add_tool(
        McpTool(
            name="exec_command",
            description=(
                "Run a shell command in the workspace and return stdout, stderr and "
                "the exit code. Destructive commands (format, rm -rf /, mkfs, "
                "registry edits, user creation, shutdown) are refused."
            ),
            properties=PropertyList(
                [
                    Property("cmd", PropertyType.STRING),
                    Property("workdir", PropertyType.STRING, default_value=""),
                    Property("timeout_ms", PropertyType.INTEGER, default_value=30000, min_value=1, max_value=120000),
                    Property("max_output_bytes", PropertyType.INTEGER, default_value=65536, min_value=1),
                    Property("stdin", PropertyType.STRING, default_value=""),
                ]
            ),
            callback=exec_command,
        )
    )

    add_tool(
        McpTool(
            name="git_status",
            description="Show the working tree status of a git repository.",
            properties=PropertyList(
                [
                    Property("path", PropertyType.STRING, default_value="."),
                    Property("include_untracked", PropertyType.BOOLEAN, default_value=True),
                    Property("max_entries", PropertyType.INTEGER, default_value=200, min_value=1),
                ]
            ),
            callback=git_status,
        )
    )

    add_tool(
        McpTool(
            name="git_diff",
            description=(
                "Show the diff of a git repository: staged changes, unstaged changes "
                "and new untracked files."
            ),
            properties=PropertyList(
                [
                    Property("path", PropertyType.STRING, default_value="."),
                    Property("staged", PropertyType.BOOLEAN, default_value=False),
                    Property("unstaged", PropertyType.BOOLEAN, default_value=True),
                    Property("include_untracked", PropertyType.BOOLEAN, default_value=True),
                    Property("context_lines", PropertyType.INTEGER, default_value=3, min_value=0),
                    Property("max_bytes", PropertyType.INTEGER, default_value=65536, min_value=1),
                ]
            ),
            callback=git_diff,
        )
    )

    add_tool(
        McpTool(
            name="git_log",
            description="Show the commit history of a git repository.",
            properties=PropertyList(
                [
                    Property("path", PropertyType.STRING, default_value="."),
                    Property("ref", PropertyType.STRING, default_value=""),
                    Property("max_count", PropertyType.INTEGER, default_value=20, min_value=1),
                    Property("skip", PropertyType.INTEGER, default_value=0, min_value=0),
                ]
            ),
            callback=git_log,
        )
    )

    add_tool(
        McpTool(
            name="git_show",
            description="Show the contents of a git commit (or just its stat).",
            properties=PropertyList(
                [
                    Property("rev", PropertyType.STRING),
                    Property("path", PropertyType.STRING, default_value="."),
                    Property("include_diff", PropertyType.BOOLEAN, default_value=True),
                    Property("context_lines", PropertyType.INTEGER, default_value=3, min_value=0),
                    Property("max_bytes", PropertyType.INTEGER, default_value=65536, min_value=1),
                ]
            ),
            callback=git_show,
        )
    )

    add_tool(
        McpTool(
            name="git_blame",
            description="Show per-line authorship of a file in a git repository.",
            properties=PropertyList(
                [
                    Property("path", PropertyType.STRING),
                    Property("rev", PropertyType.STRING, default_value=""),
                    Property("start_line", PropertyType.INTEGER, default_value=1, min_value=1),
                    Property("end_line", PropertyType.INTEGER, default_value=0, min_value=0),
                    Property("max_lines", PropertyType.INTEGER, default_value=200, min_value=1),
                ]
            ),
            callback=git_blame,
        )
    )
