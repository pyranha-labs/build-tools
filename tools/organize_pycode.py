"""Sort a module's statements into a consistent order, or report where it is wrong.

Alphabetical within the partial order a statement's own references impose. `# order: skip` freezes a statement
and splits the body around it, `# order: skip-file` exempts the module, and reporting is the default; `--fix` rewrites.
"""

import argparse
import ast
import bisect
import dataclasses
import pathlib
import sys
from typing import Any
from typing import override

DEFAULT_ROOTS = (".",)
DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
SKIP_FILE = "# order: skip-file"
SKIP_ITEM = "# order: skip"

# Top-level names left where they are, as though each carried the skip comment. `main` is the one the
# convention already places by hand, either first or last over the `__main__` guard, and sorting it by
# name lands it in the middle of the helpers it calls.
DEFAULT_PINNED = ("main",)

# Trees that hold Python this project does not own. Named beside the leading-dot test that keeps a root of `.` out
# of the virtualenv and every sibling worktree under `.claude`, which the fixer would otherwise rewrite.
UNOWNED = frozenset({"__pycache__", "build", "dist"})

# A class body reorders inside a module body, so an outer rewrite would overwrite an inner one. Each pass takes
# the outermost bodies and the next pass reaches one level further in.
MAX_PASSES = 10

# Rank within a module, then within a class body. A class body reads like a small module: its own constants,
# then its classes, then its functions, with `__init__` pulled to the front.
MODULE_CONSTANT, MODULE_CLASS, MODULE_FUNCTION = 0, 1, 2
CLASS_ATTRIBUTE, CLASS_NESTED, CLASS_INIT, CLASS_DUNDER, CLASS_METHOD = 0, 1, 2, 3, 4


@dataclasses.dataclass(frozen=True)
class Finding:
    """One statement the file does not place where the rule puts it."""

    path: pathlib.Path
    line: int
    code: str
    detail: str

    @override
    def __str__(self) -> str:
        # The column layout the other gates report in, so one parser reads every gate's output.
        return f"{self.path}:{self.line}:1: {self.code} {self.detail}"


@dataclasses.dataclass(frozen=True)
class Item:
    """One statement of a body, with the span of source text that travels when it moves."""

    node: ast.stmt
    index: int
    start: int
    end: int
    key: tuple
    name: str
    provides: frozenset[str]
    requires: frozenset[str]


def _block_span(node: ast.stmt, lines: list[str]) -> tuple[int, int]:
    """The 0-based half-open line span of one statement, its comments on both sides included.

    A comment run under a statement belongs to it only when a blank line or the end of the file closes the run.
    """
    start = min([node.lineno, *(one.lineno for one in getattr(node, "decorator_list", []))]) - 1
    while start > 0 and lines[start - 1].strip().startswith("#"):
        start -= 1

    # `end_lineno` is Optional on `ast.AST` in general; a parsed statement always carries one, and its own start line
    # is the only span a statement without one could have.
    end = trailing = node.end_lineno or node.lineno
    while trailing < len(lines) and lines[trailing].strip().startswith("#"):
        trailing += 1
    if trailing > end and (trailing >= len(lines) or not lines[trailing].strip()):
        end = trailing
    return start, end


def _block_spans(body: list[ast.stmt], lines: list[str]) -> dict[int, tuple[int, int]]:
    """The span of every statement in one body, keyed by identity.

    An attribute docstring folds into the span of the assignment above, so it is not left behind as a barrier.
    """
    spans: dict[int, tuple[int, int]] = {}
    absorbed: set[int] = set()
    for index, node in enumerate(body):
        if id(node) in absorbed:
            continue
        start, end = _block_span(node, lines)
        following = body[index + 1] if index + 1 < len(body) else None
        if following is not None and not isinstance(node, DEFINITIONS) and _is_attribute_docstring(following):
            absorbed.add(id(following))
            end = following.end_lineno or following.lineno
        spans[id(node)] = (start, end)
    return spans


