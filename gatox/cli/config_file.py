"""Optional YAML configuration file for Gato-X.

Every command line flag can be set in a config file so an operator does not
have to retype proxies, an API URL, or App credentials on every run. The
file is entirely optional; without one Gato-X behaves exactly as before.

Layout::

    defaults:          # applies to every subcommand
      api_url: https://octocorp.ghe.com
      socks_proxy: 127.0.0.1:9050
      app_id: "12345"
      app_key: ~/.config/gato-x/app.pem

    enumerate:         # only when running `gato-x enumerate`
      target: acme-corp
      skip_runners: true

Values are keyed by the flag's long name with dashes turned into
underscores (``--skip-runners`` becomes ``skip_runners``), which is also
the attribute name argparse uses.

Precedence, highest first:

1. Command line flags
2. Environment variables (``GH_TOKEN``, ``GH_APP_ID``, ``GH_APP_KEY``,
   ``GH_APP_INSTALLATION_ID``)
3. The config file -- the subcommand section, then ``defaults``
4. Gato-X's own built-in defaults

This is implemented by folding the resolved values into argparse defaults
before parsing, so an explicit flag naturally wins without any special
casing at the point of use.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class _ConfigLoader(yaml.SafeLoader):
    """A loader with standard YAML boolean handling.

    ``gatox.models.workflow`` strips the bool resolvers from the *global*
    ``yaml.resolver.Resolver`` at import time, so that a workflow's ``on:``
    key is read as the string "on" rather than ``True``. That is right for
    parsing Actions workflows and wrong for everything else: without this
    loader ``skip_runners: false`` would arrive as the string ``"false"``,
    which is truthy, and the setting would silently mean its opposite.

    Taking a private copy of the resolver table makes this immune to that
    patch regardless of module import order.
    """


_ConfigLoader.yaml_implicit_resolvers = {
    key: list(value)
    for key, value in yaml.resolver.Resolver.yaml_implicit_resolvers.items()
}

#: The YAML 1.1 boolean resolver, restored for config files only.
_ConfigLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(
        r"^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE"
        r"|on|On|ON|off|Off|OFF)$"
    ),
    list("yYnNtTfFoO"),
)

#: Default location, overridable with ``--config`` or ``GATOX_CONFIG``.
DEFAULT_CONFIG_PATH = Path("~/.config/gato-x/config.yaml")

#: Section holding values that apply to every subcommand.
DEFAULTS_SECTION = "defaults"

#: Environment variables that outrank the config file, keyed by the argparse
#: destination they feed.
ENV_OVERRIDES = {
    "app_id": "GH_APP_ID",
    "app_key": "GH_APP_KEY",
    "installation_id": "GH_APP_INSTALLATION_ID",
    "gh_token": "GH_TOKEN",
}

#: Subcommand names and aliases, so the right section is selected before
#: argparse has run.
SUBCOMMAND_ALIASES = {
    "attack": "attack",
    "a": "attack",
    "enumerate": "enumerate",
    "enum": "enumerate",
    "e": "enumerate",
    "search": "search",
    "s": "search",
    "app": "app",
    "persistence": "persistence",
    "persist": "persistence",
    "p": "persistence",
}

#: Values that name a file or directory and should honour ``~``.
PATH_LIKE_KEYS = frozenset(
    {
        "app_key",
        "pem",
        "key_path",
        "custom_file",
        "output_json",
        "output_text",
        "output_yaml",
        "cache_save_file",
        "cache_restore_file",
        "save_runlogs",
        "repositories",
        "local",
    }
)


class ConfigError(Exception):
    """Raised when a config file exists but cannot be used."""


def resolve_config_path(explicit: str | None = None) -> Path:
    """Return the config file path to read.

    Args:
        explicit: Value of ``--config``, if the operator passed one.

    Returns:
        The path, with ``~`` expanded. It may not exist.
    """
    raw = explicit or os.environ.get("GATOX_CONFIG") or str(DEFAULT_CONFIG_PATH)
    return Path(raw).expanduser()


def load_config_file(path: Path) -> dict[str, Any]:
    """Read and parse the config file.

    A missing file is not an error -- the config file is optional. A file
    that exists but is malformed is, because silently ignoring it would
    apply settings the operator believes are in effect.

    Args:
        path: Location to read.

    Returns:
        The parsed mapping, or an empty dict when the file is absent.

    Raises:
        ConfigError: If the file cannot be read or is not a YAML mapping.
    """
    if not path.exists():
        logger.debug("No config file at %s", path)
        return {}

    try:
        with open(path) as handle:
            parsed = yaml.load(handle, Loader=_ConfigLoader)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc

    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ConfigError(
            f"{path} must contain a YAML mapping at the top level, not a "
            f"{type(parsed).__name__}."
        )
    return parsed


def detect_subcommand(argv: list[str]) -> str | None:
    """Work out which subcommand is being run, before argparse parses.

    The config section is needed to build the parser defaults, which has to
    happen before parsing. Scans for the first bare word that names a
    subcommand, skipping flags and their values.

    Args:
        argv: The raw argument list.

    Returns:
        The canonical subcommand name, or ``None`` if none is present.
    """
    for token in argv:
        if token.startswith("-"):
            continue
        canonical = SUBCOMMAND_ALIASES.get(token)
        if canonical:
            return canonical
    return None


def _expand_paths(values: dict[str, Any]) -> dict[str, Any]:
    """Expand ``~`` in values that name a path."""
    expanded = {}
    for key, value in values.items():
        if key in PATH_LIKE_KEYS and isinstance(value, str):
            expanded[key] = str(Path(value).expanduser())
        else:
            expanded[key] = value
    return expanded


def _normalise_keys(section: dict[str, Any]) -> dict[str, Any]:
    """Accept ``skip-runners`` as readily as ``skip_runners``."""
    return {str(key).replace("-", "_"): value for key, value in section.items()}


def merge_config(
    config: dict[str, Any], subcommand: str | None
) -> tuple[dict[str, Any], list[str]]:
    """Flatten the config into one mapping of argparse destination to value.

    Args:
        config: The parsed config file.
        subcommand: Canonical subcommand name, or ``None``.

    Returns:
        ``(values, unknown_sections)``. Values from the subcommand's own
        section override those in ``defaults``. ``unknown_sections`` names
        top-level keys that are neither ``defaults`` nor a subcommand, so
        the caller can warn about a typo rather than silently ignore it.

    Raises:
        ConfigError: If a section is present but is not a mapping.
    """
    values: dict[str, Any] = {}
    unknown: list[str] = []

    known_sections = {DEFAULTS_SECTION, *SUBCOMMAND_ALIASES.values()}

    for section_name in (DEFAULTS_SECTION, subcommand):
        if not section_name:
            continue
        section = config.get(section_name)
        if section is None:
            continue
        if not isinstance(section, dict):
            raise ConfigError(
                f"The '{section_name}' section of the config file must be a "
                f"mapping, not a {type(section).__name__}."
            )
        values.update(_normalise_keys(section))

    for key in config:
        if key not in known_sections:
            unknown.append(str(key))

    return _expand_paths(values), unknown


def apply_env_overrides(values: dict[str, Any]) -> dict[str, Any]:
    """Let environment variables outrank the config file.

    Args:
        values: Config-derived values.

    Returns:
        A new mapping with env vars substituted in where they are set.
    """
    resolved = dict(values)
    for dest, env_name in ENV_OVERRIDES.items():
        from_env = os.environ.get(env_name)
        if from_env:
            resolved[dest] = from_env
    return resolved


def split_by_parser(
    values: dict[str, Any], general_dests: set[str], subcommand_dests: set[str]
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Route each value to the parser that owns its flag.

    Shared flags are registered on the top level parser *and* on every
    subparser (with suppressed defaults so a value passed before the
    subcommand is not clobbered). Setting a default for one of those on a
    subparser would undo that, so they must go to the top level parser.

    Args:
        values: Flattened config values.
        general_dests: Destinations owned by the shared parser.
        subcommand_dests: Destinations owned by the active subparser.

    Returns:
        ``(general, subcommand, unknown_keys)``.
    """
    general: dict[str, Any] = {}
    specific: dict[str, Any] = {}
    unknown: list[str] = []

    for key, value in values.items():
        if key in general_dests:
            general[key] = value
        elif key in subcommand_dests:
            specific[key] = value
        else:
            unknown.append(key)

    return general, specific, unknown


