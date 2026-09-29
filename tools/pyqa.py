"""Check for custom, consistency, contracts that no linters read: definition order, docstrings, links, and more.

Each group is selected by its own flag, and a run naming none of them runs all three.

`--order` sorts a module's statements alphabetically, within the partial order a statement's own references impose.
    - `# order: skip` freezes a statement and splits the body around it.
    - `# order: skip-file` exempts the module, and reporting is the default.

`--fix` rewrites. `ORD001` is a definition out of place and `ORD002` an assignment.

`--links` reports every relative markdown link whose file or heading fragment does not resolve. A link into another
document rots without anything failing, so nothing else in this repository notices when a heading moves out from
under one. `LNK001` is the file that is not there, `LNK002` the heading.

`--docs` reports a docstring whose shape does not match the one the guide holds its definition to.

`DSC001` and `DSC002` read the Google sections, from intersecting directions.
    - `DSC001` is a private or property docstring declaring a section that belongs to a public contract.
    - `DSC002` is a definition stating a contract whose docstring omits a section its own shape requires.
      Parameters with no `Args`, a return value with no `Returns`, a yield with no `Yields`, a `raise` with no `Raises`.

Both read the definition's own name, so a public method of a private class is public to both. Dunders are not private,
so a leading underscore is not the whole test.

A protocol or abstract base states the contract for every implementation of it in the same module. The declaration
opens the sections, and each implementation carries a summary line alone, exempt because the contract is stated once
above it. `DSC002` reads the declaration and passes over the methods sharing its name.

`DSC003` reads the summary line instead, and refuses one holding more than one sentence: the second sentence belongs
in a body, or joined to the first. It reads a module, a class and a function, in a test module as much as anywhere,
and it does not read an attribute docstring.

`DSC004` is a header missing a section's spelling: a case, a singular, a letter dropped or transposed. The codes
above read one as the section it means, so `DSC002` does not also report that section as missing, and a private
docstring's stays with `DSC001`, where the remedy is to cut the section rather than to spell it.

`[tool.pyqa]` in `pyproject.toml` carries `ignore`, a deny list of these codes, read from the nearest `pyproject.toml`
at or above the working directory. A run defaults to every code on. A code named there is still scanned and counted,
never fails the run, and is named in the summary with what it swallowed, so a suppressed run does not read as a clean
one. A name that is no code this tool reports fails the run rather than being passed over.
"""

import argparse
import ast
import bisect
import collections
import dataclasses
import difflib
import pathlib
import re
import sys
import tomllib
from collections.abc import Iterator
from typing import Any
from typing import override

# Spelled the way docstrings spell them, and matched case-sensitively. A dot inside one of these never ends a sentence.
ABBREVIATIONS = frozenset({"N.B.", "cf.", "e.g.", "etc.", "i.e.", "vs."})

# A backticked span is prose about the syntax inside it rather than the thing itself: a link in one is an example, and
# a dot in one ends no sentence. Blanked before either is read, keeping the line's length so a reported column
# stays true.
CODE_SPAN = re.compile(r"`[^`]*`")

DEFAULT_ROOTS = (".",)

# Top-level names left where they are, as though each carried the skip comment. `main` is the one the
# convention already places by hand, either first or last over the `__main__` guard, and sorting it by
# name lands it in the middle of the helpers it calls.
DEFAULT_PINNED = ("main",)

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# What `DSC003` reads. An attribute docstring is a bare string following an assignment rather than a node's own,
# so no shape here reaches one.
DOCUMENTED = (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef, ast.Module)

# What a heading keeps on its way to a fragment: word characters, a hyphen, and the spaces that become hyphens.
# Everything else is dropped rather than replaced.
DROPPED = re.compile(r"[^\w\- ]")

# The two characters a fenced block may be drawn with. A fence closes only on the one it opened with, so a run
# of the other inside it is content.
FENCE_CHARS = frozenset("`~")

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")

# The emphasis, code and link wrappers a heading may carry, none of which reach its fragment.
MARKUP = re.compile(r"`([^`]*)`|\*\*([^*]*)\*\*|\*([^*]*)\*|_([^_]*)_|\[([^\]]*)\]\([^)]*\)")

# A class body reorders inside a module body, so an outer rewrite would overwrite an inner one. Each
# pass takes the outermost bodies and the next pass reaches one level further in.
MAX_PASSES = 10

