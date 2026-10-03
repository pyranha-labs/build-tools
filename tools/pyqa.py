"""Check custom consistency contracts that no linter reads: definition order, docstrings, links, and more.

Each group is selected by its own flag, and a run naming none of them runs every one. `RULES` states the rule behind
each code, and a run prints it under every code it reported.

`--order` reports by default. `--fix` rewrites before any other group reads, so the run reports on the sorted files.
    - `# pyqa: disable=order-alpha` on a definition's `def` or `class` line, or `order-assignment` on an assignment's
      first line, freezes it and splits the body around it.
    - `# pyqa: disable-file=order-alpha, order-assignment` freezes every definition and assignment, which leaves the
      module as written, and `MRK001` names whichever of the two the module does not need.

`--debugging` matches a call rather than text, so a string or a comment naming one is not one.

`--docs` reads a definition's own name, so a public method of a private class is public, and a dunder is not private.
`DSC002` passes over a test module, and `DSC003` reads every module, class and function docstring but no attribute
docstring. A header `DSC004` reads as misspelled counts as the section it means, so `DSC002` does not report it missing.

Every code has a name saying what it reports, such as `debug-print` for `DBG001` or `order-alpha` for `ORD001`. A
report prints it after the code, as pylint prints its own, and a marker, `[tool.pyqa] select` or `ignore` takes either.

`# pyqa: disable=` exempts a finding on the line its comment sits on, and `# pyqa: disable-file=` every one in the
module, from wherever in it the comment sits. Each lists codes or names separated by commas, read from comment tokens,
and the list ends at the first text that is not one more entry, so comments or a ruff `noqa` may follow it.

`[tool.pyqa]` in `pyproject.toml` carries `select` and `ignore`, read from the nearest `pyproject.toml` at or above the
working directory. Each lists selectors: a code, its name, or a prefix of codes such as `DSC`. A code is on when the
most specific selector matching it comes from `select`, a code or a name being more specific than any prefix and a
longer prefix more than a shorter one, and `ignore` winning a tie; without `select`, every code starts selected.
`exclude` lists paths and globs kept out of every group and out of `--fix`, beside the hidden and `UNOWNED` trees that
are always kept out. A pattern with no `/` matches a file or directory name at any depth, and one with a `/` a path
from the `pyproject.toml`'s folder, a leading `./` or a trailing `/` dropped.
"""

# The tool ships as the one file, so it grows past the module limit rather than splitting into a package.
# pylint: disable=too-many-lines

import argparse
import ast
import bisect
import builtins
import collections
import dataclasses
import difflib
import fnmatch
import io
import itertools
import pathlib
import re
import tokenize
import tomllib
from collections.abc import Callable
from collections.abc import Iterator
from typing import Any
from typing import override

# Spelled the way docstrings spell them, and matched case-sensitively. A dot inside one of these never ends a sentence.
ABBREVIATIONS = frozenset({"N.B.", "cf.", "e.g.", "etc.", "i.e.", "vs."})

# The names every module reads without binding them. A read of one is never a forward reference to a later binding,
# so a later statement rebinding it stays below the read.
BUILTIN_NAMES = frozenset(dir(builtins))

# A backticked span is prose about the syntax inside it rather than the thing itself: a link in one is an example, and
# a dot in one ends no sentence. Blanked before either is read, keeping the line's length so a reported column
# stays true.
CODE_SPAN = re.compile(r"`[^`]*`")

# The keys `[tool.pyqa]` takes. Any other is refused, so a misspelled key cannot pass for one that does something.
CONFIG_KEYS = frozenset({"exclude", "ignore", "select"})

# The callees the debugging group reports, each with its code, spelled the way `ast.unparse` spells a call's function,
# so a method sharing a name such as `self.print` is not one. A print is output left in; the others stop a debugger.
DEBUG_CALLS = {"breakpoint": "DBG002", "pdb.set_trace": "DBG002", "print": "DBG001"}

DEFAULT_ROOTS = (".",)

# Top-level names left where they are, as though an order marker froze each. `main` is the one the
# convention already places by hand, either first or last over the `__main__` guard, and sorting it by
# name lands it in the middle of the helpers it calls.
DEFAULT_PINNED = ("main", "parse_args")

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# What `DSC003` reads. An attribute docstring is a bare string following an assignment rather than a node's own,
# so no shape here reaches one.
DOCUMENTED = (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef, ast.Module)

# What a heading keeps on its way to a fragment: word characters, a hyphen, and the spaces that become hyphens.
# Everything else is dropped rather than replaced.
DROPPED = re.compile(r"[^\w\- ]")

# One marker entry in use: the file its comment sits in, the comment's line, and the code the entry disables.
Exemption = tuple[pathlib.Path, int, str]

# The two characters a fenced block may be drawn with. A fence closes only on the one it opened with, so a run
# of the other inside it is content.
FENCE_CHARS = frozenset("`~")

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")

# A pyqa marker: `disable=` for the line its comment sits on, `disable-file=` for the module, then codes or names
# separated by commas. The list ends at the first text that is not one more entry, so prose may follow it. Matched in
# a comment token rather than a raw line, so the same text inside a string literal is no marker.
MARKER = re.compile(r"#\s*pyqa:\s*(disable|disable-file)=\s*([\w-]+(?:\s*,\s*[\w-]+)*)")

# The emphasis, code and link wrappers a heading may carry, none of which reach its fragment.
MARKUP = re.compile(r"`([^`]*)`|\*\*([^*]*)\*\*|\*([^*]*)\*|_([^_]*)_|\[([^\]]*)\]\([^)]*\)")

# A class body reorders inside a module body, so an outer rewrite would overwrite an inner one. Each
# pass takes the outermost bodies and the next pass reaches one level further in.
MAX_PASSES = 10

# Rank within a module, then within a class body. A class body reads like a small module: its own constants, then its
# classes, then its functions, with `__init__` pulled to the front. Constants of either kind keep their order.
MODULE_CONSTANT, MODULE_CLASS, MODULE_FUNCTION = 0, 1, 2
CLASS_ATTRIBUTE, CLASS_NESTED, CLASS_INIT, CLASS_DUNDER, CLASS_METHOD = 0, 1, 2, 3, 4

