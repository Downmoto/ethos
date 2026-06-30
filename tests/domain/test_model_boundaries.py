import ast
from pathlib import Path

MODEL_FILES = [
    Path("src/cassiopeia/gateways/models.py"),
    Path("src/cassiopeia/memory/models.py"),
    Path("src/cassiopeia/permissions/models.py"),
    Path("src/cassiopeia/personas/models.py"),
    Path("src/cassiopeia/sessions/models.py"),
    Path("src/cassiopeia/skills/models.py"),
    Path("src/cassiopeia/subagents/models.py"),
    Path("src/cassiopeia/tools/models.py"),
    Path("src/cassiopeia/workflows/models.py"),
    Path("src/cassiopeia/workspaces/models.py"),
]

BANNED_IMPORTS = {
    "cassiopeia.cli",
    "cassiopeia.tui",
    "cassiopeia.storage",
    "cassiopeia.providers",
    "click",
    "textual",
    "pyturso",
    "pydantic_ai",
}


def test_feature_models_do_not_import_delivery_storage_or_provider_code() -> None:
    for path in MODEL_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )

        assert not any(
            imported == banned or imported.startswith(f"{banned}.")
            for imported in imports
            for banned in BANNED_IMPORTS
        ), path
