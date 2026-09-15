"""Tests for the optional YAML config file.

Precedence is the load-bearing behaviour here: a command line flag must beat
an environment variable, which must beat the config file.
"""

import argparse

import pytest
import yaml

from gatox.cli import cli as cli_module
from gatox.cli.config_file import (
    ConfigError,
    apply_env_overrides,
    detect_subcommand,
    load_config_file,
    merge_config,
    resolve_config_path,
    split_by_parser,
)
from gatox.cli.output import Output

Output(False)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "GH_TOKEN",
        "GH_APP_ID",
        "GH_APP_KEY",
        "GH_APP_INSTALLATION_ID",
        "GATOX_CONFIG",
    ):
        monkeypatch.delenv(name, raising=False)


def _write_config(tmp_path, payload):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload))
    return path


# --------------------------------------------------------------------------
# Path resolution
# --------------------------------------------------------------------------


def test_default_path_is_under_dot_config():
    path = resolve_config_path()
    assert path.parts[-3:] == (".config", "gato-x", "config.yaml")
    assert "~" not in str(path)


def test_explicit_path_wins_over_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GATOX_CONFIG", "/env/config.yaml")
    assert resolve_config_path("/explicit/config.yaml") == __import__("pathlib").Path(
        "/explicit/config.yaml"
    )


def test_env_path_is_used_when_no_flag(monkeypatch):
    monkeypatch.setenv("GATOX_CONFIG", "/env/config.yaml")
    assert str(resolve_config_path()) == "/env/config.yaml"


def test_tilde_is_expanded(monkeypatch):
    assert "~" not in str(resolve_config_path("~/somewhere/config.yaml"))


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def test_missing_file_is_not_an_error(tmp_path):
    assert load_config_file(tmp_path / "absent.yaml") == {}


def test_empty_file_is_not_an_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("")
    assert load_config_file(path) == {}