# Rank within a module, then within a class body. A class body reads like a small module: its own
# constants, then its classes, then its functions, with `__init__` pulled to the front.
MODULE_CONSTANT, MODULE_CLASS, MODULE_FUNCTION = 0, 1, 2
CLASS_ATTRIBUTE, CLASS_NESTED, CLASS_INIT, CLASS_DUNDER, CLASS_METHOD = 0, 1, 2, 3, 4

# How close a header has to be to a section's name to read as that name misspelled. A case, a singular, or one
# letter dropped or transposed clears it; `Arguments:` and `Parameters:`, which are other conventions' names for
# `Args:` rather than misspellings of it, do not.
NEARNESS = 0.8

PROPERTY_DECORATORS = frozenset({"cached_property", "functools.cached_property", "property"})
RELATIVE = re.compile(r"\]\(([^)\s]+)\)")
REPORT_VERBS = {
    "DSC001": "declares",
    "DSC002": "omits",
    "DSC003": "runs on into",
    "DSC004": "misspells",
}

# The detailed rules behind each code, printed under a run that reported it, one line per line of output. This tool
# states its own rules so that its output is the whole reference, and no document has to carry them for it. Also the
# vocabulary `[tool.pyqa] ignore` is read against, so a name absent here is a name nothing would have suppressed.
RULES = {
    "DSC001": "A private or property docstring opens no section: a section states a public contract, and neither of these declares one. Cut the section.",
    "DSC002": "A definition stating a contract opens every section its own shape asks for: a parameter wants `Args`, a returned value `Returns`, a yield `Yields`, a raised error `Raises`. A protocol or abstract base states that contract for the implementations in its module, which carry a summary line alone, so the declaration is the one held to it.",
    "DSC003": "A summary line holds one sentence. A second belongs in the body below it, or joined to the first with a comma or semicolon.",
    "DSC004": "A section header is spelled the way the guide spells it: `Args`, `Returns`, `Yields`, `Raises`. A header near one of those reads as that section misspelled; another convention's name for it does not.",
    "LNK001": "A relative link resolves to a file this repository holds, read from the folder the linking document sits in. A URL, a `mailto:` address and a leading-slash route on the running service are none of this tool's to resolve.",
    "LNK002": "A link's `#fragment` names a heading the target document holds, slugged the way GitHub slugs one: lowered, spaces hyphenated, the rest of the punctuation dropped, and a repeat numbered from the second. A fragment with no path resolves against the document it is written in.",
    "ORD001": "A definition sits where a reader looking for it by name would look: alphabetical among its siblings, classes above functions, and `__init__` then the dunders at the top of a class body. What a statement reads at definition time still comes first. `# order: skip` freezes one statement and `# order: skip-file` exempts a module; `python tools/pyqa.py --order --fix` sorts the rest.",
    "ORD002": "An assignment is ranked above the definitions sharing its body and otherwise keeps the order someone chose for it, so the ones that move are below a definition, or below a statement that reads them. `python tools/pyqa.py --order --fix` sorts them.",
}

# Whole lines, not text found anywhere. A docstring that mentions what a caller raises in a sentence is superfluous,
# and only a line holding a header and nothing else opens a section.
SECTIONS = ("Args:", "Returns:", "Yields:", "Raises:")

# Keyed by the lowered word a header holds, since case is one of the ways a header goes wrong.
SECTION_WORDS = {one.removesuffix(":").lower(): one for one in SECTIONS}

# A period, then whitespace, then something that opens a sentence. The lookbehind is what makes an ellipsis unmatchable,
# since its last dot is preceded by one, and requiring whitespace is what lets a line-final period through. A decimal
# needs no rule of its own: nothing follows its dot but a digit.
SENTENCE_BREAK = re.compile(r"(?<!\.)\.\s+(?=[A-Z`\"'(\[])")

SKIP_FILE = "# order: skip-file"
SKIP_ITEM = "# order: skip"

# Scoped the way pylint's own `ignore` is, and for its reason: a test declares its cases through fixtures and names
# them, so holding one to `Args` would document the fixture list rather than a contract.
TEST_NAMES = frozenset({"conftest.py", "test"})

# A link target this repository does not own. A leading slash is a route on the running service.
UNCHECKED = ("http://", "https://", "mailto:", "/")

