"""Report private and property docstrings that declare a Google section the guide does not allow.

Dunders are not private, so a leading underscore is not the whole test.
"""

import argparse
import ast
import dataclasses
import pathlib
import sys
from typing import Any
from typing import override

# Whole lines, not text found anywhere. A docstring that mentions what a caller raises in a sentence is
# prose, and only a line that is exactly the header opens a section.
BANNED_SECTIONS = ("Args:", "Returns:", "Yields:", "Raises:")
DEFAULT_ROOTS = (".",)
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
PROPERTY_DECORATORS = frozenset({"cached_property", "functools.cached_property", "property"})

# Trees that hold Python this project does not own. Named beside the leading-dot test that keeps a root of `.` out
# of the virtualenv and every sibling worktree under `.claude`.
UNOWNED = frozenset({"__pycache__", "build", "dist"})


@dataclasses.dataclass(frozen=True)
class Finding:
    """One docstring that publishes a contract the guide does not want published."""

    path: pathlib.Path
    line: int
    name: str
    sections: tuple[str, ...]

    @override
    def __str__(self) -> str:
        # The column layout the other gates report in, so one parser reads every gate's output.
        declared = ", ".join(one.rstrip(":") for one in self.sections)
        return f"{self.path}:{self.line}:1: DSC001 `{self.name}` declares {declared}"


def _declared_sections(node: ast.AsyncFunctionDef | ast.FunctionDef) -> tuple[str, ...]:
    """The banned headers this definition's docstring opens, deduplicated, in the order written."""
    docstring = ast.get_docstring(node, clean=False) or ""
    opened = [line.strip() for line in docstring.splitlines() if line.strip() in BANNED_SECTIONS]
    return tuple(dict.fromkeys(opened))


def _is_covered(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Whether the guide holds this definition to a docstring with no sections in it."""
    if _is_tool(node):
        return False
    decorators = {ast.unparse(one) for one in node.decorator_list}
    accessor = bool(decorators & PROPERTY_DECORATORS) or any(
        one.endswith((".deleter", ".setter")) for one in decorators
    )
    name = node.name
    return accessor or (name.startswith("_") and not (name.startswith("__") and name.endswith("__")))


def _is_tool(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Whether a model reads this docstring, which makes its sections a contract rather than prose."""
    for decorator in node.decorator_list:
        called = decorator.func if isinstance(decorator, ast.Call) else decorator
        named = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
        if named == "tool":
            return True
    return False


def _print(*args: Any) -> None:
    """The one stdout sink this script reports through."""
    print(*args)  # no-qa


def _python_files(roots: list[str]) -> list[pathlib.Path]:
    """Every module under the roots, in a stable order."""
    found: list[pathlib.Path] = []
    for root in roots:
        path = pathlib.Path(root)
        if not path.exists():
            raise FileNotFoundError(f"no such root to check: {root}")
        if path.is_file():
            found.append(path)
        else:
            found.extend(
                one
                for one in sorted(path.rglob("*.py"))
                if not any(part.startswith(".") or part in UNOWNED for part in one.relative_to(path).parts)
            )
    return found


def main(argv: list[str] | None = None) -> int:
    """Report every covered docstring under the given roots that declares a banned section.

    Args:
        argv: The arguments to parse, or None to read `sys.argv`.

    Returns:
        0 when no covered docstring declares one, 1 otherwise, so a pipeline blocks on it.
    """
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__.splitlines()[0])
    parser.add_argument("roots", nargs="*", help=f"Folders to read. Defaults to {' '.join(DEFAULT_ROOTS)}.")
    args = parser.parse_args(argv)

    scanned = _python_files(args.roots or list(DEFAULT_ROOTS))
    findings: list[Finding] = []
    covered = 0
    for path in scanned:
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, FUNCTIONS) or not _is_covered(node):
                continue
            covered += 1
            sections = _declared_sections(node)
            if sections:
                findings.append(Finding(path=path, line=node.lineno, name=node.name, sections=sections))
    for finding in sorted(findings, key=lambda one: (str(one.path), one.line)):
        _print(finding)

    # Always say how much was read. A silent pass and a pass over nothing at all read the same.
    roots = ", ".join(args.roots or DEFAULT_ROOTS)
    summary = f"scanned {len(scanned)} file(s) under {roots}, {covered} private or property docstring(s)"
    _print(f"\n{summary}, {len(findings)} declaring a section" if findings else f"{summary}")
    return 1 if findings else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FileNotFoundError as error:
        sys.exit(f"check_pydocs: {error}")