def test_malformed_yaml_raises(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("defaults:\n  - [unclosed\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config_file(path)


def test_non_mapping_top_level_raises(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_config_file(path)


# --------------------------------------------------------------------------
# Subcommand detection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["enumerate", "-t", "acme"], "enumerate"),
        (["enum", "-t", "acme"], "enumerate"),
        (["e", "-t", "acme"], "enumerate"),
        (["--api-url", "https://x", "attack", "--workflow"], "attack"),
        (["a"], "attack"),
        (["s", "-q", "x"], "search"),
        (["app", "--installations"], "app"),
        (["persist"], "persistence"),
        (["p"], "persistence"),
        (["--help"], None),
        ([], None),
    ],
)
def test_detect_subcommand(argv, expected):
    assert detect_subcommand(argv) == expected


# --------------------------------------------------------------------------
# Merging
# --------------------------------------------------------------------------


def test_subcommand_section_overrides_defaults():
    config = {
        "defaults": {"api_url": "https://default", "log_level": "INFO"},
        "enumerate": {"api_url": "https://enumerate"},
    }
    values, unknown = merge_config(config, "enumerate")

    assert values["api_url"] == "https://enumerate"
    assert values["log_level"] == "INFO"
    assert unknown == []


def test_other_subcommand_sections_are_ignored():
    config = {"enumerate": {"target": "acme"}, "attack": {"target": "other"}}
    values, _ = merge_config(config, "enumerate")
    assert values["target"] == "acme"


def test_defaults_apply_with_no_subcommand():
    values, _ = merge_config({"defaults": {"api_url": "https://x"}}, None)
    assert values["api_url"] == "https://x"


def test_dashed_keys_are_normalised():
    values, _ = merge_config({"defaults": {"skip-runners": True}}, None)
    assert values["skip_runners"] is True


def test_unknown_sections_are_reported():
    config = {"defaults": {}, "enumrate": {"target": "typo"}}
    _, unknown = merge_config(config, "enumerate")
    assert unknown == ["enumrate"]


def test_non_mapping_section_raises():
    with pytest.raises(ConfigError, match="must be a mapping"):
        merge_config({"defaults": "not-a-mapping"}, None)


def test_path_like_values_expand_tilde():
    values, _ = merge_config({"defaults": {"app_key": "~/keys/app.pem"}}, None)
    assert "~" not in values["app_key"]


def test_non_path_values_are_left_alone():
    values, _ = merge_config({"defaults": {"target": "~weird~"}}, None)
    assert values["target"] == "~weird~"


# --------------------------------------------------------------------------
# Environment precedence
# --------------------------------------------------------------------------


def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv("GH_APP_ID", "from-env")
    assert apply_env_overrides({"app_id": "from-config"})["app_id"] == "from-env"


def test_config_is_kept_when_env_is_unset():
    assert apply_env_overrides({"app_id": "from-config"})["app_id"] == "from-config"


def test_gh_token_env_overrides_config(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_env")
    assert apply_env_overrides({"gh_token": "ghp_config"})["gh_token"] == "ghp_env"


# --------------------------------------------------------------------------
# Routing values to the right parser
# --------------------------------------------------------------------------


def test_split_routes_shared_and_specific_keys():
    general, specific, unknown = split_by_parser(
        {"api_url": "x", "target": "acme", "bogus": 1},
        general_dests={"api_url"},
        subcommand_dests={"target"},
    )
    assert general == {"api_url": "x"}
    assert specific == {"target": "acme"}
    assert unknown == ["bogus"]


# --------------------------------------------------------------------------
# End to end through the real CLI
# --------------------------------------------------------------------------


def _general_parser():
    parser = argparse.ArgumentParser()
    cli_module.configure_parser_general(parser)
    return parser


def test_config_flag_parses(tmp_path):
    args = _general_parser().parse_args(["--config", "/tmp/x.yaml"])
    assert args.config == "/tmp/x.yaml"


def test_no_config_flag_parses():
    assert _general_parser().parse_args(["--no-config"]).no_config is True


def test_config_supplies_a_value_that_has_no_flag_on_the_command_line(tmp_path):
    path = _write_config(tmp_path, {"defaults": {"api_url": "https://from-config"}})
    parser = _general_parser()

    cli_module.apply_config_defaults(["--config", str(path)], parser, {})

    assert parser.parse_args([]).api_url == "https://from-config"


def test_command_line_flag_beats_config(tmp_path):
    path = _write_config(tmp_path, {"defaults": {"api_url": "https://from-config"}})
    parser = _general_parser()

    cli_module.apply_config_defaults(["--config", str(path)], parser, {})
    args = parser.parse_args(["--api-url", "https://from-flag"])

    assert args.api_url == "https://from-flag"


def test_env_beats_config_for_app_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_APP_ID", "from-env")
    path = _write_config(tmp_path, {"defaults": {"app_id": "from-config"}})
    parser = _general_parser()

    cli_module.apply_config_defaults(["--config", str(path)], parser, {})

    assert parser.parse_args([]).app_id == "from-env"


def test_flag_beats_env_and_config(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_APP_ID", "from-env")
    path = _write_config(tmp_path, {"defaults": {"app_id": "from-config"}})
    parser = _general_parser()

    cli_module.apply_config_defaults(["--config", str(path)], parser, {})

    assert parser.parse_args(["--app-id", "from-flag"]).app_id == "from-flag"


def test_no_config_ignores_the_file(tmp_path):
    path = _write_config(tmp_path, {"defaults": {"api_url": "https://from-config"}})
    parser = _general_parser()

    values = cli_module.apply_config_defaults(
        ["--config", str(path), "--no-config"], parser, {}
    )

    assert values == {}
    assert parser.parse_args([]).api_url is None


def test_missing_explicit_config_file_is_an_error(tmp_path):
    parser = _general_parser()
    with pytest.raises(SystemExit):
        cli_module.apply_config_defaults(
            ["--config", str(tmp_path / "absent.yaml")], parser, {}
        )


def test_absent_default_config_is_silent(monkeypatch, tmp_path):
    monkeypatch.setenv("GATOX_CONFIG", str(tmp_path / "absent.yaml"))
    parser = _general_parser()

    assert cli_module.apply_config_defaults([], parser, {}) == {}


def test_malformed_config_exits_with_an_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("defaults:\n  - [unclosed\n")
    parser = _general_parser()

    with pytest.raises(SystemExit):
        cli_module.apply_config_defaults(["--config", str(path)], parser, {})


def test_subcommand_values_reach_the_subparser(tmp_path):
    from gatox.cli.enumeration.config import configure_parser_enumerate

    path = _write_config(
        tmp_path,
        {"enumerate": {"target": "acme-corp", "skip_runners": True}},
    )
    parser = _general_parser()
    sub = argparse.ArgumentParser()
    configure_parser_enumerate(sub)

    cli_module.apply_config_defaults(
        ["--config", str(path), "enumerate"], parser, {"enumerate": sub}
    )
    args = sub.parse_args([])

    assert args.target == "acme-corp"
    assert args.skip_runners is True


def test_gh_token_from_config_is_returned_for_later_stages(tmp_path):
    path = _write_config(tmp_path, {"defaults": {"gh_token": "ghp_from_config"}})
    parser = _general_parser()

    values = cli_module.apply_config_defaults(["--config", str(path)], parser, {})

    assert values["gh_token"] == "ghp_from_config"


def test_validate_arguments_accepts_a_config_supplied_token(tmp_path):
    args = _general_parser().parse_args([])
    args.socks_proxy = None
    args.http_proxy = None
    args.log_level = "CRITICAL"
    args.config_values = {"gh_token": "ghp_" + "A" * 36}

    cli_module.validate_arguments(args, argparse.ArgumentParser())

    assert args.gh_token == "ghp_" + "A" * 36


# --------------------------------------------------------------------------
# Booleans
#
# gatox.models.workflow strips the global YAML bool resolvers so that a
# workflow's `on:` key stays a string. The config loader must not inherit
# that, or `skip_runners: false` would arrive as the truthy string "false"
# and mean the opposite of what the operator wrote.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "literal,expected",
    [
        ("true", True),
        ("True", True),
        ("false", False),
        ("False", False),
        ("yes", True),
        ("no", False),
        ("on", True),
        ("off", False),
    ],
)
def test_yaml_booleans_load_as_booleans(tmp_path, literal, expected):
    path = tmp_path / "config.yaml"
    path.write_text(f"defaults:\n  skip_runners: {literal}\n")

    values, _ = merge_config(load_config_file(path), None)

    assert values["skip_runners"] is expected


def test_boolean_survives_the_workflow_module_resolver_patch(tmp_path):
    # Importing this module mutates the global yaml Resolver as a side effect.
    import gatox.models.workflow  # noqa: F401

    path = tmp_path / "config.yaml"
    path.write_text("defaults:\n  skip_runners: false\n")

    values, _ = merge_config(load_config_file(path), None)

    assert values["skip_runners"] is False


def test_false_reaches_the_parser_as_false(tmp_path):
    from gatox.cli.enumeration.config import configure_parser_enumerate

    path = tmp_path / "config.yaml"
    path.write_text("enumerate:\n  skip_runners: false\n")

    parser = _general_parser()
    sub = argparse.ArgumentParser()
    configure_parser_enumerate(sub)

    cli_module.apply_config_defaults(
        ["--config", str(path), "enumerate"], parser, {"enumerate": sub}
    )

    assert sub.parse_args([]).skip_runners is False


def test_integers_and_strings_keep_their_types(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("defaults:\n  log_level: INFO\nattack:\n  timeout: 60\n")

    values, _ = merge_config(load_config_file(path), "attack")

    assert values["timeout"] == 60 and isinstance(values["timeout"], int)
    assert values["log_level"] == "INFO"


def test_global_yaml_resolver_is_left_alone(tmp_path):
    """Importing the workflow parser must not break YAML for everyone else.

    The workflow parser needs `on:` to stay a string, which used to be done
    by stripping bool resolvers from the global yaml Resolver. That made an
    unrelated `enabled: false` load as the truthy string "false" anywhere in
    the process. The loader is scoped now; this pins that.
    """
    import yaml

    import gatox.models.composite  # noqa: F401
    import gatox.models.workflow  # noqa: F401

    assert yaml.safe_load("enabled: false")["enabled"] is False
    assert yaml.safe_load("enabled: true")["enabled"] is True


def test_workflow_loader_still_keeps_on_as_a_string():
    import yaml

    from gatox.models.yaml_loader import WorkflowLoader

    parsed = yaml.load("on: push\njobs: {}\n", Loader=WorkflowLoader)
    assert "on" in parsed and parsed["on"] == "push"