# Trees holding files this project does not own. Named beside the leading-dot test that keeps a root of `.` out of
# the virtualenv and every sibling worktree under `.claude`, which the fixer would otherwise rewrite.
UNOWNED = frozenset({"__pycache__", "build", "dist", "node_modules"})


@dataclasses.dataclass(frozen=True)
class Finding:
    """One thing a group read that the rule behind its code puts differently."""

    code: str
    path: pathlib.Path
    line: int
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


def _anchors(path: pathlib.Path) -> set[str]:
    """Every fragment this document's headings produce, a repeat numbered the way GitHub numbers it."""
    counts: dict[str, int] = {}
    for _, line in _outside_fences(path):
        match = HEADING.match(line)
        if not match:
            continue
        fragment = _slug(match.group(1))
        counts[fragment] = counts.get(fragment, 0) + 1
    return {one if repeat == 0 else f"{one}-{repeat}" for one, count in counts.items() for repeat in range(count)}


def _block_span(node: ast.stmt, lines: list[str]) -> tuple[int, int]:
    """The 0-based half-open line span of one statement, its comments on both sides included.

    A comment run under a statement belongs to it only when a blank line or the end of the file closes the run.
    """
    start = min([node.lineno, *(one.lineno for one in getattr(node, "decorator_list", []))]) - 1
    while start > 0 and lines[start - 1].strip().startswith("#"):
        start -= 1
    # `end_lineno` is Optional on `ast.AST` in general; a parsed statement always carries one, and its
    # own start line is the only span a statement without one could have.
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


def _check_docs(roots: list[str]) -> tuple[list[Finding], str]:
    """Every docstring under these roots that its own definition or its summary line contradicts."""
    scanned = _files(roots, ".py")
    findings: list[Finding] = []
    covered = 0
    contracted = 0
    summarized = 0
    for path in scanned:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, FUNCTIONS):
                continue

            # A misspelled header reads as the section it means, so a covered docstring's is reported by `DSC001`:
            # the remedy there is to cut the section rather than to spell it.
            if _is_covered(node):
                covered += 1
                sections, code = _declared_sections(node), "DSC001"
            else:
                sections, code = _misspelled_sections(node), "DSC004"
            if sections:
                findings.append(Finding(code, path, node.lineno, _docs_detail(code, node.name, sections)))

        # Above the test skip below, deliberately: what `DSC002` exempts a test module for is its fixtures standing
        # in for arguments, and a summary line holding two sentences has no such reason.
        read, overrun = _overruns(path, tree)
        summarized += read
        findings.extend(overrun)
        if _is_test(path):
            continue
        for node in _contracted(tree):
            contracted += 1
            if sections := _omitted_sections(node):
                findings.append(Finding("DSC002", path, node.lineno, _docs_detail("DSC002", node.name, sections)))
    findings.sort(key=lambda one: (str(one.path), one.line, one.code))
    read_docstrings = f"{covered} covered, {contracted} contracted and {summarized} summarized docstring(s)"
    return findings, f"{_scanned(scanned, roots)}, {read_docstrings}"


def _check_links(roots: list[str]) -> tuple[list[Finding], str]:
    """Every relative link under these roots that resolves to no file, or to no heading in one."""
    scanned = _files(roots, ".md")
    findings, checked = _unresolved(scanned, pathlib.Path(roots[0]))
    return findings, f"{_scanned(scanned, roots)}, {checked} relative link(s)"


def _check_order(roots: list[str], pinned: frozenset[str]) -> tuple[list[Finding], str]:
    """Every statement under these roots that the rule puts somewhere other than where it is."""
    scanned = _files(roots, ".py")
    findings: list[Finding] = []
    for path in scanned:
        findings.extend(_findings_for(path, path.read_text(), pinned))
    faulted = len({one.path for one in findings})
    return findings, f"{_scanned(scanned, roots)}, {faulted} out of order"