# Each code's name, pylint's way: its family's word, then what went wrong, so a reader knows the rule without looking
# the code up. Printed after the code wherever a report prints one, and accepted wherever a code is.
NAMES = {
    "DBG001": "debug-print",
    "DBG002": "debug-breakpoint",
    "DSC001": "doc-extra-section",
    "DSC002": "doc-missing-section",
    "DSC003": "doc-run-on-summary",
    "DSC004": "doc-misspelled-section",
    "LNK001": "link-missing-file",
    "LNK002": "link-missing-heading",
    "MRK001": "marker-unused",
    "ORD001": "order-alpha",
    "ORD002": "order-assignment",
}

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
# vocabulary `[tool.pyqa] select` and `ignore` are read against, so a name absent here is one nothing would have chosen.
RULES = {
    "DBG001": "A `print()` call is output left in from debugging. Remove it or route deliberate output through one helper that carries the marker once.",
    "DBG002": "A `breakpoint()` or `pdb.set_trace()` call stops in a debugger, left in from debugging. Remove it.",
    "DSC001": "A private or property docstring opens no section: a section states a public contract, and neither of these declares one. Cut the section.",
    "DSC002": "A definition stating a contract opens every section its own shape asks for: a parameter wants `Args`, a returned value `Returns`, a yield `Yields`, a raised error `Raises`. A protocol or abstract base states that contract for the implementations in its module, which carry a summary line alone, so the declaration is the one held to it.",
    "DSC003": "A summary line holds one sentence. A second belongs in the body below it, or joined to the first with a comma or semicolon.",
    "DSC004": "A section header is spelled the way the guide spells it: `Args`, `Returns`, `Yields`, `Raises`. A header near one of those reads as that section misspelled; another convention's name for it does not.",
    "LNK001": "A relative link resolves to a file this repository holds, read from the folder the linking document sits in. A URL, a `mailto:` address and a leading-slash route on the running service are none of this tool's to resolve.",
    "LNK002": "A link's `#fragment` names a heading the target document holds, slugged the way GitHub slugs one: lowered, spaces hyphenated, the rest of the punctuation dropped, and a repeat numbered from the second. A fragment with no path resolves against the document it is written in.",
    "MRK001": "A `# pyqa:` marker entry names a rule only where it exempts something. An entry naming no rule, one repeating a rule its scope already names, a line entry whose rule a file entry in its module names, and a rule no comment can exempt (`LNK`, `MRK`) are stale: cut them.",
    "ORD001": "A definition sits where a reader looking for it by name would look: alphabetical among its siblings, classes above functions, and `__init__` then the dunders first among a class's definitions. What a statement reads at definition time still comes first, and a later rebinding of it stays below the read.",
    "ORD002": "An assignment, in a module or a class body, is ranked above the definitions sharing its body and keeps the order someone chose among the assignments, so the ones that move are below a definition, or below a statement that reads them with nothing above it binding the name; one rebinding a name a statement above it reads stays below that statement.",
}

# The expressions that open a scope of their own, so a name bound inside one is not bound in the body around it.
SCOPES = (ast.DictComp, ast.GeneratorExp, ast.Lambda, ast.ListComp, ast.SetComp)

# Whole lines, not text found anywhere. A docstring that mentions what a caller raises in a sentence is superfluous,
# and only a line holding a header and nothing else opens a section.
SECTIONS = ("Args:", "Returns:", "Yields:", "Raises:")

# Keyed by the lowered word a header holds, since case is one of the ways a header goes wrong.
SECTION_WORDS = {one.removesuffix(":").lower(): one for one in SECTIONS}

# A period, then whitespace, then something that opens a sentence. The lookbehind is what makes an ellipsis unmatchable,
# since its last dot is preceded by one, and requiring whitespace is what lets a line-final period through. A decimal
# needs no rule of its own: nothing follows its dot but a digit.
SENTENCE_BREAK = re.compile(r"(?<!\.)\.\s+(?=[A-Z`\"'(\[])")

# Scoped the way pylint's own `ignore` is, and for its reason: a test declares its cases through fixtures and names
# them, so holding one to `Args` would document the fixture list rather than a contract.
TEST_NAMES = frozenset({"conftest.py", "test"})

# A link target this repository does not own. A leading slash is a route on the running service.
UNCHECKED = ("http://", "https://", "mailto:", "/")

# The families no comment can exempt: a link sits in a document, which holds no `#` comment, and a stale marker is a
# finding about a comment itself.
UNEXEMPTABLE = frozenset({"LNK", "MRK"})

# Trees holding files this project does not own. Named beside the leading-dot test that keeps a root of `.` out of
# the virtualenv and every sibling worktree under `.claude`, which the fixer would otherwise rewrite.
UNOWNED = frozenset({"__pycache__", "build", "dist", "node_modules"})


@dataclasses.dataclass(frozen=True)
class Config:
    """What `[tool.pyqa]` asks of a run: the codes it turns off, and the paths it keeps out, read from its folder."""

    off: frozenset[str] = frozenset()
    exclude: tuple[str, ...] = ()
    folder: pathlib.Path = dataclasses.field(default_factory=pathlib.Path.cwd)


@dataclasses.dataclass(frozen=True)
class Finding:
    """One thing a group read that the rule behind its code puts differently."""

    code: str
    path: pathlib.Path
    line: int
    detail: str
    # The line a marker naming this code sits on to exempt it, when that is not the finding's own: no comment
    # fits inside a multi-line string, so a docstring's run-on summary is exempted from the string's last line.
    anchor: int | None = None

    @override
    def __str__(self) -> str:
        # The column layout the other gates report in, so one parser reads every gate's output, and the code's name
        # last, where pylint prints its own.
        return f"{self.path}:{self.line}:1: {self.code} {self.detail} ({NAMES[self.code]})"


@dataclasses.dataclass(frozen=True)
class Item:  # Each field feeds the sort or the rewrite of one statement. pylint: disable=too-many-instance-attributes
    """One statement of a body, with the span of source text that travels when it moves."""

    node: ast.stmt
    index: int
    start: int
    end: int
    key: tuple[int | str, ...]
    name: str
    provides: frozenset[str]
    requires: frozenset[str]


@dataclasses.dataclass(frozen=True)
class Marker:
    """One `# pyqa:` comment: where its entries open, its form, and each entry as written beside the code it disables.

    An entry disables no code when it names none, repeats one its scope already holds, or is a line entry whose code a
    file entry in its module names. Each of those is stale in any run that reads markers.
    """

    line: int
    column: int
    form: str
    entries: tuple[tuple[str, str | None], ...]

    @property
    def codes(self) -> tuple[str, ...]:
        """The codes this marker disables, in the order written."""
        return tuple(code for _, code in self.entries if code)


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


