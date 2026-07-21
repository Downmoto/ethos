import asyncio
from pathlib import Path

import pytest
from pydantic_ai import Agent, FunctionToolset
from pydantic_ai.models.test import TestModel

from ethos.environments import resolve_workspace_environment
from ethos.provider import ProviderName
from ethos.storage import Storage
from ethos.workspaces import Workspace, WorkspaceManager


def noop() -> None:
    pass


def configured_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, WorkspaceManager, Workspace, Storage]:
    home = tmp_path / ".ethos"
    home.mkdir()
    (home / "config.yaml").write_text(
        "events:\n  enabled: true\n"
        "provider:\n  name: ollama\n  model_name: global-model\n"
        "keys: {}\n",
        encoding="utf-8",
    )
    (home / "tools.yaml").write_text(
        "tools: {}\ntoolsets: {}\n", encoding="utf-8"
    )
    (home / "skills").mkdir()
    manager = WorkspaceManager(home / "workspaces")
    workspace = manager.create("my-project")
    workspace.config_path.write_text(
        "events:\n  print_events: true\n"
        "provider:\n  model_name: workspace-model\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ETHOS_PROVIDER__MODEL_NAME", raising=False)
    storage = Storage(home / "data" / "ethos.db")
    return home, manager, workspace, storage


def test_workspace_configuration_recursively_overrides_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, manager, _workspace, storage = configured_environment(
        tmp_path, monkeypatch
    )

    environment = resolve_workspace_environment(
        home, manager, "my-project", {}, storage
    )

    assert environment.settings.provider.name is ProviderName.OLLAMA
    assert environment.settings.provider.model_name == "workspace-model"
    assert environment.settings.events.enabled
    assert environment.settings.events.print_events
    storage.close()


def test_environment_variables_override_workspace_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, manager, _workspace, storage = configured_environment(
        tmp_path, monkeypatch
    )
    monkeypatch.setenv("ETHOS_PROVIDER__MODEL_NAME", "environment-model")

    environment = resolve_workspace_environment(
        home, manager, "my-project", {}, storage
    )

    assert environment.settings.provider.model_name == "environment-model"
    storage.close()


def test_workspace_tools_are_limited_by_global_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, manager, workspace, storage = configured_environment(
        tmp_path, monkeypatch
    )
    web = FunctionToolset[object](id="web")
    web.add_function(noop, name="web.search")
    web.add_function(noop, name="web.fetch")
    filesystem = FunctionToolset[object](id="filesystem")
    filesystem.add_function(noop, name="filesystem.read")
    (home / "tools.yaml").write_text(
        "tools:\n  filesystem.read: false\ntoolsets:\n  web: true\n",
        encoding="utf-8",
    )
    workspace.tools_config_path.write_text(
        "tools:\n  web.fetch: false\n  filesystem.read: true\n"
        "toolsets:\n  web: true\n",
        encoding="utf-8",
    )

    environment = resolve_workspace_environment(
        home,
        manager,
        "my-project",
        {"web": web, "filesystem": filesystem},
        storage,
    )

    model = TestModel()
    asyncio.run(
        Agent(model).run("show available tools", toolsets=environment.toolsets)
    )
    parameters = model.last_model_request_parameters
    assert parameters is not None
    assert [tool.name for tool in parameters.function_tools] == ["web.search"]
    storage.close()


def test_tool_catalogue_rejects_names_in_multiple_toolsets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, manager, _workspace, storage = configured_environment(
        tmp_path, monkeypatch
    )
    first = FunctionToolset[object]()
    first.add_function(noop, name="shared")
    second = FunctionToolset[object]()
    second.add_function(noop, name="shared")

    with pytest.raises(
        ValueError, match="tools belong to multiple toolsets: shared"
    ):
        resolve_workspace_environment(
            home,
            manager,
            "my-project",
            {"first": first, "second": second},
            storage,
        )

    storage.close()


def test_workspace_selects_globally_installed_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, manager, workspace, storage = configured_environment(
        tmp_path, monkeypatch
    )
    selected = home / "skills" / "code-review"
    selected.mkdir()
    (selected / "SKILL.md").write_text("# Code review\n", encoding="utf-8")
    unselected = home / "skills" / "research"
    unselected.mkdir()
    (unselected / "SKILL.md").write_text("# Research\n", encoding="utf-8")
    workspace.skills_config_path.write_text(
        "skills:\n  - code-review\n", encoding="utf-8"
    )

    environment = resolve_workspace_environment(
        home, manager, "my-project", {}, storage
    )

    assert [(skill.name, skill.path) for skill in environment.skills] == [
        ("code-review", selected)
    ]
    storage.close()


def test_workspace_rejects_uninstalled_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, manager, workspace, storage = configured_environment(
        tmp_path, monkeypatch
    )
    workspace.skills_config_path.write_text(
        "skills:\n  - missing\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="skills are not installed: missing"):
        resolve_workspace_environment(home, manager, "my-project", {}, storage)

    storage.close()


def test_memory_access_is_scoped_to_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, manager, _workspace, storage = configured_environment(
        tmp_path, monkeypatch
    )

    environment = resolve_workspace_environment(
        home, manager, "my-project", {}, storage
    )

    assert environment.memory.workspace_name == "my-project"
    assert environment.memory.storage is storage
    storage.close()