def _contracted(tree: ast.Module) -> list[ast.AsyncFunctionDef | ast.FunctionDef]:
    """Every definition in this module the guide holds to a full contract, closures apart."""
    declarations = _declarations(tree)
    declaring = {one.name for one in declarations}

    # The declarations are seeded rather than reached below, because what exempts an implementation is the name it
    # shares with its declaration, and the declaration shares it too. The loop passes over each of them on that name,
    # so this is what holds the one docstring stating the contract to the contract it states.
    found = [one for one in declarations if _is_contracted(one, declaring=True)]
    pending: list[tuple[ast.stmt, bool]] = [(one, False) for one in tree.body]
    while pending:
        node, method = pending.pop()
        if isinstance(node, ast.ClassDef):
            pending.extend((one, True) for one in node.body)
        elif isinstance(node, FUNCTIONS) and _is_contracted(node) and not (method and node.name in declaring):
            found.append(node)
    return sorted(found, key=lambda one: one.lineno)


def _declarations(tree: ast.Module) -> list[ast.AsyncFunctionDef | ast.FunctionDef]:
    """Every method a protocol or abstract base in this module declares the contract of."""
    found: list[ast.AsyncFunctionDef | ast.FunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {ast.unparse(one).split("[")[0] for one in node.bases}
        declaring = any(one.endswith(("ABC", "Protocol")) for one in bases)
        for inner in node.body:
            if not isinstance(inner, FUNCTIONS):
                continue
            abstract = any("abstractmethod" in ast.unparse(one) for one in inner.decorator_list)
            if abstract or (declaring and _is_stub(inner)):
                found.append(inner)
    return found


def _declared_sections(node: ast.AsyncFunctionDef | ast.FunctionDef) -> tuple[str, ...]:
    """The sections this definition's docstring opens however spelled, deduplicated, in the order written."""
    docstring = ast.get_docstring(node, clean=False) or ""
    opened = [_section_of(line.strip()) for line in docstring.splitlines()]
    return tuple(dict.fromkeys(one for one in opened if one))


def _defined_names(node: ast.stmt) -> frozenset[str]:
    """The names this statement binds in the body that holds it, empty when it binds none."""
    if isinstance(node, DEFINITIONS):
        return frozenset({node.name})
    return frozenset(target.id for target in _targets_of(node) if isinstance(target, ast.Name))


def _docs_detail(code: str, name: str, parts: tuple[str, ...]) -> str:
    """How a docstring finding reads: whose docstring, what it did, and to which sections or sentences."""
    listed = ", ".join(one.rstrip(":") for one in parts)
    return f"`{name}` {REPORT_VERBS[code]} {listed}"


def _docstrings(tree: ast.Module) -> Iterator[tuple[str, int, str]]:
    """Every module, class and function docstring under this tree, as its name, its own line and its text."""
    for node in ast.walk(tree):
        if not isinstance(node, DOCUMENTED):
            continue
        docstring = ast.get_docstring(node, clean=False)
        if docstring is None:
            continue
        # The docstring's line rather than the definition's, which a multi-line signature puts far above it.
        yield getattr(node, "name", "<module>"), node.body[0].lineno, docstring


def _files(roots: list[str], suffix: str) -> list[pathlib.Path]:
    """Every file of one suffix under the roots, in a stable order."""
    found: list[pathlib.Path] = []
    for root in roots:
        path = pathlib.Path(root)
        if not path.exists():
            raise FileNotFoundError(f"no such root to read: {root}")
        if path.is_file():
            found.append(path)
        else:
            found.extend(one for one in sorted(path.rglob(f"*{suffix}")) if not _is_vendored(one.relative_to(path)))
    return found


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
                        code="ORD001" if isinstance(moved.node, DEFINITIONS) else "ORD002",
                        path=path,
                        line=moved.node.lineno,
                        detail=f"`{moved.name}` belongs {after}",
                    ),
                )
    return sorted(findings, key=lambda finding: finding.line)


def _fix_order(roots: list[str], pinned: frozenset[str]) -> str:
    """Sort every module under these roots, and say how many of them moved."""
    scanned = _files(roots, ".py")
    rewritten = 0
    for path in scanned:
        source = path.read_text()
        output = _rewrite(source, pinned)
        if output != source:
            path.write_text(output)
            rewritten += 1
    return f"{_scanned(scanned, roots)}, sorted {rewritten}"


