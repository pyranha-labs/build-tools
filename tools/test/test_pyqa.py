"""Cases for the definition order, docstring shape and document link checks."""

import ast
import json
import pathlib
from collections.abc import Callable

import pytest

from tools import pyqa

# The fence a markdown document opens a code block with, as a case needs to write one.
FENCE = "```"
TILDE = "~~~"

# One module's source per declaration case. The line a case expects is the `def` line, which a decorator sits above
# rather than on, so `DECLARATION_ABSTRACT` reports one line below where its decorator starts.
DECLARATION_ABSTRACT = '''"""Cases."""

import abc


class Base(abc.ABC):
    """An abstract base."""

    @abc.abstractmethod
    def write(self, name: str) -> int:
        """Write one row."""
        raise NotImplementedError
'''

DECLARATION_DUNDER = '''"""Cases."""

from collections.abc import Iterator
from typing import Protocol


class Thing(Protocol):
    """A contract."""

    def __iter__(self) -> Iterator[int]:
        """Iterate the rows."""
'''

DECLARATION_OMITTING = '''"""Cases."""

from typing import Protocol


class Thing(Protocol):
    """A contract."""

    def write(self, name: str) -> int:
        """Write one row."""
'''

DECLARATION_STATING = '''"""Cases."""

from typing import Protocol


class Thing(Protocol):
    """A contract."""

    def write(self, name: str) -> int:
        """Write one row.

        Args:
            name: The row's name.

        Returns:
            Which row.
        """


class Methods:
    """An implementation, structurally rather than by inheritance."""

    def write(self, name: str) -> int:
        """Write one row."""
        return len(name)
'''

DECLARATION_UNDOCUMENTED = '''"""Cases."""

from typing import Protocol


class Thing(Protocol):
    """A contract."""

    def write(self, name: str) -> int: ...
'''

# One violation per group, so a run reporting a code proves which group read it: a summary line holding two
# sentences is `DSC003`, `zebra` above `alpha` is `ORD001`, and the document's one link is `LNK001`. Neither
# function takes an argument or returns a value, so nothing here also asks for a section.
GROUP_DOCUMENT = "See [it](missing.md).\n"
GROUP_MODULE = '''"""Cases. And a second sentence."""


def zebra() -> None:
    """A summary."""


def alpha() -> None:
    """A summary."""
'''