def _applied(source: str, edits: list[tuple[int, int, str]]) -> str:
    """The module with its outermost edits applied, and each edit nested inside one left for the next pass."""
    # Outermost first, and nothing that overlaps what was already taken. A class body nested in a sorted run would
    # otherwise be rebuilt from lines the outer edit has just replaced.
    outermost: list[tuple[int, int, str]] = []
    reach = -1
    for edit in sorted(edits, key=lambda edited: (edited[0], -edited[1])):
        if edit[0] >= reach:
            outermost.append(edit)
            reach = edit[1]
    output = source.splitlines(keepends=True)
    for start, end, text in reversed(outermost):
        output[start:end] = [text]
    return "".join(output)


def _block_span(node: ast.stmt, lines: list[str]) -> tuple[int, int]:
    """The 0-based half-open line span of one statement, its comments on both sides included."""
    # A comment run under a statement belongs to it only when a blank line or the end of the file closes the run.
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
    """The span of every statement in one body, keyed by identity."""
    # An attribute docstring folds into the span of the assignment above, so it is not left behind as a barrier.
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


def _bodies_of(tree: ast.Module) -> list[tuple[list[ast.stmt], bool, frozenset[str]]]:
    """Every body ordered, flagged as a class body or the module's own, with the names it reads from outside."""
    # A class body reads the module's names as well as builtins, since a name its own scope lacks is looked up there.
    module = BUILTIN_NAMES.union(*(_defined_names(one) for one in tree.body))
    bodies: list[tuple[list[ast.stmt], bool, frozenset[str]]] = [(tree.body, False, BUILTIN_NAMES)]
    bodies.extend((node.body, True, module) for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
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


def _bound_above(body: list[ast.stmt], first: ast.stmt, outer: frozenset[str]) -> frozenset[str]:
    """The names a run opening at this statement finds already bound: outside its body, or above it in the body."""
    return outer.union(*(_defined_names(one) for one in body[: body.index(first)]))


def _chain(items: list[Item], edges: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """The edges with the run's assignments chained in the order written, save where a read orders two the other way."""
    chained = set(edges)
    following: dict[int, set[int]] = collections.defaultdict(set)
    for source, target in edges:
        following[source].add(target)
    assignments = [item.index for item in items if not isinstance(item.node, DEFINITIONS)]
    for first, second in itertools.pairwise(assignments):
        if not _reaches(following, second, first):
            following[first].add(second)
            chained.add((first, second))
    return chained


def _check_exempted(
    scanned: list[pathlib.Path],
    find: Callable[[pathlib.Path, str], list[Finding]],
    used: set[Exemption],
) -> tuple[list[Finding], int]:
    """What `find` reports that no marker exempts, and how many one did, adding each exempting entry to `used`."""
    findings: list[Finding] = []
    exempted = 0
    for path in scanned:
        source = path.read_text(encoding="utf-8")
        found = find(path, source)
        kept, exempting = _exempted(found, _markers(source))
        findings.extend(kept)
        used |= exempting
        exempted += len(found) - len(kept)
    return findings, exempted


def _check_markers(scanned: list[pathlib.Path], judged: frozenset[str], used: set[Exemption]) -> list[Finding]:
    """Every pyqa marker entry that exempts nothing, held to the families this run judged."""
    findings: list[Finding] = []
    for path in scanned:
        for marker in _markers(path.read_text(encoding="utf-8")):
            unused = [
                written
                for written, code in marker.entries
                if code is None or _is_stale((path, marker.line, code), judged, used)
            ]
            if unused:
                detail = f"`# pyqa: {marker.form}={', '.join(unused)}` exempts nothing"
                findings.append(Finding("MRK001", path, marker.line, detail))
    return findings


def _check_order(scanned: list[pathlib.Path], pinned: frozenset[str], used: set[Exemption]) -> list[Finding]:
    """Every statement the rule moves, adding each freeze `--fix` still needs to `used`."""
    findings: list[Finding] = []
    for path in scanned:
        source = path.read_text(encoding="utf-8")
        markers = _markers(source)
        findings.extend(_findings_for(path, source, pinned, markers))
        used |= _freezes(path, source, pinned, markers)
    return findings


def _code_of(entry: str) -> str | None:
    """The code an entry names, by the code itself or by its name, or None when it names neither."""
    return next((code for code, name in NAMES.items() if entry in {code, name}), None)


def _config() -> Config:
    """What `[tool.pyqa]` in the nearest `pyproject.toml` at or above the working directory asks of a run."""
    for folder in (pathlib.Path.cwd(), *pathlib.Path.cwd().parents):
        path = folder / "pyproject.toml"
        if path.exists():
            break
    else:
        return Config()
    table = _table(path)
    if unknown := sorted(set(table) - CONFIG_KEYS):
        raise ValueError(f"`[tool.pyqa]` in {path} has no key named: {', '.join(unknown)}")

    # Refused rather than passed over, for the reason `useless-suppression` and `RUF100` are errors here: a selector
    # naming nothing reads as load-bearing while choosing nothing at all.
    picked = _weights(_strings(table, "select", path), "select", path) if "select" in table else dict.fromkeys(RULES, 0)
    dropped = _weights(_strings(table, "ignore", path), "ignore", path)
    off = frozenset(code for code in RULES if code not in picked or dropped.get(code, -1) >= picked[code])
    return Config(off, _patterns(table, path), folder.resolve())


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


def _covers(pattern: str, below: tuple[str, ...], anchor: tuple[str, ...] | None) -> bool:
    """Whether an `exclude` pattern covers a path below a root, by its last name or by its path from the config."""
    if "/" not in pattern:
        return fnmatch.fnmatchcase(below[-1], pattern)
    # Trimmed only once the slash has made it a path, so `./generated` stays at the folder rather than at any depth.
    return anchor is not None and _glob(_pattern_segments(pattern), anchor + below)


def _debug_findings(path: pathlib.Path, source: str) -> list[Finding]:
    """Every debugging call in one module, exempted or not, in the order its lines run."""
    # Every callee in `DEBUG_CALLS` is a name or one attribute of a name, so any other callee is passed over unread. A
    # long method chain would otherwise be unparsed once per call, in time quadratic in its length, and past the
    # recursion limit.
    found = [
        Finding(DEBUG_CALLS[callee], path, node.lineno, f"`{callee}()` called")
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            or (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name))
        )
        and (callee := ast.unparse(node.func)) in DEBUG_CALLS
    ]
    return sorted(found, key=lambda one: one.line)