def _bodies_of(tree: ast.Module) -> list[tuple[list[ast.stmt], bool]]:
    """Every body that gets ordered, each flagged as a class body or the module's own."""
    bodies: list[tuple[list[ast.stmt], bool]] = [(tree.body, False)]
    bodies.extend((node.body, True) for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    return bodies


def _body_items(segment: list[ast.stmt], spans: dict[int, tuple[int, int]], in_class: bool) -> list[Item]:
    """Describe one run of movable statements, everything the sort and the rewrite need."""
    items: list[Item] = []
    for index, node in enumerate(segment):
        start, end = spans[id(node)]
        provides = _defined_names(node)
        items.append(
            Item(
                node=node,
                index=index,
                start=start,
                end=end,
                key=_sort_key(node, index, in_class),
                name=getattr(node, "name", None) or ", ".join(sorted(provides)) or "statement",
                provides=provides,
                requires=_referenced_names(node),
            ),
        )
    return items


def _defined_names(node: ast.stmt) -> frozenset[str]:
    """The names this statement binds in the body that holds it, empty when it binds none."""
    if isinstance(node, DEFINITIONS):
        return frozenset({node.name})
    return frozenset(target.id for target in _targets_of(node) if isinstance(target, ast.Name))


def _findings_for(path: pathlib.Path, source: str, pinned: frozenset[str]) -> list[Finding]:
    """Report the fewest statements whose move would put the file in order, at their current lines."""
    if any(line.strip() == SKIP_FILE for line in source.splitlines()):
        return []
    lines = source.splitlines(keepends=True)
    findings: list[Finding] = []
    for body, in_class in _bodies_of(ast.parse(source)):
        spans = _block_spans(body, lines)
        for segment in _segments(body, lines, spans, pinned, in_class):
            items = _body_items(segment, spans, in_class)
            order = _ordered(items)
            if order == list(range(len(items))):
                continue
            keeping = _stable_positions(order)
            for position, index in enumerate(order):
                if index in keeping:
                    continue
                moved = items[index]
                after = f"after `{items[order[position - 1]].name}`" if position else "first"
                findings.append(
                    Finding(
                        path=path,
                        line=moved.node.lineno,
                        code="ORD001" if isinstance(moved.node, DEFINITIONS) else "ORD002",
                        detail=f"`{moved.name}` belongs {after}",
                    ),
                )
    return sorted(findings, key=lambda finding: finding.line)


def _is_attribute_docstring(node: ast.stmt) -> bool:
    """Whether the statement is a bare string, which under an assignment documents it."""
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _is_faithful(source: str, rewritten: str) -> bool:
    """Whether the rewrite holds exactly the lines the original held, and is still a Python module."""
    kept = sorted(one for one in source.splitlines() if one.strip()) == sorted(
        one for one in rewritten.splitlines() if one.strip()
    )
    parses = True
    try:
        ast.parse(rewritten)
    except SyntaxError:
        parses = False
    return kept and parses


def _is_movable(node: ast.stmt) -> bool:
    """Whether a statement can be reordered, or has to stay put and split the body around it.

    Anything side-effecting or mutating stays, since moving it changes what runs when.
    """
    if isinstance(node, DEFINITIONS):
        return True
    targets = _targets_of(node)
    return bool(targets) and all(isinstance(target, ast.Name) for target in targets)


def _is_pinned(node: ast.stmt, lines: list[str], pinned: frozenset[str], in_class: bool) -> bool:
    """Whether the statement is frozen in place, by its own comment or by carrying a pinned name."""
    if not in_class and getattr(node, "name", None) in pinned:
        return True
    start, _ = _block_span(node, lines)
    return any(lines[number].strip() == SKIP_ITEM for number in range(start, node.lineno))


def _is_vendored(path: pathlib.Path) -> bool:
    """Whether the path sits in a tree this project does not own and must not rewrite."""
    return any(part.startswith(".") or part in UNOWNED for part in path.parts)


def _leading_underscores(name: str) -> int:
    """How many underscores the name opens with, which breaks a tie the rest of the name cannot."""
    return len(name) - len(name.lstrip("_"))


def _ordered(items: list[Item]) -> list[int]:
    """The positions the items belong in: the style order, narrowed by what each one needs defined.

    A cycle cannot be ordered at all, so a body holding one is returned exactly as it was.
    """
    producer: dict[str, int] = {}
    for item in items:
        for name in item.provides:
            producer.setdefault(name, item.index)

    dependents: list[set[int]] = [set() for _ in items]
    blocking = [0] * len(items)
    for item in items:
        for name in item.requires:
            source = producer.get(name)
            if source is None or source == item.index or item.index in dependents[source]:
                continue
            dependents[source].add(item.index)
            blocking[item.index] += 1

    ready = sorted((items[index].key, index) for index in range(len(items)) if not blocking[index])
    order: list[int] = []
    while ready:
        _, index = ready.pop(0)
        order.append(index)
        for dependent in sorted(dependents[index]):
            blocking[dependent] -= 1
            if not blocking[dependent]:
                bisect.insort(ready, (items[dependent].key, dependent))
    return order if len(order) == len(items) else list(range(len(items)))


def _print(*args: Any) -> None:
    """The one stdout sink this script reports through."""
    print(*args)  # no-qa


def _python_files(roots: list[str]) -> list[pathlib.Path]:
    """Every module under the roots, in a stable order."""
    found: list[pathlib.Path] = []
    for root in roots:
        path = pathlib.Path(root)
        if not path.exists():
            raise FileNotFoundError(f"no such root to order: {root}")
        if path.is_file():
            found.append(path)
        else:
            found.extend(one for one in sorted(path.rglob("*.py")) if not _is_vendored(one.relative_to(path)))
    return found


def _referenced_names(node: ast.stmt) -> frozenset[str]:
    """The names this statement reads the moment it executes, before anything calls into it.

    A class body counts, its annotations included, since pydantic resolves those while it builds the model.
    """
    sources: list[ast.AST] = list(getattr(node, "decorator_list", []))
    if isinstance(node, ast.ClassDef):
        sources.extend(node.bases)
        sources.extend(keyword.value for keyword in node.keywords)
        sources.extend(child for child in node.body if not isinstance(child, DEFINITIONS))
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        arguments = node.args
        sources.extend(one for one in (*arguments.defaults, *arguments.kw_defaults) if one is not None)
    else:
        sources.append(node)
    names: set[str] = set()
    for source in sources:
        for child in ast.walk(source):
            if isinstance(child, ast.Name):
                names.add(child.id)
    return frozenset(names)


def _rewrite(source: str, pinned: frozenset[str]) -> str:
    """Sort every body in one module, or return it untouched when the result cannot be trusted."""
    if any(line.strip() == SKIP_FILE for line in source.splitlines()):
        return source

    current = source
    for _ in range(MAX_PASSES):
        lines = current.splitlines(keepends=True)
        edits: list[tuple[int, int, str]] = []
        for body, in_class in _bodies_of(ast.parse(current)):
            spans = _block_spans(body, lines)
            placement = {id(node): position for position, node in enumerate(body)}
            for segment in _segments(body, lines, spans, pinned, in_class):
                items = _body_items(segment, spans, in_class)
                order = _ordered(items)
                if order == list(range(len(items))):
                    continue
                start = items[0].start
                text = _stitch(items, order, lines, in_class)

                # The import block wants one blank line under it before a constant and two before a definition,
                # so a run that hoists a constant to the top of the module has to restate the gap the import
                # block above it requires.
                above = placement[id(segment[0])] - 1
                if not in_class and above >= 0 and isinstance(body[above], (ast.Import, ast.ImportFrom)):
                    while start and not lines[start - 1].strip():
                        start -= 1
                    text = "\n" * (2 if isinstance(items[order[0]].node, DEFINITIONS) else 1) + text
                edits.append((start, items[-1].end, text))
        if not edits:
            break

        # Outermost first, and nothing that overlaps what was already taken. A class body nested in a
        # sorted run would otherwise be rebuilt from lines the outer edit has just replaced.
        edits.sort(key=lambda edited: (edited[0], -edited[1]))
        outermost: list[tuple[int, int, str]] = []
        reach = -1
        for edit in edits:
            if edit[0] >= reach:
                outermost.append(edit)
                reach = edit[1]
        output = list(lines)
        for start, end, text in reversed(outermost):
            output[start:end] = [text]
        current = "".join(output)

    return current if _is_faithful(source, current) else source


def _segments(
    body: list[ast.stmt],
    lines: list[str],
    spans: dict[int, tuple[int, int]],
    pinned: frozenset[str],
    in_class: bool,
) -> list[list[ast.stmt]]:
    """The runs of movable statements, split wherever something has to stay where it is."""
    runs: list[list[ast.stmt]] = []
    current: list[ast.stmt] = []
    for node in body:
        if id(node) not in spans:
            continue
        if _is_movable(node) and not _is_pinned(node, lines, pinned, in_class):
            current.append(node)
            continue
        if len(current) > 1:
            runs.append(current)
        current = []
    if len(current) > 1:
        runs.append(current)
    return runs


def _sort_key(node: ast.stmt, index: int, in_class: bool) -> tuple:
    """Where the guide puts this statement among its siblings, ties broken by where it already is.

    Constants and class attributes are ranked but not sorted, so they keep the order someone chose for them.
    """
    if not isinstance(node, DEFINITIONS):
        return CLASS_ATTRIBUTE if in_class else MODULE_CONSTANT, index
    name = node.name

    # Whether the name sorts as written is decided here, beside the rank, and never read back off the rank.
    # The two scales number from zero independently, so `MODULE_FUNCTION` and `CLASS_INIT` are both 2,
    # and asking a rank whether it is a dunder would answer yes for every module-level function.
    verbatim = False
    if not in_class:
        rank = MODULE_CLASS if isinstance(node, ast.ClassDef) else MODULE_FUNCTION
    elif isinstance(node, ast.ClassDef):
        rank = CLASS_NESTED
    elif name == "__init__":
        rank, verbatim = CLASS_INIT, True
    elif name.startswith("__") and name.endswith("__"):
        rank, verbatim = CLASS_DUNDER, True
    else:
        rank = CLASS_METHOD

    # A dunder sorts on the name it is written with. Everything else discards the leading underscores,
    # so a private name sits beside its public twin rather than in a block of its own, and takes the tie.
    ordering = (name, 0) if verbatim else (name.lstrip("_"), -_leading_underscores(name))
    return rank, *ordering, index


def _stable_positions(order: list[int]) -> set[int]:
    """The items that can stay where they are: the longest run already in the right relative order."""
    tails: list[int] = []
    trace: list[int] = []
    parents = [-1] * len(order)
    for position, value in enumerate(order):
        slot = bisect.bisect_left(tails, value)
        parents[position] = trace[slot - 1] if slot else -1
        if slot == len(tails):
            tails.append(value)
            trace.append(position)
        else:
            tails[slot] = value
            trace[slot] = position
    keeping: set[int] = set()
    node = trace[-1] if trace else -1
    while node != -1:
        keeping.add(order[node])
        node = parents[node]
    return keeping


def _stitch(items: list[Item], order: list[int], lines: list[str], in_class: bool) -> str:
    """Rebuild one run in the new order, spaced the way the formatter would leave it.

    Two statements that were neighbors keep the gap they had, so a block of related constants moves as a unit.
    """
    blocks = ["".join(lines[item.start : item.end]).rstrip("\n") for item in items]
    pieces: list[str] = []
    for position, index in enumerate(order):
        if position:
            previous = order[position - 1]
            spaced = any(isinstance(items[one].node, DEFINITIONS) for one in (index, previous))
            if index == previous + 1:
                gap = items[index].start - items[previous].end
            elif spaced:
                gap = 1 if in_class else 2
            else:
                gap = 0 if in_class else 1
            pieces.append("\n" * (gap + 1))
        pieces.append(blocks[index])
    return "".join(pieces) + "\n"


def _targets_of(node: ast.stmt) -> list[ast.expr]:
    """What an assignment assigns to, empty for anything that is not one."""
    if isinstance(node, ast.Assign):
        return list(node.targets)
    return [node.target] if isinstance(node, ast.AnnAssign) else []


def main(argv: list[str] | None = None) -> int:
    """Order every module under the given roots, or report the ones that are not ordered.

    Args:
        argv: The arguments to parse, or None to read `sys.argv`.

    Returns:
        0 when every file is in order, or `--fix` put it there, else 1, so a pipeline gates on it like the other checks.
    """
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__.splitlines()[0])
    parser.add_argument("roots", nargs="*", help=f"Folders to read. Defaults to {' '.join(DEFAULT_ROOTS)}.")
    parser.add_argument("--fix", action="store_true", help="Sort the files, rather than reporting what is unsorted.")
    parser.add_argument(
        "--pin",
        action="append",
        metavar="NAME",
        help=f"Top-level name to leave where it is. Repeatable, and replaces the default: {' '.join(DEFAULT_PINNED)}.",
    )
    args = parser.parse_args(argv)
    pinned = frozenset(DEFAULT_PINNED if args.pin is None else args.pin)

    scanned = _python_files(args.roots or list(DEFAULT_ROOTS))
    findings: list[Finding] = []
    rewritten = 0
    for path in scanned:
        source = path.read_text()
        if not args.fix:
            findings.extend(_findings_for(path, source, pinned))
            continue
        output = _rewrite(source, pinned)
        if output != source:
            path.write_text(output)
            rewritten += 1

    # Always say how many were read. A silent pass and a pass over nothing at all read the same, and a
    # root that resolves to no files is easy to produce by accident.
    if args.fix:
        _print(f"scanned {len(scanned)} file(s) under {', '.join(args.roots or DEFAULT_ROOTS)}, sorted {rewritten}")
        return 0
    for finding in findings:
        _print(finding)
    faulted = len({finding.path for finding in findings})
    summary = f"scanned {len(scanned)} file(s) under {', '.join(args.roots or DEFAULT_ROOTS)}, {faulted} out of order"
    _print(f"\n{summary}" if findings else summary)
    if findings:
        _print(f"{len(findings)} statement(s) to move. Run `python tools/organize_pycode.py --fix` to sort them.")
    return 1 if findings else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FileNotFoundError as error:
        sys.exit(f"organize_pycode: {error}")
