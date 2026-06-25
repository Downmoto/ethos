from pathlib import Path

from click.testing import CliRunner

from cli.main import main


def test_ask_command_smoke() -> None:
    result = CliRunner().invoke(main, ["ask", "hello"])

    assert result.exit_code == 0
    assert result.output == "hello world\n" or result.output == "hello world: hello\n"


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