def _ignored() -> frozenset[str]:
    """The codes `[tool.pyqa] ignore` turns off, empty when the key, the section or the file is absent."""
    for folder in (pathlib.Path.cwd(), *pathlib.Path.cwd().parents):
        config = folder / "pyproject.toml"
        if config.exists():
            break
    else:
        return frozenset()
    declared = tomllib.loads(config.read_text()).get("tool", {}).get("pyqa", {}).get("ignore", [])
    if unknown := sorted(set(declared) - set(RULES)):
        # Refused rather than passed over, for the reason `useless-suppression` and `RUF100` are errors here: an
        # entry naming nothing reads as load-bearing while suppressing nothing at all.
        raise ValueError(f"`[tool.pyqa] ignore` in {config} names no code this tool reports: {', '.join(unknown)}")
    return frozenset(declared)


def _is_attribute_docstring(node: ast.stmt) -> bool:
    """Whether the statement is a bare string, which under an assignment documents it."""
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _is_contracted(node: ast.AsyncFunctionDef | ast.FunctionDef, declaring: bool = False) -> bool:
    """Whether the guide holds this definition to a docstring stating its whole contract.

    A declaring definition is the exception a stub body would otherwise exempt: a protocol's method body is a
    docstring and nothing else, and that docstring is where the contract for every implementation of it is written.
    """
    name = node.name
    # `__init__` is the one dunder the guide calls a public method. Every other one implements a language
    # protocol whose contract the language states, which is why `D105` is ignored rather than selected.
    dunder = name.startswith("__") and name.endswith("__") and name != "__init__"
    stated = ast.get_docstring(node) is not None and (declaring or not _is_stub(node))
    if _is_covered(node) or dunder or not stated:
        return False
    decorators = {ast.unparse(one) for one in node.decorator_list}
    return not any(one.endswith(("overload", "override")) for one in decorators)


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