TEST_CASES = {
    "slug": {
        "a plain heading lowercases and hyphenates": {
            "args": ["Backend providers"],
            "returns": "backend-providers",
        },
        "a code span in a heading keeps its text and loses its backticks": {
            "args": ["`--dev` and the rest"],
            "returns": "--dev-and-the-rest",
        },
        "punctuation a fragment never carries is dropped rather than hyphenated": {
            "args": ["Registry files: JSON, YAML, or TOML"],
            "returns": "registry-files-json-yaml-or-toml",
        },
        "an apostrophe closes the word rather than splitting it": {
            "args": ["Authenticating an agent's own tools"],
            "returns": "authenticating-an-agents-own-tools",
        },
    },
    "anchors": {
        "every heading level produces a fragment": {
            "args": ["# One\n\n## Two words\n\n### Three\n"],
            "returns": {"one", "two-words", "three"},
        },
        "a repeated heading is numbered the way GitHub numbers it": {
            "args": ["## Same\n\n## Same\n\n## Same\n"],
            "returns": {"same", "same-1", "same-2"},
        },
        "a hash inside a fence is a comment rather than a heading": {
            "args": [f"## Real\n\n{FENCE}bash\n# Not a heading\n{FENCE}\n"],
            "returns": {"real"},
        },
        "a fence drawn with tildes holds a comment rather than a heading": {
            "args": [f"## Real\n\n{TILDE}bash\n# Not a heading\n{TILDE}\n"],
            "returns": {"real"},
        },
        "a run of the other fence character inside a fence is content": {
            "args": [f"## Real\n\n{TILDE}text\n{FENCE}\n## Not a heading\n{FENCE}\n{TILDE}\n"],
            "returns": {"real"},
        },
    },
    "not_a_link": {
        "a fenced block holds an example, so nothing inside it is followed as a link": {
            "args": [f"{FENCE}text\nSee [it](gone.md#anchor).\n{FENCE}\n"],
            "returns": ([], 0),
        },
        "a block fenced with tildes holds an example the same way": {
            "args": [f"{TILDE}text\nSee [it](gone.md#anchor).\n{TILDE}\n"],
            "returns": ([], 0),
        },
        "a code span holds an example of the syntax, so nothing in it is followed": {
            "args": ["Write `](gone.md#anchor)` to point at one.\n"],
            "returns": ([], 0),
        },
        "a URL, a mail link, and a route on the running service are not files to resolve": {
            "args": ["[a](https://example.com) [b](mailto:x@example.com) [c](/redoc)\n"],
            "returns": ([], 0),
        },
    },
    "declarations": {
        "a protocol stub is held to the contract it states": {
            "args": [DECLARATION_OMITTING],
            "returns": ("one.py:9:1: DSC002 `write` omits Args, Returns",),
        },
        # The body is what makes this one carry: it is not a stub, so no shape exempts it and only the name it
        # shares with itself ever did. Its `raise` asks for a section of its own, as any other body's would.
        "an abstract method with a body is held to it as well": {
            "args": [DECLARATION_ABSTRACT],
            "returns": ("one.py:10:1: DSC002 `write` omits Args, Returns, Raises",),
        },
        # The other half of the rule, and the reason the exemption exists: one contract above, a summary line below.
        "a declaration stating its contract leaves its implementation a summary line": {
            "args": [DECLARATION_STATING],
            "returns": (),
        },
        "a declaration carrying no docstring states no contract to hold it to": {
            "args": [DECLARATION_UNDOCUMENTED],
            "returns": (),
        },
        "a dunder declares a contract the language states, not this one": {
            "args": [DECLARATION_DUNDER],
            "returns": (),
        },
    },
    "sentences": {
        "one sentence is the whole summary": {
            "args": ["Read raw bytes from the underlying storage."],
            "returns": ("Read raw bytes from the underlying storage.",),
        },
        "a second sentence is a second sentence": {
            "args": ["Report that the service is up. Deliberately nothing else."],
            "returns": ("Report that the service is up.", "Deliberately nothing else."),
        },
        "a third is reported too": {
            "args": ["One. Two. Three."],
            "returns": ("One.", "Two.", "Three."),
        },
        "a semicolon joins rather than breaks": {
            "args": ["Release whatever the backend holds open; called once, at shutdown."],
            "returns": ("Release whatever the backend holds open; called once, at shutdown.",),
        },
        "a dot inside a code span is not a break": {
            "args": ["The last path segment of every module a tree imports, by `import x.y.z` form."],
            "returns": ("The last path segment of every module a tree imports, by `import x.y.z` form.",),
        },
        "an ellipsis inside a code span is not a break": {
            "args": ["Every module a tree imports, by `import x.y.z` or any `from ... import` form."],
            "returns": ("Every module a tree imports, by `import x.y.z` or any `from ... import` form.",),
        },
        "a bare ellipsis is not a break": {
            "args": ["A trailing ellipsis is not a break ... Read on."],
            "returns": ("A trailing ellipsis is not a break ... Read on.",),
        },
        "an abbreviation before a code span is not a break": {
            "args": ["Options forwarded to agno's own `@tool`, e.g. `external_execution=True`."],
            "returns": ("Options forwarded to agno's own `@tool`, e.g. `external_execution=True`.",),
        },
        "a marker abbreviation is not a break": {
            "args": ["N.B. The wrapper closes a gap `AsyncExitStack` leaves open."],
            "returns": ("N.B. The wrapper closes a gap `AsyncExitStack` leaves open.",),
        },
        "a decimal is not a break": {
            "args": ["Coverage must reach 98.0 percent."],
            "returns": ("Coverage must reach 98.0 percent.",),
        },
        "a lowercase clause after a period is not a break": {
            "args": ["Read the row. and stop."],
            "returns": ("Read the row. and stop.",),
        },
        "a quoted sentence opens a break": {
            "args": ['`null` is nothing declared. "none" is a level an operator chose.'],
            "returns": ("`null` is nothing declared.", '"none" is a level an operator chose.'),
        },
        "only the first line is read": {
            "args": ["A summary.\n\n    A body. Holding two sentences.\n    "],
            "returns": ("A summary.",),
        },
        "a summary under a leading newline is still read": {
            "args": ["\n    A summary. And a second.\n    "],
            "returns": ("A summary.", "And a second."),
        },
        # The documented limitation: initials are indistinguishable from a sentence end. No docstring in this
        # repository hits it, and the fix is to rephrase rather than to suppress.
        "initials read as a break, which is the known false positive": {
            "args": ["Parse a U.S. Federal filing."],
            "returns": ("Parse a U.S.", "Federal filing."),
        },
    },
    "docstrings": {
        "a module docstring is read": {
            "args": ['"""A module. With two."""\n'],
            "returns": (("<module>", 1, "A module. With two."),),
        },
        "a class docstring is read": {
            "args": ['class One:\n    """A class. With two."""\n'],
            "returns": (("One", 2, "A class. With two."),),
        },
        "a function docstring is read": {
            "args": ['def one() -> None:\n    """A function. With two."""\n'],
            "returns": (("one", 2, "A function. With two."),),
        },
        "an async function docstring is read": {
            "args": ['async def one() -> None:\n    """A coroutine. With two."""\n'],
            "returns": (("one", 2, "A coroutine. With two."),),
        },
        "a docstring below a multi-line signature reports its own line": {
            "args": ['def one(\n    first: int,\n) -> None:\n    """A function. With two."""\n'],
            "returns": (("one", 4, "A function. With two."),),
        },
        # The scope boundary: an attribute docstring is a bare string after an assignment, and DSC003 does
        # not read one. The class here carries its own, so the case proves the attribute is passed over
        # rather than that the shape holds no docstring at all.
        "an attribute docstring beside a class docstring is not read": {
            "args": ['class One:\n    """A class summary."""\n\n    A = "a"\n    """An attribute. With two."""\n'],
            "returns": (("One", 2, "A class summary."),),
        },
        "a definition with no docstring yields nothing": {
            "args": ["def one() -> None:\n    return None\n"],
            "returns": (),
        },
    },
    "sections": {
        "a header spelled the way the guide spells it is that section": {"args": ["Returns:"], "returns": "Returns:"},
        "a lowercase header is that section": {"args": ["returns:"], "returns": "Returns:"},
        "a singular header is that section": {"args": ["Return:"], "returns": "Returns:"},
        "a singular raise is that section": {"args": ["Raise:"], "returns": "Raises:"},
        "a singular yield is that section": {"args": ["Yield:"], "returns": "Yields:"},
        "a transposed letter is still that section": {"args": ["Retruns:"], "returns": "Returns:"},
        "a dropped letter is still that section": {"args": ["Arg:"], "returns": "Args:"},
        "a space before the colon is that section": {"args": ["Returns :"], "returns": "Returns:"},
        # Where the cutoff sits: another convention's name for a section is not a misspelling of this one's, and a
        # section this guide does not name is no section here however it is spelled.
        "another convention's name for one is no section": {"args": ["Parameters:"], "returns": None},
        "a section the guide does not name is no section": {"args": ["Attributes:"], "returns": None},
        "a header of two words is no section": {"args": ["Keyword Args:"], "returns": None},
        "a header with no colon is no section": {"args": ["Returns"], "returns": None},
        "a sentence ending in a colon is no section": {"args": ["What it returns:"], "returns": None},
        "a bare colon is no section": {"args": [":"], "returns": None},
    },
    "groups": {
        "no flag runs every group": {"args": [], "returns": ("DSC003", "LNK001", "ORD001")},
        "the docstring group runs alone": {"args": ["--docs"], "returns": ("DSC003",)},
        "the link group runs alone": {"args": ["--links"], "returns": ("LNK001",)},
        "the order group runs alone": {"args": ["--order"], "returns": ("ORD001",)},
        "two named groups run and the third does not": {
            "args": ["--docs", "--links"],
            "returns": ("DSC003", "LNK001"),
        },
    },
    "ignore": {
        "an absent deny list leaves every code on": {"args": [[]], "returns": (1, ("DSC003", "ORD001"))},
        "a denied code stops failing the run": {"args": [["DSC003", "ORD001"]], "returns": (0, ())},
        "a denied code takes nothing else with it": {"args": [["DSC003"]], "returns": (1, ("ORD001",))},
        # The counterpart of `useless-suppression` and `RUF100`: an entry that suppresses nothing reads as
        # load-bearing, so it is refused rather than passed over.
        "a name that is no code is refused": {
            "args": [["DSC999"]],
            "raises": (ValueError, "names no code this tool reports: DSC999"),
        },
    },
}


