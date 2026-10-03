"""Cases for the shared test helpers in the root conftest."""

import asyncio
from collections.abc import Callable

import pytest


class Target:
    """A class whose methods a case patches, one of each kind."""

    @classmethod
    async def acm(cls, value: int) -> int:
        """Return the value, once awaited."""
        return value

    @classmethod
    def cm(cls, value: int) -> int:
        """Return the value."""
        return value

    @staticmethod
    def sm(value: int) -> int:
        """Return the value."""
        return value


TEST_CASES = {
    "patches": {
        "a class method patch receives the class, through the class and an instance alike": {
            "args": ["cm", 2],
            "patches": [(Target, "cm", lambda cls, value: (cls.__name__, value))],
            "returns": (("Target", 2), ("Target", 2)),
        },
        "an async class method patch is awaited and receives the class": {
            "args": ["acm", 2],
            "patches": [(Target, "acm", lambda cls, value: (cls.__name__, value))],
            "returns": (("Target", 2), ("Target", 2)),
        },
        "a static method patch stays static": {
            "args": ["sm", 2],
            "patches": [(Target, "sm", lambda value: value * 10)],
            "returns": (20, 20),
        },
    },
}


def call(name: str, value: int) -> tuple:
    """Call one of `Target`'s methods through the class and through an instance, awaiting what a coroutine returns."""
    results = []
    for owner in (Target, Target()):
        result = getattr(owner, name)(value)
        results.append(asyncio.run(result) if asyncio.iscoroutine(result) else result)
    return tuple(results)


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["patches"])
def test_a_patch_takes_the_shape_of_the_method_it_replaces(
    test_case: dict,
    monkeypatch: pytest.MonkeyPatch,
    function_tester: Callable,
) -> None:
    """A plain callable patched over a class or static method is wrapped to match, so it is called the same way.

    Mutation: read the patched attribute with `getattr` alone in `_patch_test`. A class method reads as already bound
    rather than as a function, so its patch goes in unwrapped and the class is never passed.
    """
    function_tester(test_case, call, monkeypatch=monkeypatch)