def _declarations(tree: ast.Module) -> list[ast.AsyncFunctionDef | ast.FunctionDef]:
    """Every method a protocol or abstract base in this module declares the contract of."""
    found: list[ast.AsyncFunctionDef | ast.FunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {ast.unparse(one).split("[", maxsplit=1)[0] for one in node.bases}
        declaring = any(one.endswith(("ABC", "Protocol")) for one in bases)
        for inner in node.body:
            if not isinstance(inner, FUNCTIONS):
                continue
            abstract = any("abstractmethod" in one for one in _decorators(inner))
            if abstract or (declaring and _is_stub(inner)):
                found.append(inner)
    return found


def _declared_sections(node: ast.AsyncFunctionDef | ast.FunctionDef) -> tuple[str, ...]:
    """The sections this definition's docstring opens however spelled, deduplicated, in the order written."""
    opened = [_section_of(line) for line in _docstring_lines(node)]
    return tuple(dict.fromkeys(one for one in opened if one))


def _decorators(node: ast.AsyncFunctionDef | ast.FunctionDef) -> set[str]:
    """Each decorator on this definition, spelled the way `ast.unparse` spells it."""
    return {ast.unparse(one) for one in node.decorator_list}


def _defined_names(node: ast.stmt) -> frozenset[str]:
    """The names this statement binds in the body that holds it, empty when it binds none."""
    names: set[str] = set()
    pending: list[ast.AST] = [node]
    while pending:
        one = pending.pop()
        if isinstance(one, DEFINITIONS):
            # A definition binds its own name here, and everything its body binds belongs to its own scope.
            names.add(one.name)
            continue
        if isinstance(one, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0] for alias in one.names)
        elif isinstance(one, ast.Name) and isinstance(one.ctx, ast.Store):
            names.add(one.id)
        if not isinstance(one, SCOPES):
            pending.extend(ast.iter_child_nodes(one))
    return frozenset(names)


def _docs_detail(code: str, name: str, parts: tuple[str, ...]) -> str:
    """How a docstring finding reads: whose docstring, what it did, and to which sections or sentences."""
    listed = ", ".join(one.rstrip(":") for one in parts)
    return f"`{name}` {REPORT_VERBS[code]} {listed}"


def _docs_findings(path: pathlib.Path, source: str) -> list[Finding]:
    """One module's docstring findings, exempted or not, in the order its lines run."""
    tree = ast.parse(source)
    found: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, FUNCTIONS):
            continue

        # A misspelled header reads as the section it means, so a covered docstring's is reported by `DSC001`:
        # the remedy there is to cut the section rather than to spell it.
        if _is_covered(node):
            sections, code = _declared_sections(node), "DSC001"
        else:
            sections, code = _misspelled_sections(node), "DSC004"
        if sections:
            found.append(Finding(code, path, node.lineno, _docs_detail(code, node.name, sections)))

    # Above the test skip below, deliberately: what `DSC002` exempts a test module for is its fixtures standing
    # in for arguments, and a summary line holding two sentences has no such reason.
    found.extend(_overruns(path, tree))
    if not _is_test(path):
        found.extend(
            Finding("DSC002", path, node.lineno, _docs_detail("DSC002", node.name, sections))
            for node in _contracted(tree)
            if (sections := _omitted_sections(node))
        )
    return sorted(found, key=lambda one: (one.line, one.code))


def _docstring_lines(node: ast.AsyncFunctionDef | ast.FunctionDef) -> list[str]:
    """Each line of this definition's docstring, stripped, and none when it has no docstring."""
    return [line.strip() for line in (ast.get_docstring(node, clean=False) or "").splitlines()]


def _docstrings(tree: ast.Module) -> Iterator[tuple[str, int, int, str]]:
    """Every module, class and function docstring under this tree: its name, first and last lines, and text."""
    for node in ast.walk(tree):
        if not isinstance(node, DOCUMENTED):
            continue
        docstring = ast.get_docstring(node, clean=False)
        if docstring is None:
            continue
        # The docstring's lines rather than the definition's, which a multi-line signature puts far above them.
        string = node.body[0]
        yield getattr(node, "name", "<module>"), string.lineno, string.end_lineno or string.lineno, docstring


def _edits(source: str, pinned: frozenset[str]) -> list[tuple[int, int, str]]:
    """The line span and replacement text of every run out of order in one module, class bodies included."""
    lines = source.splitlines(keepends=True)
    # Read again on every pass: a pass moves lines, and a marker travels with the statement it sits on.
    markers = _markers(source)
    edits: list[tuple[int, int, str]] = []
    for body, in_class, items, order in _out_of_order(source, lines, pinned, markers):
        start = items[0].start
        text = _stitch(items, order, lines, in_class)

        # The import block wants one blank line under it before a constant and two before a definition, so a run
        # that hoists a constant to the top of the module has to restate the gap the import block above it requires.
        above = body.index(items[0].node) - 1
        if not in_class and above >= 0 and isinstance(body[above], (ast.Import, ast.ImportFrom)):
            while start and not lines[start - 1].strip():
                start -= 1
            blank = "\n" * (2 if isinstance(items[order[0]].node, DEFINITIONS) else 1)
            text = f"{blank}{text}"
        edits.append((start, items[-1].end, text))
    return edits


def _exempted(findings: list[Finding], markers: list[Marker]) -> tuple[list[Finding], set[Exemption]]:
    """The findings no marker exempts, and each entry that exempted one."""
    kept: list[Finding] = []
    used: set[Exemption] = set()
    for one in findings:
        line = _exempting(markers, one.code, one.line if one.anchor is None else one.anchor)
        if line is None:
            kept.append(one)
        else:
            used.add((one.path, line, one.code))
    return kept, used


def _exempting(markers: list[Marker], code: str, line: int) -> int | None:
    """The line of the marker exempting this code at this line: a file entry anywhere, a line entry on its own line."""
    return next(
        (one.line for one in markers if code in one.codes and (one.form == "disable-file" or one.line == line)), None
    )


