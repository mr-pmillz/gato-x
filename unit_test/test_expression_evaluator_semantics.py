"""Evaluator semantics for boolean literals and non-string operands.

STANDARD_VARIABLES encodes the attacker's situation: an unmerged pull
request from a fork, an actor they control. Evaluating an `if:` against it
answers "could the attacker reach this job".
"""

import pytest

from gatox.workflow_parser.expression_evaluator import (
    ExpressionEvaluator,
    FlexibleAction,
    Wildcard,
)
from gatox.workflow_parser.expression_parser import ExpressionParser
from gatox.workflow_parser.utility import validate_if_check


def _eval(expression):
    return ExpressionEvaluator().evaluate(ExpressionParser(expression).get_node())


# --------------------------------------------------------------------------
# Boolean literals
#
# `true` and `false` are in STANDARD_VARIABLES but the "not github." check
# rejected them first, so every `== true` / `== false` comparison raised
# NotImplementedError and failed open.
# --------------------------------------------------------------------------


def test_comparison_against_false_literal_is_evaluated():
    # The attacker's pull request is not merged, so this gate lets them in.
    assert _eval("github.event.pull_request.merged == false") is True


def test_comparison_against_true_literal_is_evaluated():
    # A deploy-on-merge gate: the attacker cannot merge their own PR.
    assert _eval("github.event.pull_request.merged == true") is False


def test_literal_on_the_left_hand_side_also_works():
    assert _eval("true == github.event.issue.pull_request") is True


def test_boolean_literals_no_longer_raise():
    for expression in (
        "github.event.pull_request.merged == false",
        "github.event.pull_request.merged == true",
        "github.event.comment.body == true",
    ):
        _eval(expression)  # must not raise NotImplementedError


def test_unknown_contexts_still_fail_open():
    """Step outputs and env vars remain unknowable, so stay conservative."""
    assert validate_if_check("env.SOMETHING == 'x'", {}) is True
    assert validate_if_check("steps.foo.outputs.bar == 'x'", {}) is True


# --------------------------------------------------------------------------
# Operand types
# --------------------------------------------------------------------------


@pytest.mark.parametrize("operand", [True, False, 42, None])
def test_wildcard_tolerates_non_string_operands(operand):
    # Previously AttributeError: 'bool' object has no attribute 'startswith'.
    assert (Wildcard("github.repository") == operand) is True


def test_wildcard_still_compares_context_to_context():
    """The fork check depends on this and must not regress.

    `head.repo.full_name == github.repository` compares two Wildcards; a
    fork's full name differs, so the guard excludes the attacker.
    """
    assert (
        _eval("github.event.pull_request.head.repo.full_name == github.repository")
        is False
    )


def test_wildcard_matches_a_plain_string():
    assert (Wildcard("github.repository") == "'acme/widgets'") is True


def test_wildcard_is_hashable():
    assert len({Wildcard("a"), Wildcard("a"), Wildcard("b")}) >= 1


@pytest.mark.parametrize("operand", [1, None, object()])
def test_flexible_action_tolerates_non_string_operands(operand):
    assert (FlexibleAction(["opened"]) == operand) is False


def test_flexible_action_matches_a_non_string_option():
    assert (FlexibleAction([True, False]) == True) is True  # noqa: E712


def test_flexible_action_eq_returns_a_real_bool():
    """It used to fall off the end and return None on no match."""
    result = FlexibleAction(["opened"]) == "'closed'"
    assert result is False and isinstance(result, bool)


def test_flexible_action_ne_returns_a_real_bool():
    result = FlexibleAction(["opened"]) != "'opened'"
    assert isinstance(result, bool)


def test_flexible_action_ne_holds_when_an_option_differs():
    # The actor picks the association, so "not NONE" is satisfiable.
    assert (FlexibleAction(["CONTRIBUTOR", "NONE"]) != "'NONE'") is True


def test_flexible_action_is_hashable():
    assert len({FlexibleAction(["a"]), FlexibleAction(["b"])}) == 2


# --------------------------------------------------------------------------
# Detection behaviour that must not drift
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expression,reachable",
    [
        ("github.event_name == 'pull_request_target'", True),
        ("github.event_name == 'push'", False),
        ("github.event.comment.author_association != 'NONE'", True),
        ("github.event.label.name == 'safe to test'", False),
        ("github.repository == 'acme/widgets'", True),
        ("!github.event.pull_request.merged", True),
        ("contains(github.event.comment.body, 'lgtm')", True),
    ],
)
def test_established_detection_outcomes_are_unchanged(expression, reachable):
    assert bool(_eval(expression)) is reachable
