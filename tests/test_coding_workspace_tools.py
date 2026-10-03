import json
import sys
import time
from types import SimpleNamespace

import pytest

from src.mcp.tools.coding import service as coding_service
from src.mcp.tools.coding.patching import ToolFailure
from src.mcp.tools.coding.register import register_coding_tools
from src.mcp.tools.coding.service import _resolve


def test_absolute_path_inside_workspace_is_allowed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "main.py"
    source.write_text("print('ok')", encoding="utf-8")

    assert _resolve(str(source), workspace, must_exist=True) == source.resolve()


def test_absolute_path_outside_workspace_gives_switch_workspace_guidance(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "other-project"
    workspace.mkdir()
    outside.mkdir()

    with pytest.raises(ToolFailure) as exc_info:
        _resolve(str(outside), workspace, must_exist=True)

    assert exc_info.value.code == "WORKSPACE_MISMATCH"
    assert "call set_workspace once" in exc_info.value.message


@pytest.mark.asyncio
async def test_set_workspace_tool_selects_user_requested_project(monkeypatch, tmp_path):
    from src.utils import workspace

    selected = tmp_path / "project"
    selected.mkdir()
    calls = []
    monkeypatch.setattr(
        workspace,
        "set_workspace",
        lambda path: (calls.append(path) is None, str(selected.resolve())),
    )
    tools = []
    register_coding_tools(tools.append)
    tool = next(item for item in tools if item.name == "set_workspace")

    result = await tool.callback({"path": str(selected)})

    assert calls == [str(selected)]
    assert "Active coding workspace" in result
    assert str(selected.resolve()) in result


def test_coding_tools_advertise_resumable_search_and_command_jobs():
    tools = []
    register_coding_tools(tools.append)
    by_name = {tool.name: tool for tool in tools}

    search_properties = {
        prop.name: prop for prop in by_name["search_text"].properties.properties
    }
    command_properties = {
        prop.name: prop for prop in by_name["exec_command"].properties.properties
    }

    assert search_properties["file_offset"].default_value == 0
    assert search_properties["max_files"].max_value == coding_service.MAX_SEARCH_FILES
    assert command_properties["background"].default_value is False
    assert command_properties["job_id"].default_value == ""
    assert command_properties["output_offset"].default_value == 0
    assert "result is partial" in by_name["read_file"].description
    assert "until all batches are searched" in by_name["search_text"].description
    assert "exit_code=0" in by_name["exec_command"].description


def test_large_repo_noise_dirs_are_skipped_during_analysis(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "real.py").write_text("needle = 'found'\n", encoding="utf-8")
    (workspace / "docs").mkdir()
    (workspace / "docs" / "guide.py").write_text("needle = 'docs-only'\n", encoding="utf-8")
    (workspace / ".venv").mkdir()
    (workspace / ".venv" / "ignored.py").write_text("needle = 'too noisy'\n", encoding="utf-8")
    (workspace / "build").mkdir()
    (workspace / "build" / "artifact.py").write_text("needle = 'build-only'\n", encoding="utf-8")
    monkeypatch.setattr(coding_service, "_workspace_root", lambda: workspace)

    recursive_listing = coding_service._list_dir({"path": ".", "recursive": True}).replace("\\", "/")
    assert "src/real.py" in recursive_listing
    assert ".venv" not in recursive_listing
    assert "build" not in recursive_listing

    search_result = coding_service._search_text({"path": ".", "query": "needle", "glob": "*.py"}).replace("\\", "/")
    assert "src/real.py:1" in search_result
    assert "docs/guide.py:1" in search_result
    assert search_result.index("src/real.py:1") < search_result.index("docs/guide.py:1")
    assert ".venv" not in search_result
    assert "build" not in search_result


def test_firmware_artifacts_are_skipped_by_coding_analysis_tools(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.c").write_text("SOURCE_MARKER", encoding="utf-8")
    (workspace / "firmware.hex").write_text("HEX_ONLY_MARKER", encoding="utf-8")
    (workspace / "firmware.BIN").write_bytes(b"\\x00\\x01")
    monkeypatch.setattr(coding_service, "_workspace_root", lambda: workspace)

    assert "firmware.hex" not in coding_service._list_dir(
        {"path": ".", "recursive": True}
    )
    listed_files = coding_service._list_files({"path": "."})
    assert "firmware.hex" not in listed_files
    assert "firmware.BIN" not in listed_files
    search_result = coding_service._search_text(
        {"path": ".", "query": "HEX_ONLY_MARKER"}
    )
    assert "no matches" in search_result

    with pytest.raises(ToolFailure) as exc_info:
        coding_service._read_file({"path": "firmware.hex"})
    assert exc_info.value.code == "IGNORED_FILE_TYPE"


def test_list_files_prioritizes_source_dirs_before_docs_and_noise(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "module.py").write_text("print('source')\n", encoding="utf-8")
    (workspace / "docs").mkdir()
    (workspace / "docs" / "guide.py").write_text("print('docs')\n", encoding="utf-8")
    (workspace / "build").mkdir()
    (workspace / "build" / "artifact.py").write_text("print('noisy')\n", encoding="utf-8")
    monkeypatch.setattr(coding_service, "_workspace_root", lambda: workspace)

    listing = coding_service._list_files({"path": ".", "max_results": 10})
    first, second = listing.splitlines()[:2]

    assert first == "src/module.py"
    assert second == "docs/guide.py"
    assert "build/artifact.py" not in listing


def test_reason_about_project_rank_relevant_files_by_task(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "auth.py").write_text("def login(user):\n    return user\n", encoding="utf-8")
    (workspace / "src" / "config.py").write_text("AUTH_URL = 'https://example.test'\n", encoding="utf-8")
    (workspace / "docs").mkdir()
    (workspace / "docs" / "guide.md").write_text("Authentication docs\n", encoding="utf-8")
    (workspace / "build").mkdir()
    (workspace / "build" / "artifact.py").write_text("AUTH_URL = 'not used'\n", encoding="utf-8")
    monkeypatch.setattr(coding_service, "_workspace_root", lambda: workspace)

    result = coding_service._reason_about_project({"question": "fix login auth bug", "max_files": 3})

    assert "Likely relevant files" in result
    assert "Root cause hypothesis" in result
    assert "Likely fix" in result
    assert "Validation" in result
    assert "Confidence" in result
    assert "Suggested validation command" in result
    assert "Patch plan" in result
    assert "src/auth.py" in result
    assert "src/config.py" in result
    assert "docs/guide.md" not in result.splitlines()[0]
    assert "build/artifact.py" not in result


def test_reason_about_project_keeps_scan_bounded_for_large_repos(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "auth.py").write_text("def login(user):\n    return user\n", encoding="utf-8")
    for idx in range(60):
        noisy = workspace / "noise" / f"file_{idx}.py"
        noisy.parent.mkdir(exist_ok=True)
        noisy.write_text(f"unused_{idx} = {idx}\n", encoding="utf-8")
    seen = []

    def fake_read_text(path):
        seen.append(str(path.relative_to(workspace)))
        return path.read_text(encoding="utf-8", errors="replace")

    monkeypatch.setattr(coding_service, "_workspace_root", lambda: workspace)
    monkeypatch.setattr(coding_service, "_read_text", fake_read_text)

    result = coding_service._reason_about_project({"question": "fix login auth bug", "max_files": 3})

    assert "src/auth.py" in result
    assert len(seen) <= 12


def test_mcp_server_keeps_a_margin_before_session_disconnect(monkeypatch):
    from src.mcp.mcp_server import McpServer
    from src.utils import config_manager

    class ConfigStub:
        def get_config(self, key, default=None):
            return 45 if key == "MCP_TOOLS.CALL_TIMEOUT" else default

    monkeypatch.setattr(config_manager, "get_config", lambda: ConfigStub())

    assert McpServer()._call_timeout() == 30.0


def test_read_file_streams_requested_range_and_keeps_revision(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "large.py"
    text = "first\r\nsecond\r\nthird\n"
    source.write_bytes(text.encode("utf-8"))
    monkeypatch.setattr(coding_service, "_workspace_root", lambda: workspace)

    result = coding_service._read_file(
        {"path": "large.py", "start_line": 2, "end_line": 3, "max_lines": 1}
    )

    assert "Showing lines 2-2 of 3" in result
    assert "second\r" in result
    assert coding_service.content_revision(text) in result
    assert "third" not in result


@pytest.mark.parametrize(
    "arguments",
    [
        {"start_line": 0},
        {"end_line": -1},
        {"max_lines": -1},
        {"max_bytes": 0},
    ],
)
def test_read_file_rejects_invalid_ranges(monkeypatch, tmp_path, arguments):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.py").write_text("line\n", encoding="utf-8")
    monkeypatch.setattr(coding_service, "_workspace_root", lambda: workspace)

    with pytest.raises(ToolFailure) as exc_info:
        coding_service._read_file({"path": "source.py", **arguments})

    assert exc_info.value.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    "arguments",
    [
        {"max_results": 0},
        {"max_preview_bytes": 0},
        {"max_files": 0},
        {"file_offset": -1},
    ],
)
def test_search_text_rejects_invalid_limits(monkeypatch, tmp_path, arguments):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(coding_service, "_workspace_root", lambda: workspace)

    with pytest.raises(ToolFailure) as exc_info:
        coding_service._search_text({"path": ".", "query": "needle", **arguments})

    assert exc_info.value.code == "INVALID_ARGUMENT"


def test_search_text_returns_cursor_for_next_file_batch(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name, content in (
        ("a.py", "no match"),
        ("b.py", "still no match"),
        ("c.py", "NEEDLE here"),
    ):
        (workspace / name).write_text(content, encoding="utf-8")
    monkeypatch.setattr(coding_service, "_workspace_root", lambda: workspace)

    first = coding_service._search_text(
        {"path": ".", "query": "NEEDLE", "glob": "*.py", "max_files": 2}
    )
    second = coding_service._search_text(
        {
            "path": ".",
            "query": "NEEDLE",
            "glob": "*.py",
            "max_files": 2,
            "file_offset": 2,
        }
    )

    assert "no matches" in first
    assert "file_offset=2" in first
    assert "c.py:1" in second


def test_background_command_can_be_polled_in_output_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(coding_service, "_workspace_root", lambda: tmp_path)
    command = (
        f'"{sys.executable}" -c "import time; '
        "print('first', flush=True); time.sleep(0.1); "
        "print('second', flush=True)\""
    )

    started = json.loads(
        coding_service._exec_command({"cmd": command, "background": True})
    )
    assert started["status"] == "running"
    output = ""
    offset = 0
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        polled = json.loads(
            coding_service._exec_command(
                {
                    "job_id": started["job_id"],
                    "output_offset": offset,
                    "max_output_bytes": 5,
                }
            )
        )
        output += polled["output"]
        offset = polled["output_offset"]
        if polled["status"] == "completed" and offset >= polled["total_output_bytes"]:
            break
        time.sleep(0.01)

    assert "first" in output
    assert "second" in output
    assert polled["status"] == "completed"
    assert polled["exit_code"] == 0


def test_background_command_can_be_cancelled(monkeypatch, tmp_path):
    monkeypatch.setattr(coding_service, "_workspace_root", lambda: tmp_path)
    command = f'"{sys.executable}" -c "import time; time.sleep(60)"'
    started = json.loads(
        coding_service._exec_command({"cmd": command, "background": True})
    )

    result = json.loads(
        coding_service._exec_command(
            {"job_id": started["job_id"], "cancel": True}
        )
    )
    deadline = time.monotonic() + 5
    while result["status"] != "cancelled" and time.monotonic() < deadline:
        time.sleep(0.02)
        result = json.loads(
            coding_service._exec_command(
                {"job_id": started["job_id"], "output_offset": result["output_offset"]}
            )
        )

    assert result["status"] == "cancelled"


def test_sync_command_timeout_leaves_mcp_response_slack(monkeypatch, tmp_path):
    from src.utils import config_manager

    class ConfigStub:
        def get_config(self, key, default=None):
            return 45 if key == "MCP_TOOLS.CALL_TIMEOUT" else default

    captured = {}

    def fake_run(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(config_manager, "get_config", lambda: ConfigStub())
    monkeypatch.setattr(coding_service.subprocess, "run", fake_run)
    monkeypatch.setattr(coding_service, "_workspace_root", lambda: tmp_path)

    result = coding_service._exec_command({"cmd": "echo ok", "timeout_ms": 120000})

    assert "status=exited" in result
    assert captured["timeout"] == 40


def test_exec_command_blocks_repeated_pdf_page_extraction(monkeypatch, tmp_path):
    monkeypatch.setattr(coding_service, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(
        coding_service.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("PDF dump command must not execute"),
    )
    command = (
        "python -c \"import pypdf; pdf = open('book.pdf', 'rb'); "
        "reader = pypdf.PdfReader(pdf); page = reader.pages[12]; "
        "text = page.extract_text(); print(text[:8000])\""
    )

    result = coding_service._exec_command({"cmd": command})

    assert result.startswith("USE_DOCUMENT_TOOL:")
    assert "entry_number" in result