def test_a_bare_fragment_resolves_against_its_own_document(tmp_path: pathlib.Path) -> None:
    """A link with no path names a heading in the file it is written in."""
    (tmp_path / "a.md").write_text("## Here\n\n[one](#here) and [two](#there).\n", encoding="utf-8")
    findings, checked = pyqa._unresolved([tmp_path / "a.md"], tmp_path)
    assert [one.detail for one in findings] == ["no heading `#there` in `a.md`"]
    assert checked == 2


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["declarations"])
def test_a_declaration_is_held_to_the_contract_its_implementations_point_at(
    test_case: dict,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    function_tester: Callable,
) -> None:
    """A protocol or abstract base states the contract, and its implementations carry a summary line alone.

    Mutation: drop the seeded `declarations` from `_contracted` and let the loop reach them. Every declaration goes
    back to being exempt by the name its implementations share, so the one docstring stating a contract is the one
    nothing reads.
    """

    def reported(source: str) -> tuple[str, ...]:
        """Every DSC002 line one module's source reports, named relative to the temporary root."""
        (tmp_path / "one.py").write_text(source)
        pyqa.main(["--docs", str(tmp_path)])
        printed = capsys.readouterr().out.splitlines()
        return tuple(one.removeprefix(f"{tmp_path}/") for one in printed if ":1: DSC002" in one)

    function_tester(test_case, reported)


