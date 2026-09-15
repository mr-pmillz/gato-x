"""YAML loader for GitHub Actions workflow and action definitions.

YAML 1.1 resolves ``on``, ``off``, ``yes``, ``no``, ``true`` and ``false``
to booleans. That is wrong for Actions files, where ``on:`` is the trigger
block -- a stock loader turns that key into ``True`` and the workflow
becomes unparseable.

The fix used to be a loop that deleted those resolvers from the *global*
``yaml.resolver.Resolver`` at import time. It worked, but it reached far
beyond the workflow parser: every YAML document loaded anywhere in the
process lost boolean handling, so an unrelated ``enabled: false`` came back
as the string ``"false"``, which is truthy. Scoping the change to this
loader keeps the workflow parser correct without that side effect.
"""

from __future__ import annotations

import yaml

#: Characters whose implicit resolvers must not produce booleans. These are
#: the first letters of On/Off, True/False -- the forms that collide with
#: Actions keys and values.
_NON_BOOL_PREFIXES = "OoTtFf"

_BOOL_TAG = "tag:yaml.org,2002:bool"


class WorkflowLoader(yaml.CSafeLoader):
    """Loads Actions YAML with ``on``/``off``/``true``/``false`` kept as strings."""


def _build_resolvers() -> dict:
    """Copy the stock resolver table, dropping bools for the Actions keys."""
    resolvers = {
        char: list(entries)
        for char, entries in yaml.resolver.Resolver.yaml_implicit_resolvers.items()
    }

    for char in _NON_BOOL_PREFIXES:
        remaining = [
            entry for entry in resolvers.get(char, []) if entry[0] != _BOOL_TAG
        ]
        if remaining:
            resolvers[char] = remaining
        else:
            resolvers.pop(char, None)

    return resolvers


WorkflowLoader.yaml_implicit_resolvers = _build_resolvers()


class WorkflowDumper(yaml.Dumper):
    """Emits Actions YAML with ``on`` and ``true``/``false`` left unquoted.

    The dumper quotes a string only when it would otherwise be read back as
    another type. With stock resolvers the key ``on`` and the value ``"true"``
    are ambiguous, so they come out as ``'on':`` and ``'true'``. Actions
    files conventionally carry them bare, and the generated payloads are
    pushed to real repositories, so the same resolver table used for loading
    is applied here to keep the output unchanged.
    """


WorkflowDumper.yaml_implicit_resolvers = _build_resolvers()