def _is_stub(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Whether this definition declares a shape rather than implementing one."""
    body = [one for one in node.body if not (isinstance(one, ast.Expr) and isinstance(one.value, ast.Constant))]
    return not body or all(isinstance(one, ast.Pass) for one in body)


def _is_test(path: pathlib.Path) -> bool:
    """Whether this module holds cases rather than the code they read."""
    return bool(TEST_NAMES & set(path.parts)) or path.name.startswith("test_")


def _is_tool(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Whether a model reads this docstring, which makes its sections a contract rather than superfluous."""
    for decorator in node.decorator_list:
        called = decorator.func if isinstance(decorator, ast.Call) else decorator
        named = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
        if named == "tool":
            return True
    return False


def _is_vendored(path: pathlib.Path) -> bool:
    """Whether the path sits in a tree this project does not own and must not read or rewrite."""
    return any(part.startswith(".") or part in UNOWNED for part in path.parts)


def _leading_underscores(name: str) -> int:
    """How many underscores the name opens with, which breaks a tie the rest of the name cannot."""
    return len(name) - len(name.lstrip("_"))


def _masked(line: str) -> str:
    """The line with every dot that cannot end a sentence spelled over, at its original length."""
    # Length is preserved so a match's offsets still slice the line the reader wrote, backticks and all.
    masked = CODE_SPAN.sub(lambda match: f"`{'x' * (len(match.group()) - 2)}`", line)
    for abbreviation in ABBREVIATIONS:
        masked = masked.replace(abbreviation, "x" * len(abbreviation))
    return masked


def _misspelled_sections(node: ast.AsyncFunctionDef | ast.FunctionDef) -> tuple[str, ...]:
    """The sections this definition's docstring opens under a spelling that is not theirs, in the order written."""
    docstring = ast.get_docstring(node, clean=False) or ""
    lines = [line.strip() for line in docstring.splitlines()]
    opened = [_section_of(line) for line in lines if line not in SECTIONS]
    return tuple(dict.fromkeys(one for one in opened if one))


def _omitted_sections(node: ast.AsyncFunctionDef | ast.FunctionDef) -> tuple[str, ...]:
    """The headers this definition's shape asks for and its docstring does not open."""
    declared = set(_declared_sections(node))
    owned = list(_owned(node))
    yields = any(isinstance(one, (ast.Yield, ast.YieldFrom)) for one in owned)
    annotation = ast.unparse(node.returns) if node.returns else "None"
    arguments = node.args
    named = arguments.posonlyargs + arguments.args + arguments.kwonlyargs
    wanted = {
        "Args:": bool([one for one in named if one.arg not in {"cls", "self"}] or arguments.kwarg or arguments.vararg),
        # A generator's annotation is the iterator it hands back rather than a value it returns.
        "Returns:": not yields and annotation not in {"None", "NoReturn"},
        "Yields:": yields,
        # A bare `raise` re-raises what is already propagating, which the frame below it declared.
        "Raises:": any(isinstance(one, ast.Raise) and one.exc for one in owned),
    }
    return tuple(one for one in SECTIONS if wanted[one] and one not in declared)


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


def _outside_fences(path: pathlib.Path) -> Iterator[tuple[int, str]]:
    """Each numbered line of one document that a fenced block does not hold."""
    marker = ""
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.lstrip()
        char = stripped[:1]
        run = len(stripped) - len(stripped.lstrip(char)) if char in FENCE_CHARS else 0

        # A fence closes only on its own character, on a marker at least as long as the one that opened it,
        # and with nothing else on the line; anything else is content, not a boundary.
        closing = bool(marker) and char == marker[:1] and run >= len(marker) and not stripped[run:].strip()
        if run >= 3 and (closing or not marker):
            marker = "" if closing else char * run
            continue
        if not marker:
            yield number, raw


def _overruns(path: pathlib.Path, tree: ast.Module) -> tuple[int, list[Finding]]:
    """How many summary lines this tree holds, and a finding for each one running past a single sentence."""
    found: list[Finding] = []
    read = 0
    for name, line, docstring in _docstrings(tree):
        read += 1
        if len(sentences := _sentences(docstring)) > 1:
            found.append(Finding("DSC003", path, line, _docs_detail("DSC003", name, sentences[1:])))
    return read, found


def _owned(node: ast.AsyncFunctionDef | ast.FunctionDef) -> Iterator[ast.AST]:
    """Every node this definition's own body reaches, stopping at each definition nested inside it."""
    pending: list[ast.AST] = list(ast.iter_child_nodes(node))
    while pending:
        one = pending.pop()
        if isinstance(one, (*FUNCTIONS, ast.Lambda)):
            continue
        yield one
        pending.extend(ast.iter_child_nodes(one))


def _print(*args: Any) -> None:
    """The one stdout sink this script reports through."""
    print(*args)  # no-qa


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


def _report(results: list[tuple[list[Finding], str]], ignored: frozenset[str]) -> int:
    """Print what each group found and how much it read, and answer whether anything is left to fail on."""
    counted: collections.Counter[str] = collections.Counter()
    suppressed: collections.Counter[str] = collections.Counter()
    for position, (found, summary) in enumerate(results):
        kept = [one for one in found if one.code not in ignored]
        counted.update(one.code for one in kept)
        suppressed.update(one.code for one in found if one.code in ignored)
        if position:
            _print("")
        for finding in kept:
            _print(finding)
        # Always say how much was read. A silent pass and a pass over nothing at all read the same.
        _print(f"\n{summary}" if kept else summary)
    if counted:
        _print(f"\nreported {', '.join(f'{count} {code}' for code, count in sorted(counted.items()))}")
    if ignored:
        swallowed = f"{sum(suppressed.values())} finding(s) suppressed"
        _print(f"\nignoring {', '.join(sorted(ignored))} by `[tool.pyqa] ignore`, {swallowed}")
    for code in sorted(counted):
        _print(f"\n{code}  {RULES[code]}")
    return 1 if counted else 0


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

                # The import block wants one blank line under it before a constant and two before a
                # definition, so a run that hoists a constant to the top of the module has to restate
                # the gap the import block above it requires.
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


def _scanned(paths: list[pathlib.Path], roots: list[str]) -> str:
    """How much one group read, which is what tells a pass over nothing from a pass."""
    return f"scanned {len(paths)} file(s) under {', '.join(roots)}"


def _section_of(line: str) -> str | None:
    """The section this docstring line opens, spelled the way the guide spells it or near enough to read as it."""
    if not line.endswith(":") or not (word := line.removesuffix(":").strip()).isalpha():
        return None
    matched = difflib.get_close_matches(word.lower(), tuple(SECTION_WORDS), n=1, cutoff=NEARNESS)
    return SECTION_WORDS[matched[0]] if matched else None


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


def _sentences(docstring: str) -> tuple[str, ...]:
    """A docstring's summary line, split into the sentences it holds."""
    # Stripped first: a docstring opening on its own line carries the indentation of the one below it.
    line = docstring.strip().split("\n", 1)[0].rstrip()
    probe = _masked(line)
    found: list[str] = []
    opens = 0
    for match in SENTENCE_BREAK.finditer(probe):
        found.append(line[opens : match.start() + 1])
        opens = match.end()
    found.append(line[opens:])
    return tuple(one for one in found if one)


def _slug(text: str) -> str:
    """The fragment one heading is served under, stripped of markup and punctuation."""
    bare = MARKUP.sub(lambda match: next(one for one in match.groups() if one is not None), text)
    return DROPPED.sub("", bare.lower()).replace(" ", "-")


def _sort_key(node: ast.stmt, index: int, in_class: bool) -> tuple:
    """Where the guide puts this statement among its siblings, ties broken by where it already is.

    Constants and class attributes are ranked but not sorted, so they keep the order someone chose for them.
    """
    if not isinstance(node, DEFINITIONS):
        return CLASS_ATTRIBUTE if in_class else MODULE_CONSTANT, index
    name = node.name
    # Whether the name sorts as written is decided here, beside the rank, and never read back off the
    # rank. The two scales number from zero independently, so `MODULE_FUNCTION` and `CLASS_INIT` are
    # both 2, and asking a rank whether it is a dunder would answer yes for every module-level function.
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


def _unresolved(paths: list[pathlib.Path], root: pathlib.Path) -> tuple[list[Finding], int]:
    """Every relative link in these documents that does not resolve, and how many were read."""
    known: dict[pathlib.Path, set[str]] = {}
    findings: list[Finding] = []
    checked = 0
    for path in paths:
        reported = path.relative_to(root) if path.is_relative_to(root) else path
        for number, raw in _outside_fences(path):
            # A link in a code span is prose about link syntax, as one in a fence is an example.
            blanked = CODE_SPAN.sub(lambda hit: " " * len(hit.group()), raw)
            for match in RELATIVE.finditer(blanked):
                target = match.group(1)
                if target.startswith(UNCHECKED):
                    continue
                name, _, fragment = target.partition("#")
                checked += 1
                resolved = (path.parent / name).resolve() if name else path
                if not resolved.exists():
                    findings.append(Finding("LNK001", reported, number, f"no such file `{name}`"))
                    continue
                if not fragment or resolved.suffix != ".md":
                    continue
                if resolved not in known:
                    known[resolved] = _anchors(resolved)
                if fragment not in known[resolved]:
                    findings.append(
                        Finding("LNK002", reported, number, f"no heading `#{fragment}` in `{name or path.name}`"),
                    )
    return findings, checked


def main(argv: list[str] | None = None) -> int:
    """Report what every enabled group finds under the given roots.

    Args:
        argv: The arguments to parse, or None to read `sys.argv`.

    Returns:
        0 when every group passes, or `--fix` put the order right, else 1, so a pipeline gates on it.
    """
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__.splitlines()[0])
    parser.add_argument("roots", nargs="*", help=f"Folders to read. Defaults to {' '.join(DEFAULT_ROOTS)}.")
    parser.add_argument("--docs", action="store_true", help="Check docstring shape. On when no group is named.")
    parser.add_argument("--links", action="store_true", help="Check document links. On when no group is named.")
    parser.add_argument("--order", action="store_true", help="Check definition order. On when no group is named.")
    parser.add_argument("--fix", action="store_true", help="Sort what `--order` reports, rather than reporting it.")
    parser.add_argument(
        "--pin",
        action="append",
        metavar="NAME",
        help=f"Top-level name `--order` leaves alone. Repeatable, and replaces the default: {' '.join(DEFAULT_PINNED)}.",
    )
    args = parser.parse_args(argv)

    every = not (args.docs or args.links or args.order)
    if args.fix and not (every or args.order):
        parser.error("--fix sorts what --order reports, so it cannot be asked for without it")
    roots = args.roots or list(DEFAULT_ROOTS)
    pinned = frozenset(DEFAULT_PINNED if args.pin is None else args.pin)
    results: list[tuple[list[Finding], str]] = []
    if every or args.docs:
        results.append(_check_docs(roots))
    if every or args.links:
        results.append(_check_links(roots))
    if every or args.order:
        results.append(([], _fix_order(roots, pinned)) if args.fix else _check_order(roots, pinned))
    return _report(results, _ignored())


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FileNotFoundError, ValueError) as error:
        sys.exit(f"pyqa: {error}")
