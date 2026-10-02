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