def _files(roots: list[str], suffix: str, config: Config) -> list[pathlib.Path]:
    """Every file of one suffix under the roots, in a stable order, minus what the project does not own or excludes."""
    found: list[pathlib.Path] = []
    for root in roots:
        path = pathlib.Path(root)
        if not path.exists():
            raise FileNotFoundError(f"no such root to read: {root}")
        if path.is_file():
            if path.suffix == suffix:
                found.append(path)
            continue

        # Resolved once per root, so a relative root or one reached through a link reads from the config's folder,
        # and a root outside that folder takes no anchor, so no pattern holding a `/` matches beneath it.
        base = path.resolve()
        anchor = base.relative_to(config.folder).parts if base.is_relative_to(config.folder) else None
        for one in sorted(path.rglob(f"*{suffix}")):
            below = one.relative_to(path)
            if not _is_vendored(below) and not _is_excluded(below.parts, anchor, config.exclude):
                found.append(one)
    return found


def _findings_for(
    path: pathlib.Path,
    source: str,
    pinned: frozenset[str],
    markers: list[Marker],
) -> list[Finding]:
    """Report the fewest statements whose move would put the file in order, at their current lines."""
    lines = source.splitlines(keepends=True)
    findings = [
        finding
        for _, _, items, order in _out_of_order(source, lines, pinned, markers)
        for finding in _misplaced(path, items, order)
    ]
    return sorted(findings, key=lambda finding: finding.line)


def _fix_order(scanned: list[pathlib.Path], pinned: frozenset[str], off: frozenset[str]) -> str:
    """Sort every module and say how many moved, or say the config has turned sorting off."""
    if all(code in off for code in RULES if code.startswith("ORD")):
        return "sorting off"
    rewritten = 0
    for path in scanned:
        source = path.read_text(encoding="utf-8")
        output = _rewrite(source, pinned)
        if output != source:
            path.write_text(output, encoding="utf-8")
            rewritten += 1
    return f"sorted {rewritten}"


def _freezes(path: pathlib.Path, source: str, pinned: frozenset[str], markers: list[Marker]) -> set[Exemption]:
    """The order entries in one module whose freeze changes what `--fix` writes, judged in file order."""
    entries = [
        (one, index, code)
        for one in markers
        for index, (_, code) in enumerate(one.entries)
        if code is not None and code.startswith("ORD")
    ]
    if not entries:
        return set()
    held: set[Exemption] = set()

    # Judged against the statements `--fix` itself writes, every pass and a refusal included, rather than the report:
    # a freeze at the head of a body can leave the report's text unchanged while deciding where `--fix` moves the
    # statements below it. An entry already set aside is overwritten first, so cutting a file entry never restores the
    # line entry it shadowed. A stale freeze is dropped before the next is judged, so two that each make the other
    # redundant leave one in use, and every freeze reported stale can be cut together without changing what `--fix`
    # writes.
    kept = source
    for one in markers:
        for index, (_, code) in enumerate(one.entries):
            if code is None:
                kept = _overwritten(kept, one, index)
    target = _written(kept, pinned)
    for one, index, code in entries:
        cut = _overwritten(kept, one, index)
        if _written(cut, pinned) == target:
            kept = cut
        else:
            held.add((path, one.line, code))
    return held


def _glob(pattern: tuple[str, ...], parts: tuple[str, ...]) -> bool:
    """Whether a path's segments match a pattern's, as `fnmatch` reads each, with `**` standing for any run of them."""
    if pattern[:1] == ("**",):
        return any(_glob(pattern[1:], parts[skip:]) for skip in range(len(parts) + 1))
    return (not pattern and not parts) or (
        bool(pattern) and bool(parts) and fnmatch.fnmatchcase(parts[0], pattern[0]) and _glob(pattern[1:], parts[1:])
    )


def _held(body: list[ast.stmt], pinned: frozenset[str], in_class: bool, markers: list[Marker]) -> set[int]:
    """The statements of one body frozen in place, by identity: by carrying a pinned name, or by a marker on its line."""
    return {
        id(node)
        for node in body
        if (not in_class and getattr(node, "name", None) in pinned)
        or _exempting(markers, _order_code(node), node.lineno) is not None
    }


def _is_attribute_docstring(node: ast.stmt) -> bool:
    """Whether the statement is a bare string, which under an assignment documents it."""
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _is_contracted(node: ast.AsyncFunctionDef | ast.FunctionDef, declaring: bool = False) -> bool:
    """Whether the guide holds this definition to a docstring stating its whole contract."""
    # A declaring definition is the exception a stub body would otherwise exempt: a protocol's method body is a
    # docstring and nothing else, and that docstring is where the contract for every implementation of it is written.
    name = node.name

    # `__init__` is the one dunder the guide calls a public method. Every other one implements a language
    # protocol whose contract the language states, which is why `D105` is ignored rather than selected.
    dunder = _is_dunder(name) and name != "__init__"
    stated = ast.get_docstring(node) is not None and (declaring or not _is_stub(node))
    if _is_covered(node) or dunder or not stated:
        return False
    return not any(one.endswith(("overload", "override")) for one in _decorators(node))