def test_a_docstring_in_a_test_module_is_still_read(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture) -> None:
    """DSC002 exempts a test module because its inputs arrive as fixtures; a run-on summary has no such reason.

    Mutation: move the DSC003 pass below `_check_docs`'s `_is_test` skip. Every test module stops being read.
    """
    (tmp_path / "test_one.py").write_text('"""Cases."""\n\n\ndef test_one() -> None:\n    """A case. And more."""\n')
    status = pyqa.main(["--docs", str(tmp_path)])
    reported = [line for line in capsys.readouterr().out.splitlines() if ":1: DSC003" in line]
    assert status == 1
    assert reported == [f"{tmp_path / 'test_one.py'}:5:1: DSC003 `test_one` runs on into And more."]


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["anchors"])
def test_a_document_produces_exactly_the_fragments_a_link_may_name(
    test_case: dict,
    tmp_path: pathlib.Path,
    function_tester: Callable,
) -> None:
    """What a heading offers a link, including a repeat and a fence that offers nothing."""

    def anchors(body: str) -> set[str]:
        """Read one document's headings out of a file holding the given body."""
        path = tmp_path / "one.md"
        path.write_text(body, encoding="utf-8")
        return pyqa._anchors(path)

    function_tester(test_case, anchors)


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["groups"])
def test_a_group_flag_runs_that_check_and_no_other(
    test_case: dict,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    function_tester: Callable,
) -> None:
    """Each flag selects one group over a tree holding one violation of all three, and no flag selects them all.

    Mutation: drop `every` from one group's guard in `main`. That group falls out of the default run, and the gate
    reads clean over a tree it never looked at.
    """

    def reported(*flags: str) -> tuple[str, ...]:
        """Every code one run reports, over a tree holding one violation per group."""
        (tmp_path / "one.py").write_text(GROUP_MODULE)
        (tmp_path / "one.md").write_text(GROUP_DOCUMENT, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        pyqa.main([*flags, "."])
        printed = capsys.readouterr().out.splitlines()
        return tuple(sorted({one.split()[1] for one in printed if ":1: " in one}))

    function_tester(test_case, reported)


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["sections"])
def test_a_header_reads_as_the_section_it_misspells(test_case: dict, function_tester: Callable) -> None:
    """A header near enough to a section's name opens that section, and one further off opens none."""
    function_tester(test_case, pyqa._section_of)


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["slug"])
def test_a_heading_reduces_to_the_fragment_it_is_served_under(
    test_case: dict,
    function_tester: Callable,
) -> None:
    """Markup and punctuation leave the fragment; the words in them stay."""
    function_tester(test_case, pyqa._slug)


