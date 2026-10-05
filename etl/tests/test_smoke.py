from click.testing import CliRunner

from hydroatlas.cli import main


def test_cli_entrypoint_exists():
    assert callable(main)


def test_cli_group_has_commands():
    assert set(main.commands) >= {"fetch", "registry"}


def test_cli_help_runs():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "fetch" in result.output