def _is_covered(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Whether the guide holds this definition to a docstring with no sections in it."""
    if _is_tool(node):
        return False
    decorators = _decorators(node)
    accessor = bool(decorators & PROPERTY_DECORATORS) or any(
        one.endswith((".deleter", ".setter")) for one in decorators
    )
    name = node.name
    return accessor or (name.startswith("_") and not _is_dunder(name))


def _is_dunder(name: str) -> bool:
    """Whether the name is a dunder, which a leading underscore does not make private."""
    return name.startswith("__") and name.endswith("__")


def _is_excluded(below: tuple[str, ...], anchor: tuple[str, ...] | None, exclude: tuple[str, ...]) -> bool:
    """Whether `exclude` covers a file, or a directory between the root it was found under and it."""
    # Depths start below the root, which is never matched itself: a path named on the command line is always read.
    return any(_covers(pattern, below[:depth], anchor) for depth in range(1, len(below) + 1) for pattern in exclude)


def _is_faithful(source: str, rewritten: str) -> bool:
    """Whether the rewrite holds exactly the lines the original held, and is still a Python module."""
    try:
        ast.parse(rewritten)
    except SyntaxError:
        return False
    return sorted(one for one in source.splitlines() if one.strip()) == sorted(
        one for one in rewritten.splitlines() if one.strip()
    )


def _is_movable(node: ast.stmt) -> bool:
    """Whether a statement can be reordered or has to stay put and split the body around it."""
    # Anything side-effecting or mutating stays, since moving it changes what runs when.
    if isinstance(node, DEFINITIONS):
        return True
    targets: list[ast.expr] = node.targets if isinstance(node, ast.Assign) else []
    if isinstance(node, ast.AnnAssign):
        targets = [node.target]
    return bool(targets) and all(isinstance(target, ast.Name) for target in targets)


def _is_stale(exemption: Exemption, judged: frozenset[str], used: set[Exemption]) -> bool:
    """Whether a marker entry exempts nothing: its family takes no comment, or was judged, and the entry went unused."""
    family = exemption[2][:3]
    return family in UNEXEMPTABLE or (family in judged and exemption not in used)


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


def _markers(source: str) -> list[Marker]:
    """Every pyqa marker in one module, in file order, each entry beside the code it disables or set aside as stale."""
    if "pyqa:" not in source:
        return []
    found = [
        (token.start[0], token.start[1] + match.start(2), match[1], tuple(re.split(r"\s*,\s*", match[2])))
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and (match := MARKER.search(token.string))
    ]
    # A scope names each code once: one comment for a line entry, the whole module for a file entry. A file entry
    # covers the module wherever it sits, so a line entry is set aside under one below it as much as one above.
    filed = {_code_of(one) for *_, form, written in found if form == "disable-file" for one in written}
    module: set[str | None] = set()
    markers: list[Marker] = []
    for line, column, form, written in found:
        scope = module if form == "disable-file" else set(filed)
        entries: list[tuple[str, str | None]] = []
        for one in written:
            code = _code_of(one)
            entries.append((one, None if code is None or code in scope else code))
            scope.add(code)
        markers.append(Marker(line, column, form, tuple(entries)))
    return markers


def _masked(line: str) -> str:
    """The line with every dot that cannot end a sentence spelled over, at its original length."""
    # Length is preserved so a match's offsets still slice the line the reader wrote, backticks and all.
    masked = CODE_SPAN.sub(lambda match: f"`{'x' * (len(match.group()) - 2)}`", line)
    for abbreviation in ABBREVIATIONS:
        masked = masked.replace(abbreviation, "x" * len(abbreviation))
    return masked


def _misplaced(path: pathlib.Path, items: list[Item], order: list[int]) -> Iterator[Finding]:
    """A finding for each item outside the longest run already in order, naming the one it belongs after."""
    keeping = _stable_positions(order)
    for position, index in enumerate(order):
        if index in keeping:
            continue
        moved = items[index]
        after = f"after `{items[order[position - 1]].name}`" if position else "first"
        yield Finding(
            code=_order_code(moved.node),
            path=path,
            line=moved.node.lineno,
            detail=f"`{moved.name}` belongs {after}",
        )


def _misspelled_sections(node: ast.AsyncFunctionDef | ast.FunctionDef) -> tuple[str, ...]:
    """The sections this definition's docstring opens under a spelling that is not theirs, in the order written."""
    opened = [_section_of(line) for line in _docstring_lines(node) if line not in SECTIONS]
    return tuple(dict.fromkeys(one for one in opened if one))


def _named(code: str) -> str:
    """One code the way a report's own lines print it, its name beside it."""
    return f"{code} ({NAMES[code]})"


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


def _order_code(node: ast.stmt) -> str:
    """The code a statement out of place reports as, which is the one a marker names to freeze it."""
    return "ORD001" if isinstance(node, DEFINITIONS) else "ORD002"


def _ordered(items: list[Item], above: frozenset[str]) -> list[int]:
    """The positions the items belong in: the style order, narrowed by what each one reads and what rebinds it."""
    # `above` holds the names bound before the run opens. Two bindings of one name keep their order, since the last one
    # is the one that holds, and the assignments keep theirs. A cycle cannot be ordered at all, so a body holding one is
    # returned exactly as it was.
    binders: dict[str, list[int]] = collections.defaultdict(list)
    for item in items:
        for name in item.provides:
            binders[name].append(item.index)
    edges = {pair for indices in binders.values() for pair in itertools.pairwise(indices)}
    for item in items:
        for name in item.requires:
            edges.update(_read_edges(item.index, binders.get(name, []), name in above))

    dependents: list[set[int]] = [set() for _ in items]
    blocking = [0] * len(items)
    for source, target in _chain(items, edges):
        dependents[source].add(target)
        blocking[target] += 1

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


def _out_of_order(
    source: str,
    lines: list[str],
    pinned: frozenset[str],
    markers: list[Marker],
) -> Iterator[tuple[list[ast.stmt], bool, list[Item], list[int]]]:
    """Every run out of order in one module, with its body, whether that is a class body, and the order it belongs in."""
    for body, in_class, outer in _bodies_of(ast.parse(source)):
        spans = _block_spans(body, lines)
        for segment in _segments(body, spans, _held(body, pinned, in_class, markers)):
            items = _body_items(segment, spans, in_class)
            order = _ordered(items, _bound_above(body, segment[0], outer))
            if order != list(range(len(items))):
                yield body, in_class, items, order


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


def _overruns(path: pathlib.Path, tree: ast.Module) -> list[Finding]:
    """A finding for each summary line under this tree running past a single sentence."""
    found: list[Finding] = []
    for name, line, end, docstring in _docstrings(tree):
        if len(sentences := _sentences(docstring)) > 1:
            found.append(Finding("DSC003", path, line, _docs_detail("DSC003", name, sentences[1:]), anchor=end))
    return found


def _overwritten(source: str, marker: Marker, index: int) -> str:
    """The module with one marker entry spelled over by a name nothing carries, so the entry disables nothing."""
    lines = source.split("\n")
    text = lines[marker.line - 1]
    # The entries open at the marker's column, so the words from there on start with them, in the order written.
    entry = list(re.finditer(r"[\w-]+", text[marker.column :]))[index]
    start = marker.column + entry.start()
    end = start + len(entry[0])
    lines[marker.line - 1] = f"{text[:start]}{'_' * len(entry[0])}{text[end:]}"
    return "\n".join(lines)


def _owned(node: ast.AsyncFunctionDef | ast.FunctionDef) -> Iterator[ast.AST]:
    """Every node this definition's own body reaches, stopping at each definition nested inside it."""
    pending: list[ast.AST] = list(ast.iter_child_nodes(node))
    while pending:
        one = pending.pop()
        if isinstance(one, (*FUNCTIONS, ast.Lambda)):
            continue
        yield one
        pending.extend(ast.iter_child_nodes(one))


def _pattern_segments(pattern: str) -> tuple[str, ...]:
    """The segments of an `exclude` pattern, a leading `./` and a trailing `/` dropped."""
    return tuple(pattern.removeprefix("./").rstrip("/").split("/"))


def _patterns(table: dict[str, Any], path: pathlib.Path) -> tuple[str, ...]:
    """The `exclude` patterns, refused when one can never match: one opening at `/` or holding `.`, `..` or nothing."""
    patterns = tuple(_strings(table, "exclude", path))
    impossible = sorted(one for one in patterns if one.startswith("/") or {"", ".", ".."} & set(_pattern_segments(one)))
    if impossible:
        listed = ", ".join(impossible)
        raise ValueError(f"`[tool.pyqa] exclude` in {path} holds a pattern that can never match: {listed}")
    return patterns


def _plural(count: int, noun: str) -> str:
    """The count beside its noun, which takes an `s` unless the count is one."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _print(*args: Any) -> None:
    """The one stdout sink this script reports through."""
    print(*args)  # pyqa: disable=debug-print  # The script's one deliberate output.


def _reaches(following: dict[int, set[int]], start: int, goal: int) -> bool:
    """Whether a chain of edges already leads from one position to another."""
    pending, seen = [start], {start}
    while pending:
        one = pending.pop()
        pending.extend(after for after in following[one] if after not in seen)
        seen.update(following[one])
    return goal in seen


def _read_edges(index: int, binders: list[int], bound: bool) -> list[tuple[int, int]]:
    """The order one read imposes, as pairs of positions in a run whose first stays above its second."""
    # The read follows the last binding above it and stays above every binding below it. With nothing binding the name
    # above it, in the run or before it, the read is a forward reference, and the first binding below it rises instead.
    earlier = [one for one in binders if one < index]
    later = [one for one in binders if one > index]
    if earlier or bound:
        edges = [(index, one) for one in later]
        edges.extend((one, index) for one in earlier[-1:])
    else:
        edges = [(one, index) for one in later[:1]]
    return edges


def _referenced_names(node: ast.stmt) -> frozenset[str]:
    """The names this statement reads the moment it executes, before anything calls into it."""
    # A class body counts, its annotations included, since libraries such as pydantic resolve those while it builds
    # the model, and so do its method decorators, defaults, and nested classes, which all run while the class is built.
    sources: list[ast.AST] = list(getattr(node, "decorator_list", []))
    names: set[str] = set()
    if isinstance(node, ast.ClassDef):
        sources.extend(node.bases)
        sources.extend(keyword.value for keyword in node.keywords)
        sources.extend(child for child in node.body if not isinstance(child, DEFINITIONS))
        names.update(*(_referenced_names(child) for child in node.body if isinstance(child, DEFINITIONS)))
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        arguments = node.args
        sources.extend(one for one in (*arguments.defaults, *arguments.kw_defaults) if one is not None)
    else:
        sources.append(node)
    for source in sources:
        # A target is bound rather than read, so `X = 1` reads nothing, while `X = X + 1` and `X += 1` read the binding
        # above it.
        for child in ast.walk(source):
            if isinstance(child, ast.Name) and not isinstance(child.ctx, ast.Store):
                names.add(child.id)
            elif isinstance(child, ast.AugAssign) and isinstance(child.target, ast.Name):
                names.add(child.target.id)
    return frozenset(names)


def _report(results: dict[str, tuple[list[Finding], int]], off: frozenset[str], scanned: str, fixed: str | None) -> int:
    """Print every finding, then one line saying what the run read and found, and answer whether anything fails it."""
    found = [one for findings, _ in results.values() for one in findings]
    kept = [one for one in found if one.code not in off]
    for finding in kept:
        _print(finding)
    counts: list[str] = []
    if results:
        checked = [code for code in RULES if code[:3] in results and code not in off]
        counts += [f"{_plural(len(checked), 'code')} checked", _plural(len(kept), "finding")]
        if exempted := sum(spared for _, spared in results.values()):
            counts.append(f"{exempted} exempted")
        if suppressed := len(found) - len(kept):
            counts.append(f"{suppressed} suppressed")
    if fixed is not None:
        counts.append(fixed)
    summary = f"{scanned}: {', '.join(counts)}"
    # Always say how much was read. A silent pass and a pass over nothing at all read the same.
    _print(f"\n{summary}" if kept else summary)
    counted = collections.Counter(one.code for one in kept)
    if counted:
        _print(f"\nreported {', '.join(f'{count} {_named(code)}' for code, count in sorted(counted.items()))}")
    for code in sorted(counted):
        _print(f"\n{_named(code)}  {RULES[code]}")
    return 1 if counted else 0


def _rewrite(source: str, pinned: frozenset[str]) -> str:
    """Sort every body in one module, or return it untouched when the result cannot be trusted."""
    current = source
    for _ in range(MAX_PASSES):
        edits = _edits(current, pinned)
        if not edits:
            break
        current = _applied(current, edits)

    return current if _is_faithful(source, current) else source


def _scanned(read: dict[str, list[pathlib.Path]], roots: list[str]) -> str:
    """How many files of each kind a run read, which is what tells a pass over nothing from a pass."""
    kinds = " and ".join(_plural(len(paths), kind) for kind, paths in read.items())
    return f"scanned {kinds} under {', '.join(roots)}"


def _section_of(line: str) -> str | None:
    """The section this docstring line opens, spelled the way the guide spells it or near enough to read as it."""
    if not line.endswith(":") or not (word := line.removesuffix(":").strip()).isalpha():
        return None
    matched = difflib.get_close_matches(word.lower(), tuple(SECTION_WORDS), n=1, cutoff=NEARNESS)
    return SECTION_WORDS[matched[0]] if matched else None


def _segments(body: list[ast.stmt], spans: dict[int, tuple[int, int]], held: set[int]) -> list[list[ast.stmt]]:
    """The runs of movable statements, split wherever something has to stay where it is."""
    runs: list[list[ast.stmt]] = []
    current: list[ast.stmt] = []
    for node in body:
        if id(node) not in spans:
            continue
        if _is_movable(node) and id(node) not in held:
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


def _sort_key(node: ast.stmt, index: int, in_class: bool) -> tuple[int | str, ...]:
    """Where the guide puts this statement among its siblings, ties broken by where it already is."""
    # Constants and class attributes are ranked but not sorted, so they keep the order someone chose for them.
    if not isinstance(node, DEFINITIONS):
        return CLASS_ATTRIBUTE if in_class else MODULE_CONSTANT, index
    name = node.name

    # Whether the name sorts as written is decided here, beside the rank, and never read back off the rank. The
    # module and class scales number independently and overlap, so a rank cannot say whether its definition is a dunder.
    verbatim = False
    if not in_class:
        rank = MODULE_CLASS if isinstance(node, ast.ClassDef) else MODULE_FUNCTION
    elif isinstance(node, ast.ClassDef):
        rank = CLASS_NESTED
    elif name == "__init__":
        rank, verbatim = CLASS_INIT, True
    elif _is_dunder(name):
        rank, verbatim = CLASS_DUNDER, True
    else:
        rank = CLASS_METHOD

    # A dunder sorts on the name it is written with. Everything else discards the leading underscores,
    # so a private name sits beside its public twin rather than in a block of its own, and takes the tie.
    stripped = name.lstrip("_")
    ordering = (name, 0) if verbatim else (stripped, len(stripped) - len(name))
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
    """Rebuild one run in the new order, spaced the way the formatter would leave it."""
    # Two statements that were neighbors keep the gap they had, so a block of related constants moves as a unit.
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
    return f"{''.join(pieces)}\n"


def _strings(table: dict[str, Any], key: str, path: pathlib.Path) -> list[str]:
    """One `[tool.pyqa]` key's list, empty when the key is absent and refused when it is anything but strings."""
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(one, str) for one in value):
        raise ValueError(f"`[tool.pyqa] {key}` in {path} must be a list of strings")
    return value