def test_a_link_outside_a_fence_that_needs_a_longer_marker_to_close_is_still_checked(
    tmp_path: pathlib.Path,
) -> None:
    """A run of backticks shorter than the one that opened a fence is content, not a close, for the link past it."""
    four = FENCE + "`"
    body = f"# Heading\n\n{four}markdown\n{FENCE}\n{four}\n\n[broken](nope.md)\n"
    (tmp_path / "a.md").write_text(body, encoding="utf-8")
    findings, checked = pyqa._unresolved([tmp_path / "a.md"], tmp_path)
    assert [one.code for one in findings] == ["LNK001"]
    assert checked == 1


def test_a_link_to_a_file_that_is_not_there_is_reported(tmp_path: pathlib.Path) -> None:
    """A target this repository does not hold fails the check, as LNK001."""
    (tmp_path / "a.md").write_text("See [it](gone.md).\n", encoding="utf-8")
    findings, checked = pyqa._unresolved([tmp_path / "a.md"], tmp_path)
    assert [one.code for one in findings] == ["LNK001"]
    assert checked == 1


def test_a_link_to_a_heading_the_target_lacks_is_reported(tmp_path: pathlib.Path) -> None:
    """A fragment no heading produces fails the check, as LNK002."""
    (tmp_path / "a.md").write_text("See [it](b.md#nope).\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("## Here\n", encoding="utf-8")
    findings, _ = pyqa._unresolved([tmp_path / "a.md", tmp_path / "b.md"], tmp_path)
    assert [str(one) for one in findings] == ["a.md:1:1: LNK002 no heading `#nope` in `b.md`"]


def test_a_misspelled_section_in_a_private_docstring_is_reported_as_dsc001(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """A private docstring may carry no section at all, so a misspelled header is one to cut rather than to spell.

    Mutation: report a covered definition through `_misspelled_sections`. The message asks for the header to be
    corrected, which would leave a section behind that `DSC001` exists to remove.
    """
    source = '"""Cases."""\n\n\ndef _one() -> int:\n    """A summary.\n\n    Return:\n        A value.\n    """\n    return 1\n'
    (tmp_path / "one.py").write_text(source)
    status = pyqa.main(["--docs", str(tmp_path)])
    reported = [line for line in capsys.readouterr().out.splitlines() if ":1: DSC" in line]
    assert status == 1
    assert reported == [f"{tmp_path / 'one.py'}:4:1: DSC001 `_one` declares Returns"]


def test_a_misspelled_section_in_a_public_docstring_is_reported_as_dsc004(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """A public definition may carry the section, so the spelling is the thing to fix.

    Mutation: match a header against `SECTIONS` alone in `_section_of`. The header reads as prose, and `DSC002`
    reports the section missing from a docstring that visibly holds it.
    """
    source = '"""Cases."""\n\n\ndef one() -> int:\n    """A summary.\n\n    Return:\n        A value.\n    """\n    return 1\n'
    (tmp_path / "one.py").write_text(source)
    status = pyqa.main(["--docs", str(tmp_path)])
    reported = [line for line in capsys.readouterr().out.splitlines() if ":1: DSC" in line]
    assert status == 1
    assert reported == [f"{tmp_path / 'one.py'}:4:1: DSC004 `one` misspells Returns"]


def test_a_one_sentence_summary_passes(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture) -> None:
    """A summary holding one sentence is what the gate is asking for.

    Mutation: treat a semicolon as a sentence end. This line reports, and every joined summary in the tree does.
    """
    (tmp_path / "one.py").write_text('"""Report that the service is up; deliberately nothing else."""\n')
    status = pyqa.main(["--docs", str(tmp_path)])
    assert status == 0
    assert ":1: DSC003" not in capsys.readouterr().out


def test_a_section_spelled_as_the_guide_spells_it_is_not_reported(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """A header the guide spells this way is the section it opens, and nothing to report.

    Mutation: drop the `line not in SECTIONS` filter from `_misspelled_sections`. Every correct header in the tree
    reports as a misspelling of itself.
    """
    source = '"""Cases."""\n\n\ndef one() -> int:\n    """A summary.\n\n    Returns:\n        A value.\n    """\n    return 1\n'
    (tmp_path / "one.py").write_text(source)
    status = pyqa.main(["--docs", str(tmp_path)])
    assert status == 0
    assert ":1: DSC" not in capsys.readouterr().out


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["sentences"])
def test_a_summary_line_splits_into_the_sentences_it_holds(test_case: dict, function_tester: Callable) -> None:
    """A period that ends a sentence breaks the line, and every other period does not."""
    function_tester(test_case, pyqa._sentences)


def test_a_two_sentence_summary_is_reported_as_dsc003(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture) -> None:
    """The gate refuses a run-on summary and names the sentence to move.

    Mutation: return the whole line from `_sentences` rather than splitting it. Nothing is ever reported.
    """
    (tmp_path / "one.py").write_text('"""Report that the service is up. Deliberately nothing else."""\n')
    status = pyqa.main(["--docs", str(tmp_path)])
    reported = [line for line in capsys.readouterr().out.splitlines() if ":1: DSC003" in line]
    assert status == 1
    assert reported == [f"{tmp_path / 'one.py'}:1:1: DSC003 `<module>` runs on into Deliberately nothing else."]


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["ignore"])
def test_an_ignored_code_is_counted_and_never_failed_on(
    test_case: dict,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    function_tester: Callable,
) -> None:
    """A code the deny list names is still read and still printed under `ignoring`, and stops failing the run.

    Mutation: filter the findings before `_report` counts them. A wholly suppressed run reports nothing and the
    line saying what was swallowed goes with it, so the output reads exactly like a clean one.
    """

    def reported(ignore: list[str]) -> tuple[int, tuple[str, ...]]:
        """The status one deny list leaves, and the codes that still reported under it."""
        (tmp_path / "one.py").write_text(GROUP_MODULE)
        (tmp_path / "pyproject.toml").write_text(f"[tool.pyqa]\nignore = {json.dumps(ignore)}\n")
        monkeypatch.chdir(tmp_path)
        status = pyqa.main(["--docs", "--order", "."])
        printed = capsys.readouterr().out.splitlines()
        assert not ignore or f"ignoring {', '.join(sorted(ignore))}" in "\n".join(printed)
        return status, tuple(sorted({one.split()[1] for one in printed if ":1: " in one}))

    function_tester(test_case, reported)


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["docstrings"])
def test_every_shape_holding_a_docstring_reports_its_summary(test_case: dict, function_tester: Callable) -> None:
    """Modules, classes and functions are read; an attribute docstring is not."""

    def docstrings(source: str) -> tuple:
        """Read every docstring out of one module's source."""
        return tuple(pyqa._docstrings(ast.parse(source)))

    function_tester(test_case, docstrings)


def test_fix_is_refused_without_the_order_group(capsys: pytest.CaptureFixture) -> None:
    """`--fix` sorts what `--order` reports, so naming another group alone beside it would sort nothing.

    Mutation: drop the guard from `main`. The run reports the group that was named and silently passes over the
    flag that was asked for, which reads as a fixer that found nothing to fix.
    """
    with pytest.raises(SystemExit) as refused:
        pyqa.main(["--fix", "--docs"])
    assert refused.value.code == 2
    assert "--fix sorts what --order reports" in capsys.readouterr().err


def test_fix_sorts_the_module_it_would_have_reported(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture) -> None:
    """`--fix` puts a file in the order a report asked for, says how many moved, and leaves nothing to report.

    Mutation: return `source` from `_rewrite` whatever the sort produced. The summary counts nothing sorted, and
    the file on disk keeps the order it had.
    """
    path = tmp_path / "one.py"
    path.write_text(GROUP_MODULE)
    status = pyqa.main(["--order", "--fix", str(tmp_path)])
    sorted_source = path.read_text()
    assert status == 0
    assert capsys.readouterr().out.splitlines() == [f"scanned 1 file(s) under {tmp_path}, sorted 1"]
    assert sorted_source.index("def alpha") < sorted_source.index("def zebra")
    assert pyqa.main(["--order", str(tmp_path)]) == 0