def _coerce_one(dest, action, value):
    """Apply one flag's own type and choices rules to a config value.

    argparse validates values it parses off the command line, but it does
    not validate defaults. Without this, ``log_level: NOT_A_LEVEL`` in the
    config file sails past the parser and blows up much later inside
    ``logging``, with a traceback that says nothing about the config file.

    Args:
        dest: The argparse destination, used in error messages.
        action: The argparse Action that owns the flag.
        value: The value read from the config file.

    Returns:
        The value, coerced to the flag's type where needed.

    Raises:
        ConfigError: If the value is not usable for this flag.
    """
    flag = action.option_strings[0] if action.option_strings else dest
    is_flag = isinstance(
        action, (argparse._StoreTrueAction, argparse._StoreFalseAction)
    )

    if is_flag:
        if not isinstance(value, bool):
            raise ConfigError(
                f"{dest}: {flag} is a switch, so it needs true or false, not {value!r}."
            )
        return value

    takes_many = action.nargs in ("*", "+") or isinstance(
        action, argparse._AppendAction
    )
    if takes_many:
        items = value if isinstance(value, list) else [value]
    else:
        if isinstance(value, list):
            raise ConfigError(f"{dest}: {flag} takes a single value, not a list.")
        items = [value]

    coerced = []
    for item in items:
        if callable(action.type) and not isinstance(item, bool):
            try:
                item = action.type(str(item) if not isinstance(item, str) else item)
            except Exception as exc:  # noqa: BLE001 -- reported with context
                raise ConfigError(
                    f"{dest}: {item!r} is not valid for {flag} ({exc})"
                ) from exc
        if action.choices is not None and item not in action.choices:
            raise ConfigError(
                f"{dest}: {item!r} is not valid for {flag}. "
                f"Choose from {', '.join(map(str, action.choices))}."
            )
        coerced.append(item)

    return coerced if takes_many else coerced[0]


def coerce_values(values, parsers):
    """Validate config values against the flags that own them.

    Args:
        values: Flattened config values.
        parsers: Parsers to look for each flag in, most specific first.

    Returns:
        ``(coerced, errors)``. Keys with no matching flag pass through
        untouched; the caller reports those separately as unknown.
    """
    actions_by_dest = {}
    for parser in parsers:
        if parser is None:
            continue
        for action in parser._actions:
            actions_by_dest.setdefault(action.dest, action)

    coerced, errors = {}, []
    for dest, value in values.items():
        action = actions_by_dest.get(dest)
        if action is None:
            coerced[dest] = value
            continue
        try:
            coerced[dest] = _coerce_one(dest, action, value)
        except ConfigError as exc:
            errors.append(str(exc))
    return coerced, errors