def _table(path: pathlib.Path) -> dict[str, Any]:
    """The `[tool.pyqa]` table in one `pyproject.toml`, empty when absent and refused when it is no table."""
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path} is not valid TOML: {error}") from error
    tool = document.get("tool", {})
    table = tool.get("pyqa", {}) if isinstance(tool, dict) else None
    if not isinstance(table, dict):
        raise ValueError(f"`[tool.pyqa]` in {path} must be a table")
    return table


def _unresolved(paths: list[pathlib.Path], root: pathlib.Path) -> list[Finding]:
    """Every relative link in these documents that does not resolve."""
    known: dict[pathlib.Path, set[str]] = {}
    findings: list[Finding] = []
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
    return findings


def _weights(selectors: list[str], key: str, path: pathlib.Path) -> dict[str, int]:
    """The codes these selectors match, each beside the specificity of the most specific selector matching it."""
    # A code or a name is as specific as a whole code, and a prefix as specific as its length.
    weights: dict[str, int] = {}
    unknown: list[str] = []
    for selector in selectors:
        code = _code_of(selector)
        matched = (
            {code: len(code)}
            if code
            else {one: len(selector) for one in RULES if selector and one.startswith(selector)}
        )
        if not matched:
            unknown.append(selector)
        for one, weight in matched.items():
            weights[one] = max(weights.get(one, 0), weight)
    if unknown:
        listed = ", ".join(sorted(unknown))
        raise ValueError(f"`[tool.pyqa] {key}` in {path} names no code, name or prefix this tool reports: {listed}")
    return weights


