"""
Copyright 2025, Adnan Khan

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import json
import random
import string


class Wildcard:
    """Class that equals everything for comparison operators."""

    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        # If other is a context variable, then we just return it,
        # because context to context would be a direct comp.
        # Duck-typed on purpose: the operand may be another Wildcard,
        # which forwards startswith to its own context string. Testing
        # for str here would break Wildcard-to-Wildcard comparison --
        # the head.repo.full_name == github.repository fork check.
        if hasattr(other, "startswith") and other.startswith("github."):
            return self.value == other

        # Anything else -- a literal string, a bool, a number -- is a
        # value the attacker can steer this context to, so it matches.
        return True

    def __hash__(self):
        # Defining __eq__ drops the inherited __hash__, and these end up
        # in sets while tags and triggers are collected.
        return hash(self.value)

    def startswith(self, check):
        return self.value.startswith(check)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return self.value


class FlexibleAction:
    """Flexible action matcher for events that a user can control."""

    def __init__(self, options):
        self.options = options

    def __contains__(self, other):
        return other in self.options

    @staticmethod
    def _unquote(other):
        """Strip expression quoting, leaving non-strings alone."""
        if isinstance(other, str) and other.startswith("'") and other.endswith("'"):
            return other[1:-1]
        return other

    def __eq__(self, other):
        # Returns a real bool: falling off the end gave None, which is
        # falsy but compares unequal to False.
        return self._unquote(other) in self.options

    def __ne__(self, other):
        other = self._unquote(other)
        # The actor picks the option, so "not equal" holds whenever any
        # option differs.
        return any(option != other for option in self.options)

    def __hash__(self):
        return hash(tuple(self.options))


class ExpressionEvaluator:
    STANDARD_VARIABLES = {
        "github.event.pull_request.merged": False,
        "github.event.pull_request.head.fork": True,
        "true": True,
        "false": False,
        "github.event.action": FlexibleAction(
            ["opened", "edited", "reopened", "synchronize", "closed"]
        ),
        "github.event_name": FlexibleAction(
            [
                "pull_request",
                "pull_request_target",
                "issue_comment",
                "issues",
                "workflow_run",
            ]
        ),
        "github.event.pull_request": True,
        "github.actor": "".join(random.choices(string.ascii_uppercase, k=10)),
        "github.event.pull_request.head.repo.owner.login": "".join(
            random.choices(string.ascii_uppercase, k=10)
        ),
        "github.event.comment.body": Wildcard("github.event.comment.body"),
        "github.event.label.name": False,
        "github.event.issue.pull_request": FlexibleAction([True, False]),
        "github.event.comment.author_association": FlexibleAction(
            ["CONTRIBUTOR", "NONE"]
        ),
        "github.event.pull_request.labels.*.name": [],
        "github.event.issue.labels.*.name": [],
        "github.event.pull_request.user.login": "".join(
            random.choices(string.ascii_uppercase, k=10)
        ),
        "github.event.pull_request.head.repo.full_name": Wildcard(
            "github.event.pull_request.head.repo.full_name"
        ),
        "github.repository": Wildcard("github.repository"),
    }

    def __init__(self, variables=None):
        # Variables is a dictionary mapping variable names to boolean values
        if variables is None:
            variables = {}
        self.variables = {**self.STANDARD_VARIABLES, **variables}

    def evaluate(self, node):
        if node.type == "identifier":
            if node.value in [
                "github.repository_owner",
                "github.event.pull_request.base.repo.owner.login",
            ]:
                return Wildcard(node.value)
            # Boolean literals are part of the threat model encoded in
            # STANDARD_VARIABLES, not an unknown context. They were
            # unreachable behind the github. check below, so every
            # `== true` / `== false` comparison failed open instead of
            # being answered.
            elif node.value in ("true", "false"):
                return self.variables[node.value]

            # Right now, it is not worth supporting non
            # github contexts. Anything that comes out of a step, etc.
            # is really hard to solve without running the step.
            # The vasty majority of if checks are context + string only.
            elif not node.value.startswith("github."):
                raise NotImplementedError()

            if isinstance(node.value, FlexibleAction):
                return True

            return self.variables.get(node.value, node.value)
        elif node.type == "string":
            if node.value.startswith("'") and node.value.endswith("'"):
                node.value = node.value[1:-1]
            return node.value
        elif node.type == "unary_negation":
            # Evaluate the operand and negate its value

            value = self.evaluate(node.children[0])

            # There are certain values that can be true for us always, so match either.
            if isinstance(value, FlexibleAction) and True in value and False in value:
                return True

            return not self.evaluate(node.children[0])
        elif node.type == "logical_and":
            # Evaluate logical AND between children
            return self.evaluate(node.children[0]) and self.evaluate(node.children[1])
        elif node.type == "logical_or":
            # Evaluate logical OR between children
            return self.evaluate(node.children[0]) or self.evaluate(node.children[1])
        elif node.type == "comparison":
            # Handle comparison operations
            left = self.evaluate(node.children[0])
            right = self.evaluate(node.children[1])
            if node.value == "==":
                return left == right
            elif node.value == "!=":
                return left != right
            else:
                raise ValueError(f"Unknown comparison operator: {node.value}")
        elif node.type == "function_call":
            # Handle function calls
            if node.value == "contains":
                # Evaluate the first argument
                container = self.evaluate(node.children[0])
                # Evaluate the second argument
                value = self.evaluate(node.children[1])

                # If a wildcard (like comment body, then go through)
                if type(container) is Wildcard:
                    return True

                if type(container) is bool:
                    return False

                if type(value) is not str:
                    value = str(value)

                return str(value) in container
            elif node.value in ["fromJson", "fromJSON"]:
                # Evaluate the argument
                json_string = self.evaluate(node.children[0])
                if (
                    type(json_string) is str
                    and json_string.startswith("'")
                    and json_string.endswith("'")
                ):
                    json_string = json_string[1:-1]
                    return json.loads(json_string)
                else:
                    return json_string
            elif node.value in ["toJSON", "toJson"]:
                json_string = self.evaluate(node.children[0])

                return json_string
            elif node.value == "success":
                return True
            elif node.value == "always":
                return True
            elif node.value == "startsWith":
                left = self.evaluate(node.children[0])
                right = self.evaluate(node.children[1])
                # We compare because if it is a field we control fully, then
                # it doesn't matter if it's starts with or full.
                return left == right
            elif node.value in "failure":
                # We can induce failure, so it's reachable.
                return True
            elif node.value == "cancelled":
                return False
            elif node.value == "format":
                # Just a hack for now
                return self.evaluate(node.children[1])
            else:
                raise ValueError(f"Unknown function: {node.value}")
        else:
            raise ValueError(f"Unknown node type: {node.type}")
