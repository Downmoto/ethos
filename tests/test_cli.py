from pathlib import Path

from click.testing import CliRunner

from cassiopeia.cli.main import PLACEHOLDER_GROUPS, main


def test_ask_command_smoke() -> None:
    result = CliRunner().invoke(main, ["ask", "hello"])

    assert result.exit_code == 0
    assert result.output == "hello world\n" or result.output == "hello world: hello\n"


def test_help_lists_administration_groups() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    for group_name in PLACEHOLDER_GROUPS:
        assert group_name in result.output


def test_placeholder_groups_fail_clearly() -> None:
    for group_name in PLACEHOLDER_GROUPS:
        result = CliRunner().invoke(main, [group_name])

        assert result.exit_code == 1
        assert (
            f"`cass {group_name}` is reserved for future administration commands." in result.output
        )


def test_init_creates_home(tmp_path: Path) -> None:
    home = tmp_path / "cassiopeia-home"

    result = CliRunner().invoke(main, ["init", "--home", str(home)])

    assert result.exit_code == 0
    assert result.output == f"{home}\n"
    assert (home / "config.json").is_file()
    assert (home / "personas").is_dir()


def test_init_uses_environment_home(tmp_path: Path) -> None:
    home = tmp_path / "env-home"

    result = CliRunner().invoke(main, ["init"], env={"CASSIOPEIA_HOME": str(home)})

    assert result.exit_code == 0
    assert result.output == f"{home}\n"
    assert (home / "config.json").is_file()


def test_init_rejects_existing_home(tmp_path: Path) -> None:
    home = tmp_path / "cassiopeia-home"
    home.mkdir()
    (home / "config.json").write_text('{"version": 2}\n', encoding="utf-8")

    result = CliRunner().invoke(main, ["init", "--home", str(home)])

    assert result.exit_code == 1
    assert "cassiopeia home already exists" in result.output
    assert (home / "config.json").read_text(encoding="utf-8") == '{"version": 2}\n'
