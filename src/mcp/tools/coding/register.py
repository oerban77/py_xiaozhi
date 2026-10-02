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
)


def register_coding_tools(add_tool: Callable[[McpTool], None]) -> None:
    """Register the coding tool set."""

    async def select_workspace(args: dict) -> str:
        from src.utils.workspace import set_workspace

        ok, result = set_workspace(str((args or {}).get("path", "")))
        if not ok:
            return f"Could not set coding workspace: {result}"
        return (
            f"Active coding workspace: {result}. Use workspace-relative paths "
            "for subsequent coding tools."
        )

    add_tool(
        McpTool(
            name="set_workspace",
            description=(
                "Select an existing project directory as the active coding workspace "
                "when the user explicitly asks to analyze or use a different project "
                "path. This selection persists. After selecting it, use relative paths "
                "for read_file/search_text/list_dir/list_files; do not repeatedly pass "
                "absolute paths to those tools."
            ),
            properties=PropertyList([Property("path", PropertyType.STRING)]),
            callback=select_workspace,
        )
    )

    add_tool(
        McpTool(
            name="read_file",
            description=(
                "Read a UTF-8 text file as line ranges. Returns the content with a "
                "sha256 revision that apply_changes needs to modify the file. "
                "Binary files are rejected. Paths are relative to the active coding "
                "workspace; if the user named another project directory, call "
                "set_workspace once before reading it. Firmware artifacts with .bin "
                "or .hex extensions are intentionally skipped; do not retry reading "
                "them, analyze source/config files instead. If the returned banner "
                "shows a line/byte limit, the result is partial: request the next "
                "range before describing the whole file. Base code claims only on "
                "the returned source text, not guessed surrounding code."
            ),
            properties=PropertyList(
                [
                    Property("path", PropertyType.STRING),
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
            description=(
                "List the entries of a directory, optionally recursively. Paths are "
                "relative to the active coding workspace; call set_workspace first "
                "when the user explicitly names a different project directory. .bin "
                "and .hex firmware artifacts are omitted from listings."
            ),
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
                "for example '*.py'. Paths are relative to the active coding "
                "workspace; call set_workspace first when the user explicitly names "
                "a different project directory. .bin and .hex firmware artifacts "
                "are omitted from results."
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
                "directory and return matching lines with file and line number. Paths "
                "are relative to the active coding workspace; call set_workspace once "
                "for a user-requested different project, then use relative paths. "
                "Search skips .bin and .hex firmware artifacts, scans at most 100 "
                "files and 16 MiB per call, and skips files larger than 1 MiB. "
                "Continue a bounded search with the returned file_offset. A no-match "
                "result applies only to the files scanned in that batch; do not say "
                "the workspace has no matches until all batches are searched."
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
                    Property("max_files", PropertyType.INTEGER, default_value=100, min_value=1, max_value=100),
                    Property("file_offset", PropertyType.INTEGER, default_value=0, min_value=0),
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
                "Before patching, read the exact current file range and use its "
                "actual text as context; do not invent nearby code. If a hunk fails, "
                "reread the file before trying a revised patch. "
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
                "read_file reported for that same current file. Never reuse a "
                "revision after the file changes. 'edit' takes an array of edits: "
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
                "registry edits, user creation, shutdown) are refused. Short commands "
                "run synchronously within the MCP timeout budget. For tests/builds "
                "that may take longer, pass background=true; poll with job_id and "
                "output_offset until status=completed or status=cancelled. Pass cancel=true with job_id "
                "to stop a running job. Do not report a test/build as successful "
                "while status is running; after completion, require exit_code=0 "
                "before claiming it passed. Do not use per-page pypdf/PyMuPDF "
                "commands to read PDFs; use manage_document with entry_number or "
                "page ranges to avoid a long sequence of tool calls."
            ),
            properties=PropertyList(
                [
                    Property("cmd", PropertyType.STRING, default_value=""),
                    Property("workdir", PropertyType.STRING, default_value=""),
                    Property("timeout_ms", PropertyType.INTEGER, default_value=30000, min_value=1, max_value=120000),
                    Property("max_output_bytes", PropertyType.INTEGER, default_value=65536, min_value=1),
                    Property("stdin", PropertyType.STRING, default_value=""),
                    Property("background", PropertyType.BOOLEAN, default_value=False),
                    Property("job_id", PropertyType.STRING, default_value=""),
                    Property("output_offset", PropertyType.INTEGER, default_value=0, min_value=0),
                    Property("cancel", PropertyType.BOOLEAN, default_value=False),
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