def _written(source: str, pinned: frozenset[str]) -> str:
    """Every statement `--fix` leaves in one module, in order, comments and spacing apart."""
    return ast.dump(ast.parse(_rewrite(source, pinned)))


def main(argv: list[str] | None = None) -> int:
    """Report what every enabled group finds under the given roots.

    Args:
        argv: The arguments to parse, or None to read `sys.argv`.

    Returns:
        0 when every group passes, or `--fix` put the order right, else 1, so a pipeline gates on it.
    """
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__.splitlines()[0])
    parser.add_argument("roots", nargs="*", help=f"Folders to read. Defaults to {' '.join(DEFAULT_ROOTS)}.")
    parser.add_argument(
        "--debugging", action="store_true", help="Check for debugging calls. On when no group is named."
    )
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

    every = not (args.debugging or args.docs or args.links or args.order)
    if args.fix and not (every or args.order):
        parser.error("--fix sorts what --order reports, so it cannot be asked for without it")

    # Read before `--fix` writes anything, so a config that fails the run fails it with every file as it was.
    config = _config()
    roots = args.roots or list(DEFAULT_ROOTS)
    pinned = frozenset(DEFAULT_PINNED if args.pin is None else args.pin)

    # Each kind of file is found once, and only when a group reading it runs, so every group, and `--fix`, reads the
    # same files, and the summary counts only the kinds read.
    read: dict[str, list[pathlib.Path]] = {}
    modules: list[pathlib.Path] = []
    documents: list[pathlib.Path] = []
    if every or args.debugging or args.docs or args.order:
        modules = read["module"] = _files(roots, ".py", config)
    if every or args.links:
        documents = read["document"] = _files(roots, ".md", config)

    # `--fix` rewrites before any group reads, so every finding, and every marker a group spends, is matched against the
    # sorted text the marker pass reads after it.
    fixed = _fix_order(modules, pinned, config.off) if args.fix else None

    # What the marker pass needs to tell a stale entry from one no group of this run weighed: the entries each group
    # spent exempting a finding, and the families that ran whose findings a marker can exempt.
    used: set[Exemption] = set()

    # Each group's findings and how many of them a marker exempted, under the family of codes the group reports. A group
    # whose findings take no marker counts none, and so does the order group: a freeze holds a statement in place
    # rather than exempting a finding.
    results: dict[str, tuple[list[Finding], int]] = {}
    if every or args.debugging:
        results["DBG"] = _check_exempted(modules, _debug_findings, used)
    if every or args.docs:
        results["DSC"] = _check_exempted(modules, _docs_findings, used)
    if every or args.links:
        results["LNK"] = _unresolved(documents, pathlib.Path(roots[0])), 0
    if fixed is None and (every or args.order):
        results["ORD"] = _check_order(modules, pinned, used), 0
    if judged := frozenset(results) - UNEXEMPTABLE:
        results["MRK"] = _check_markers(modules, judged, used), 0
    return _report(results, config.off, _scanned(read, roots), fixed)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as error:
        raise SystemExit(f"pyqa: {error}") from error
    except KeyboardInterrupt as user_interrupt:
        raise SystemExit(130) from user_interrupt
