"""Cases for the shared test helpers in the root conftest."""

import asyncio
import functools
import types
from collections.abc import Callable

import pytest


class Target:
    """A class whose methods a case patches, one of each kind."""

    @classmethod
    async def async_class_method(cls, value: int) -> int:
        """Return the value unchanged, once awaited.

        Args:
            value: What to return.

        Returns:
            The value.
        """
        return value

    @classmethod
    def class_method(cls, value: int) -> int:
        """Return the value unchanged.

        Args:
            value: What to return.

        Returns:
            The value.
        """
        return value

    def instance_method(self, value: int) -> int:
        """Return the value unchanged.

        Args:
            value: What to return.

        Returns:
            The value.
        """
        return value

    @staticmethod
    def static_method(value: int) -> int:
        """Return the value unchanged.

        Args:
            value: What to return.

        Returns:
            The value.
        """
        return value


TESTS = {
    "patches": {
        "a class method patch receives the class, through the class and an instance alike": {
            "args": ["class_method", 2],
            "patches": [(Target, "class_method", lambda cls, value: (cls.__name__, value))],
            "returns": (("Target", 2), ("Target", 2)),
        },
        "an async class method patch is awaited and receives the class": {
            "args": ["async_class_method", 2],
            "patches": [(Target, "async_class_method", lambda cls, value: (cls.__name__, value))],
            "returns": (("Target", 2), ("Target", 2)),
        },
        "an async class method patch returning a coroutine has it awaited too": {
            "args": ["async_class_method", 2],
            "patches": [(Target, "async_class_method", lambda cls, value: asyncio.sleep(0, (cls.__name__, value)))],
            "returns": (("Target", 2), ("Target", 2)),
        },
        "a static method patch stays static": {
            "args": ["static_method", 2],
            "patches": [(Target, "static_method", lambda value: value * 10)],
            "returns": (20, 20),
        },
    },
    # Each case is run by `function_tester` against `function_tester` itself, so `args` holds the inner case and the
    # callable that case runs against.
    "unheld": {
        "a case declaring no result": {
            "args": [{"args": [2]}, Target.static_method],
            "raises": (ValueError, "Test must declare one of: returns, raises, attributes"),
        },
        "a case declaring two results": {
            "args": [{"args": [2], "raises": (ValueError, "never"), "returns": 2}, Target.static_method],
            "raises": (ValueError, "Test must declare only one of: returns, raises, attributes"),
        },
        "a case declaring attributes with none to compare": {
            "args": [{"attributes": {}}, types.SimpleNamespace],
            "raises": (ValueError, "Test attributes result type must have values"),
        },
        "a case declaring patches with no monkeypatch to apply them": {
            "args": [
                {"args": [2], "patches": [(Target, "static_method", lambda value: value)], "returns": 2},
                Target.static_method,
            ],
            "raises": (ValueError, "Test declares patches but no monkeypatch was passed"),
        },
        "a case declaring a result its callable does not return": {
            "args": [{"args": [2], "returns": 3}, Target.static_method],
            "raises": (AssertionError, r"Result:\s+2\s+Expected:\s+3"),
        },
        "a case declaring a raise its callable never makes": {
            "args": [{"args": [2], "raises": (ValueError, "never")}, Target.static_method],
            "raises": (pytest.fail.Exception, "DID NOT RAISE"),
        },
    },
    "runs": {
        "a coroutine function is awaited": {
            "args": [{"args": [2], "returns": 2}, Target.async_class_method],
            "returns": None,
        },
        "an awaitable a sync function returns is awaited": {
            "args": [{"args": [2], "returns": 2}, lambda value: Target.async_class_method(value)],
            "returns": None,
        },
        "a generator is drained into a list": {
            "args": [{"args": [2], "returns": [2, 2]}, lambda value: (value for _ in range(2))],
            "returns": None,
        },
        "a generator that raises only once drained is still a raise": {
            "args": [
                {"args": [0], "raises": (ZeroDivisionError, "by zero")},
                lambda value: (1 // value for _ in range(1)),
            ],
            "returns": None,
        },
        "an object is compared by the attributes the case names": {
            "args": [{"kwargs": {"name": "one", "value": 2}, "attributes": {"value": 2}}, types.SimpleNamespace],
            "returns": None,
        },
        "a plain method patch is bound to the instance it is called on": {
            "args": [
                {
                    "args": [2],
                    "patches": [(Target, "instance_method", lambda self, value: (type(self).__name__, value))],
                    "returns": ("Target", 2),
                },
                lambda value: Target().instance_method(value),
            ],
            "returns": None,
        },
    },
}


def call(name: str, value: int) -> tuple:
    """Call one of `Target`'s methods through the class and through an instance, awaiting what a coroutine returns.

    Args:
        name: Which of `Target`'s methods to call.
        value: What to pass to the method.

    Returns:
        The class's result, then the instance's.
    """
    results = []
    for owner in (Target, Target()):
        result = getattr(owner, name)(value)
        results.append(asyncio.run(result) if asyncio.iscoroutine(result) else result)
    return tuple(results)


@pytest.mark.parametrize_tests(TESTS["unheld"])
def test_a_case_that_cannot_hold_as_declared_fails(test: dict, function_tester: Callable) -> None:
    """A case fails when its callable does other than it declares, or when what it declares cannot be checked.

    Mutation: drop the no-result check in `_initialize_test`. A case stating nothing to check is compared against
    `None`, so it passes on any callable returning `None`.
    """
    function_tester(test, function_tester)


@pytest.mark.parametrize_tests(TESTS["runs"])
def test_a_declared_case_runs_however_its_callable_returns(
    test: dict,
    monkeypatch: pytest.MonkeyPatch,
    function_tester: Callable,
) -> None:
    """A case is checked against what its callable finally produces, once awaited, drained or read by attribute.

    Mutation: drop `list(result)` under `raises` in `function_tester`. A generator that raises only once drained is
    never drained, so the raise the case declares never comes.
    """
    function_tester(test, functools.partial(function_tester, monkeypatch=monkeypatch))


@pytest.mark.parametrize_tests(TESTS["patches"])
def test_a_patch_takes_the_shape_of_the_method_it_replaces(
    test: dict,
    monkeypatch: pytest.MonkeyPatch,
    function_tester: Callable,
) -> None:
    """A plain callable patched over a class or static method is wrapped to match, so it is called the same way.

    Mutation: read the patched attribute with `getattr` alone in `_patch_test`. A class method reads as already bound
    rather than as a function, so its patch goes in unwrapped and the class is never passed.
    """
    function_tester(test, call, monkeypatch=monkeypatch)


@pytest.mark.parametrize_tests("value", [1, 2])
def test_the_marker_parametrizes_the_argument_it_names(value: int, request: pytest.FixtureRequest) -> None:
    """Named first, the marker parametrizes that argument, and a list's entries become their own case IDs.

    Mutation: always put `test` first in `pytest_generate_tests`. The named argument goes unfilled, and collection fails.
    """
    assert request.node.callspec.id == str(value)
