"""Cases for the definition order, docstring shape and document link checks."""

import ast
import functools
import json
import pathlib
import re
import runpy
import sys
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
# sentences is `DSC003`, `zebra` above `alpha` is `ORD001`, the `print()` inside `zebra` is `DBG001`, and the
# document's one link is `LNK001`. Neither function takes an argument or returns a value, so nothing here also asks
# for a section.
GROUP_DOCUMENT = "See [it](missing.md).\n"
GROUP_MODULE = '''"""Cases. And a second sentence."""


def zebra() -> None:
    """A summary."""
    print()


def alpha() -> None:
    """A summary."""
'''

# A private function stating its whole contract, under whichever decorator a case names.
TOOL_MODULE = '''"""Cases."""


{decorator}
def _search(query: str) -> str:
    """Search the index.

    Args:
        query: What to look for.

    Returns:
        The best match.
    """
    return query
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
            "args": ["Authenticating a file's own tools"],
            "returns": "authenticating-a-files-own-tools",
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
            "returns": [],
        },
        "a block fenced with tildes holds an example the same way": {
            "args": [f"{TILDE}text\nSee [it](gone.md#anchor).\n{TILDE}\n"],
            "returns": [],
        },
        "a code span holds an example of the syntax, so nothing in it is followed": {
            "args": ["Write `](gone.md#anchor)` to point at one.\n"],
            "returns": [],
        },
        "a URL, a mail link, and a route on the running service are not files to resolve": {
            "args": ["[a](https://example.com) [b](mailto:x@example.com) [c](/redoc)\n"],
            "returns": [],
        },
    },
    "debugging": {
        "a print at column 0 is reported": {
            "args": ["print(1)\n"],
            "returns": ("one.py:1:1: DBG001 `print()` called (debug-print)",),
        },
        "an indented print is reported": {
            "args": ["def one() -> None:\n    print(1)\n"],
            "returns": ("one.py:2:1: DBG001 `print()` called (debug-print)",),
        },
        "a print whose value is kept is reported all the same": {
            "args": ["VALUE = print(1)\n"],
            "returns": ("one.py:1:1: DBG001 `print()` called (debug-print)",),
        },
        "a breakpoint is reported": {
            "args": ["breakpoint()\n"],
            "returns": ("one.py:1:1: DBG002 `breakpoint()` called (debug-breakpoint)",),
        },
        "a pdb trace is reported": {
            "args": ["import pdb\n\npdb.set_trace()\n"],
            "returns": ("one.py:3:1: DBG002 `pdb.set_trace()` called (debug-breakpoint)",),
        },
        "a method named print is not the builtin": {"args": ["self.print(1)\n"], "returns": ()},
        "a private print helper is not the builtin": {"args": ["_print(1)\n"], "returns": ()},
        "the builtin reached through its module is deliberate output": {
            "args": ["import builtins\n\nbuiltins.print(1)\n"],
            "returns": (),
        },
        "defining a function named print calls nothing": {"args": ["def print() -> None: ...\n"], "returns": ()},
        "a reference that is never called is not a call": {"args": ["CALLBACK = print\n"], "returns": ()},
        "a print inside a string is text": {"args": ['MESSAGE = "call print(1) to see it"\n'], "returns": ()},
        "a breakpoint inside a docstring is text": {
            "args": ['"""Stop at breakpoint() when asked."""\n'],
            "returns": (),
        },
        "a print inside a comment is text": {"args": ["# print(1)\n"], "returns": ()},
        "a marker naming the code exempts the call": {"args": ["print(1)  # pyqa: disable=DBG001\n"], "returns": ()},
        "a marker naming the name exempts the call": {
            "args": ["print(1)  # pyqa: disable=debug-print\n"],
            "returns": (),
        },
        "a marker naming the name among others exempts the call": {
            "args": ["print(1)  # pyqa: disable=doc-run-on-summary, debug-print\n"],
            "returns": (),
        },
        "a marker's entries may carry whitespace on either side of a comma": {
            "args": ["print(1)  # pyqa: disable=DSC003 ,debug-print\n"],
            "returns": (),
        },
        "a marker with no space after its colon or comma exempts the call": {
            "args": ["print(1)  #pyqa:disable=DSC003,DBG001\n"],
            "returns": (),
        },
        "entries separated by whitespace alone end at the first": {
            "args": ["print(1)  # pyqa: disable=DSC003 debug-print\n"],
            "returns": ("one.py:1:1: DBG001 `print()` called (debug-print)",),
        },
        "prose after a marker's entries is not read": {
            "args": ["print(1)  # pyqa: disable=debug-print for the command line\n"],
            "returns": (),
        },
        "a ruff noqa after a marker is not read as an entry": {
            "args": ["print(1)  # pyqa: disable=debug-print  # noqa: E501\n"],
            "returns": (),
        },
        "a marker after a ruff noqa is still read": {
            "args": ["print(1)  # noqa: E501  # pyqa: disable=debug-print\n"],
            "returns": (),
        },
        "a long method chain is passed over without recursing": {
            "args": ["VALUE = q" + "".join(f".m{index}(1)" for index in range(200)) + "\nprint(1)\n"],
            "returns": ("one.py:2:1: DBG001 `print()` called (debug-print)",),
        },
        "a long method chain inside a subscripted callee is passed over without recursing": {
            "args": ["x[q" + "".join(f".m{index}(1)" for index in range(200)) + "](1)\nprint(1)\n"],
            "returns": ("one.py:2:1: DBG001 `print()` called (debug-print)",),
        },
        "a marker on the line a call opens on exempts the whole call": {
            "args": ["print(  # pyqa: disable=debug-print\n    1,\n)\n"],
            "returns": (),
        },
        "one marker exempts every call opening on its line": {
            "args": ["print(1), print(2)  # pyqa: disable=DBG001\n"],
            "returns": (),
        },
        "a print's marker naming the debugger code exempts nothing": {
            "args": ["print(1)  # pyqa: disable=debug-breakpoint\n"],
            "returns": (
                "one.py:1:1: DBG001 `print()` called (debug-print)",
                "one.py:1:1: MRK001 `# pyqa: disable=debug-breakpoint` exempts nothing (marker-unused)",
            ),
        },
        "a breakpoint's marker naming the print code exempts nothing": {
            "args": ["breakpoint()  # pyqa: disable=DBG001\n"],
            "returns": (
                "one.py:1:1: DBG002 `breakpoint()` called (debug-breakpoint)",
                "one.py:1:1: MRK001 `# pyqa: disable=DBG001` exempts nothing (marker-unused)",
            ),
        },
        "one marker naming both codes exempts a print and a breakpoint on its line": {
            "args": ["print(1), breakpoint()  # pyqa: disable=debug-print, DBG002\n"],
            "returns": (),
        },
        "a ruff noqa naming the code is no marker": {
            "args": ["print(1)  # noqa: DBG001\n"],
            "returns": ("one.py:1:1: DBG001 `print()` called (debug-print)",),
        },
        "pylint's marker is no pyqa marker": {
            "args": ["print(1)  # pylint: disable=debug-print\n"],
            "returns": ("one.py:1:1: DBG001 `print()` called (debug-print)",),
        },
        "a marker with no entry is no marker": {
            "args": ["print(1)  # pyqa: disable=\n"],
            "returns": ("one.py:1:1: DBG001 `print()` called (debug-print)",),
        },
        "a marker naming only other codes exempts nothing": {
            "args": ["print(1)  # pyqa: disable=DSC003\n"],
            "returns": ("one.py:1:1: DBG001 `print()` called (debug-print)",),
        },
        "an uppercase marker exempts nothing": {
            "args": ["print(1)  # PYQA: disable=DBG001\n"],
            "returns": ("one.py:1:1: DBG001 `print()` called (debug-print)",),
        },
        "a name in another case names nothing": {
            "args": ["print(1)  # pyqa: disable=Debug-Print\n"],
            "returns": (
                "one.py:1:1: DBG001 `print()` called (debug-print)",
                "one.py:1:1: MRK001 `# pyqa: disable=Debug-Print` exempts nothing (marker-unused)",
            ),
        },
        "a marker inside a string on the call's line is no comment": {
            "args": ['print("# pyqa: disable=debug-print")\n'],
            "returns": ("one.py:1:1: DBG001 `print()` called (debug-print)",),
        },
    },
    "stale": {
        "a marker on a line holding no call is stale": {
            "args": ["VALUE = 1  # pyqa: disable=DBG002\n"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=DBG002` exempts nothing (marker-unused)",),
        },
        "a marker on a later line of a call is stale, and the call still reports": {
            "args": ["print(\n    1,  # pyqa: disable=debug-print\n)\n"],
            "returns": (
                "one.py:1:1: DBG001 `print()` called (debug-print)",
                "one.py:2:1: MRK001 `# pyqa: disable=debug-print` exempts nothing (marker-unused)",
            ),
        },
        "a code this tool does not report is stale beside one it does": {
            "args": ["print(1)  # pyqa: disable=DBG001, DBG999\n"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=DBG999` exempts nothing (marker-unused)",),
        },
        "a lone stale code beside a real call reports with the call": {
            "args": ["print(1)  # pyqa: disable=DBG999\n"],
            "returns": (
                "one.py:1:1: DBG001 `print()` called (debug-print)",
                "one.py:1:1: MRK001 `# pyqa: disable=DBG999` exempts nothing (marker-unused)",
            ),
        },
        "a name no code carries is stale": {
            "args": ["print(1)  # pyqa: disable=debug-prnit\n"],
            "returns": (
                "one.py:1:1: DBG001 `print()` called (debug-print)",
                "one.py:1:1: MRK001 `# pyqa: disable=debug-prnit` exempts nothing (marker-unused)",
            ),
        },
        "every stale entry is named, in the order written": {
            "args": ["VALUE = 1  # pyqa: disable=DBG999, debug-print\n"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=DBG999, debug-print` exempts nothing (marker-unused)",),
        },
        "a code one marker names twice is stale the second time": {
            "args": ["print(1)  # pyqa: disable=DBG001, debug-print\n"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=debug-print` exempts nothing (marker-unused)",),
        },
        "a marker written inside a string is no marker": {
            "args": ['VALUE = "# pyqa: disable=DBG002"\n'],
            "returns": (),
        },
        "a ruff noqa naming a pyqa code is ruff's alone": {"args": ["VALUE = 1  # noqa: DBG002\n"], "returns": ()},
    },
    "declarations": {
        "a protocol stub is held to the contract it states": {
            "args": [DECLARATION_OMITTING],
            "returns": ("one.py:9:1: DSC002 `write` omits Args, Returns (doc-missing-section)",),
        },
        # The body is what makes this one carry: it is not a stub, so no shape exempts it and only the name it
        # shares with itself ever did. Its `raise` asks for a section of its own, as any other body's would.
        "an abstract method with a body is held to it as well": {
            "args": [DECLARATION_ABSTRACT],
            "returns": ("one.py:10:1: DSC002 `write` omits Args, Returns, Raises (doc-missing-section)",),
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
            "args": ["Options forwarded to library's own `@tool`, e.g. `external_execution=True`."],
            "returns": ("Options forwarded to library's own `@tool`, e.g. `external_execution=True`.",),
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
            "returns": (("<module>", 1, 1, "A module. With two."),),
        },
        "a class docstring is read": {
            "args": ['class One:\n    """A class. With two."""\n'],
            "returns": (("One", 2, 2, "A class. With two."),),
        },
        "a function docstring is read": {
            "args": ['def one() -> None:\n    """A function. With two."""\n'],
            "returns": (("one", 2, 2, "A function. With two."),),
        },
        "an async function docstring is read": {
            "args": ['async def one() -> None:\n    """A coroutine. With two."""\n'],
            "returns": (("one", 2, 2, "A coroutine. With two."),),
        },
        "a docstring below a multi-line signature reports its own line": {
            "args": ['def one(\n    first: int,\n) -> None:\n    """A function. With two."""\n'],
            "returns": (("one", 4, 4, "A function. With two."),),
        },
        "a docstring spanning lines reports where it opens and where it closes": {
            "args": ['def one() -> None:\n    """A function. With two.\n\n    More.\n    """\n'],
            "returns": (("one", 2, 5, "A function. With two.\n\n    More.\n    "),),
        },
        # The scope boundary: an attribute docstring is a bare string after an assignment, and DSC003 does
        # not read one. The class here carries its own, so the case proves the attribute is passed over
        # rather than that the shape holds no docstring at all.
        "an attribute docstring beside a class docstring is not read": {
            "args": ['class One:\n    """A class summary."""\n\n    A = "a"\n    """An attribute. With two."""\n'],
            "returns": (("One", 2, 2, "A class summary."),),
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
    "tools": {
        "a bare `tool` decorator states a contract the model reads": {"args": ["@tool"], "returns": ()},
        "a called `tool` decorator states one too": {"args": ["@tool(strict=True)"], "returns": ()},
        "an file's `tool` decorator states one too": {"args": ["@file.tool"], "returns": ()},
        "a called file's `tool` decorator states one too": {"args": ["@file.tool(strict=True)"], "returns": ()},
        "any other decorator leaves a private docstring holding no section": {
            "args": ["@functools.cache"],
            "returns": ("DSC001",),
        },
    },
    "groups": {
        "no flag runs every group": {"args": [], "returns": ("DBG001", "DSC003", "LNK001", "ORD001")},
        "the debugging group runs alone": {"args": ["--debugging"], "returns": ("DBG001",)},
        "the docstring group runs alone": {"args": ["--docs"], "returns": ("DSC003",)},
        "the link group runs alone": {"args": ["--links"], "returns": ("LNK001",)},
        "the order group runs alone": {"args": ["--order"], "returns": ("ORD001",)},
        "two named groups run and the others do not": {
            "args": ["--docs", "--links"],
            "returns": ("DSC003", "LNK001"),
        },
    },
    "config": {
        "no config leaves every code on": {"args": [{}], "returns": (1, ("DBG001", "DSC003", "LNK001", "ORD001"), ())},
        "an ignored prefix turns off its family": {
            "args": [{"ignore": ["DSC"]}],
            "returns": (1, ("DBG001", "LNK001", "ORD001"), ("DSC001", "DSC002", "DSC003", "DSC004")),
        },
        "a code ignored inside a selected family wins, being more specific": {
            "args": [{"select": ["DSC"], "ignore": ["DSC003"]}],
            "returns": (0, (), ("DBG001", "DBG002", "DSC003", "LNK001", "LNK002", "MRK001", "ORD001", "ORD002")),
        },
        "a code selected inside an ignored family wins, being more specific": {
            "args": [{"select": ["DSC003"], "ignore": ["DSC"]}],
            "returns": (
                1,
                ("DSC003",),
                ("DBG001", "DBG002", "DSC001", "DSC002", "DSC004", "LNK001", "LNK002", "MRK001", "ORD001", "ORD002"),
            ),
        },
        "a one-letter prefix spans the families it opens": {
            "args": [{"select": ["D"], "ignore": ["DBG001"]}],
            "returns": (1, ("DSC003",), ("DBG001", "LNK001", "LNK002", "MRK001", "ORD001", "ORD002")),
        },
        "a longer prefix outweighs a shorter one": {
            "args": [{"select": ["DSC00"], "ignore": ["DS"]}],
            "returns": (1, ("DSC003",), ("DBG001", "DBG002", "LNK001", "LNK002", "MRK001", "ORD001", "ORD002")),
        },
        "a tie goes to ignore": {
            "args": [{"select": ["DSC003"], "ignore": ["doc-run-on-summary"]}],
            "returns": (
                0,
                (),
                (
                    "DBG001",
                    "DBG002",
                    "DSC001",
                    "DSC002",
                    "DSC003",
                    "DSC004",
                    "LNK001",
                    "LNK002",
                    "MRK001",
                    "ORD001",
                    "ORD002",
                ),
            ),
        },
        "an empty selection turns every code off": {
            "args": [{"select": []}],
            "returns": (
                0,
                (),
                (
                    "DBG001",
                    "DBG002",
                    "DSC001",
                    "DSC002",
                    "DSC003",
                    "DSC004",
                    "LNK001",
                    "LNK002",
                    "MRK001",
                    "ORD001",
                    "ORD002",
                ),
            ),
        },
        "a name selects the code it names": {
            "args": [{"select": ["debug-print"]}],
            "returns": (
                1,
                ("DBG001",),
                ("DBG002", "DSC001", "DSC002", "DSC003", "DSC004", "LNK001", "LNK002", "MRK001", "ORD001", "ORD002"),
            ),
        },
        "codes and names mix in one list": {
            "args": [{"ignore": ["order-alpha", "DSC003"]}],
            "returns": (1, ("DBG001", "LNK001"), ("DSC003", "ORD001")),
        },
        # The counterpart of `useless-suppression` and `RUF100`: an entry that chooses nothing reads as load-bearing, so
        # it is refused rather than passed over.
        "a selector matching no code is refused": {
            "args": [{"select": ["DSX"]}],
            "raises": (
                ValueError,
                "select` in .*pyproject\\.toml names no code, name or prefix this tool reports: DSX",
            ),
        },
        "a name no code carries is refused": {
            "args": [{"ignore": ["doc-run-on"]}],
            "raises": (
                ValueError,
                "ignore` in .*pyproject\\.toml names no code, name or prefix this tool reports: doc-run-on",
            ),
        },
        "an empty selector is refused": {
            "args": [{"ignore": [""]}],
            "raises": (ValueError, "pyproject\\.toml names no code, name or prefix this tool reports: $"),
        },
        "a key the table does not take is refused": {
            "args": [{"ignor": ["DSC003"]}],
            "raises": (ValueError, "pyproject\\.toml has no key named: ignor"),
        },
        "a selector list written as one string is refused": {
            "args": [{"ignore": "DSC003"}],
            "raises": (ValueError, "ignore` in .*pyproject\\.toml must be a list of strings"),
        },
        "an exclude list written as one string is refused": {
            "args": [{"exclude": "src"}],
            "raises": (ValueError, "exclude` in .*pyproject\\.toml must be a list of strings"),
        },
        "an exclude pattern opening at a slash can never match, and is refused": {
            "args": [{"exclude": ["/src/gen"]}],
            "raises": (ValueError, "exclude` in .*pyproject\\.toml holds a pattern that can never match: /src/gen"),
        },
        "an exclude pattern climbing out with two dots can never match, and is refused": {
            "args": [{"exclude": ["../vendor"]}],
            "raises": (ValueError, "can never match: \\.\\./vendor"),
        },
        "an exclude pattern with an empty segment can never match, and is refused": {
            "args": [{"exclude": ["src//gen"]}],
            "raises": (ValueError, "can never match: src//gen"),
        },
        "a selector that is not a string is refused": {
            "args": [{"select": [3]}],
            "raises": (ValueError, "select` in .*pyproject\\.toml must be a list of strings"),
        },
    },
    "summary": {
        "a group reading modules counts them, the codes it checked and its findings": {
            "args": [{"one.py": '"""Cases."""\n'}, ["--docs"], {}],
            "returns": "scanned 1 module under .: 5 codes checked, 0 findings",
        },
        "the link group counts documents, and runs no marker pass": {
            "args": [{"one.md": GROUP_DOCUMENT}, ["--links"], {}],
            "returns": "scanned 1 document under .: 2 codes checked, 1 finding",
        },
        "every group counts both kinds of file and every code": {
            "args": [{"one.py": GROUP_MODULE, "one.md": GROUP_DOCUMENT}, [], {}],
            "returns": "scanned 1 module and 1 document under .: 11 codes checked, 4 findings",
        },
        "more than one of each takes the plural": {
            "args": [
                {"one.py": GROUP_MODULE, "two.py": GROUP_MODULE, "one.md": GROUP_DOCUMENT, "two.md": GROUP_DOCUMENT},
                ["--docs", "--links"],
                {},
            ],
            "returns": "scanned 2 modules and 2 documents under .: 7 codes checked, 4 findings",
        },
        "a run over nothing still says how much it read": {
            "args": [{}, ["--docs"], {}],
            "returns": "scanned 0 modules under .: 5 codes checked, 0 findings",
        },
        "one code checked takes the singular": {
            "args": [{"one.py": '"""Cases."""\n'}, ["--debugging"], {"select": ["DBG001"]}],
            "returns": "scanned 1 module under .: 1 code checked, 0 findings",
        },
        "every call a marker exempts is counted, however many share its line": {
            "args": [
                {
                    "one.py": "print(1)\nprint(2); print(3)  # pyqa: disable=DBG001\nbreakpoint()  # pyqa: disable=debug-breakpoint\n",
                },
                ["--debugging"],
                {},
            ],
            "returns": "scanned 1 module under .: 3 codes checked, 1 finding, 3 exempted",
        },
        "a docstring finding a marker exempts is counted beside the findings": {
            "args": [{"one.py": '"""Cases. And a second."""  # pyqa: disable=doc-run-on-summary\n'}, ["--docs"], {}],
            "returns": "scanned 1 module under .: 5 codes checked, 0 findings, 1 exempted",
        },
        "a marker entry exempting nothing counts as a finding": {
            "args": [{"one.py": "VALUE = 1  # pyqa: disable=DBG002, link-missing-file\n"}, ["--debugging"], {}],
            "returns": "scanned 1 module under .: 3 codes checked, 1 finding",
        },
        "a finding the config turns off is counted as suppressed": {
            "args": [{"one.py": GROUP_MODULE}, ["--docs"], {"ignore": ["DSC003"]}],
            "returns": "scanned 1 module under .: 4 codes checked, 0 findings, 1 suppressed",
        },
        "the exempted count comes before the suppressed one": {
            "args": [
                {"one.py": "print(1)  # pyqa: disable=DBG001\nbreakpoint()\n"},
                ["--debugging"],
                {"ignore": ["DBG002"]},
            ],
            "returns": "scanned 1 module under .: 2 codes checked, 0 findings, 1 exempted, 1 suppressed",
        },
        "fix alone checks no code and says how many modules it sorted": {
            "args": [{"one.py": GROUP_MODULE}, ["--order", "--fix"], {}],
            "returns": "scanned 1 module under .: sorted 1",
        },
        "fix with both order codes off says sorting is off": {
            "args": [{"one.py": GROUP_MODULE}, ["--order", "--fix"], {"select": ["DSC"]}],
            "returns": "scanned 1 module under .: sorting off",
        },
        "fix beside the other groups closes the line": {
            "args": [{"one.py": GROUP_MODULE, "one.md": GROUP_DOCUMENT}, ["--fix"], {}],
            "returns": "scanned 1 module and 1 document under .: 9 codes checked, 3 findings, sorted 1",
        },
    },
    "layout": {
        "a clean run prints its summary alone": {
            "args": [{"one.py": '"""Cases."""\n'}, ["--docs"], {}],
            "returns": (0, ("scanned 1 module under .: 5 codes checked, 0 findings",)),
        },
        "a clean run under a config turning a code off prints its summary alone": {
            "args": [{"one.py": GROUP_MODULE}, ["--docs"], {"ignore": ["DSC003"]}],
            "returns": (0, ("scanned 1 module under .: 4 codes checked, 0 findings, 1 suppressed",)),
        },
        "a failing run prints its findings in group order, then its summary, its tally and each rule": {
            "args": [{"one.py": GROUP_MODULE, "one.md": GROUP_DOCUMENT}, ["--docs", "--links"], {}],
            "returns": (
                1,
                (
                    "one.py:1:1: DSC003 `<module>` runs on into And a second sentence. (doc-run-on-summary)",
                    "one.md:1:1: LNK001 no such file `missing.md` (link-missing-file)",
                    "",
                    "scanned 1 module and 1 document under .: 7 codes checked, 2 findings",
                    "",
                    "reported 1 DSC003 (doc-run-on-summary), 1 LNK001 (link-missing-file)",
                    "",
                    f"DSC003 (doc-run-on-summary)  {pyqa.RULES['DSC003']}",
                    "",
                    f"LNK001 (link-missing-file)  {pyqa.RULES['LNK001']}",
                ),
            ),
        },
    },
    "exclude": {
        "a directory excludes everything beneath it": {
            "args": [
                ["src/pkg/shared/a.py", "src/pkg/shared/deep/b.py", "src/pkg/c.py"],
                ["src/pkg/shared"],
                ["src"],
                ".",
            ],
            "returns": ("src/pkg/c.py",),
        },
        "a file excludes itself alone": {
            "args": [["src/pkg/c.py", "src/pkg/d.py"], ["src/pkg/c.py"], ["src"], "."],
            "returns": ("src/pkg/d.py",),
        },
        "a star stays within one segment": {
            "args": [["src/pkg/gen_a.py", "src/pkg/sub/gen_b.py", "src/pkg/c.py"], ["src/*/gen_*.py"], ["src"], "."],
            "returns": ("src/pkg/c.py", "src/pkg/sub/gen_b.py"),
        },
        "a double star spans any number of segments": {
            "args": [["src/pkg/gen_a.py", "src/pkg/sub/gen_b.py", "src/pkg/c.py"], ["src/**/gen_*.py"], ["src"], "."],
            "returns": ("src/pkg/c.py",),
        },
        "a name with no slash matches at any depth": {
            "args": [["src/generated/a.py", "src/pkg/generated/b.py", "src/pkg/c.py"], ["generated"], ["src"], "."],
            "returns": ("src/pkg/c.py",),
        },
        "a leading dot-slash and a trailing slash are dropped": {
            "args": [
                ["src/pkg/shared/a.py", "src/pkg/shared/deep/b.py", "src/pkg/c.py"],
                ["./src/pkg/shared/"],
                ["src"],
                ".",
            ],
            "returns": ("src/pkg/c.py",),
        },
        "a leading dot-slash keeps a name at the config folder": {
            "args": [["generated/a.py", "src/generated/b.py", "src/c.py"], ["./generated"], ["."], "."],
            "returns": ("src/c.py", "src/generated/b.py"),
        },
        "a trailing slash keeps a name at the config folder": {
            "args": [["generated/a.py", "src/generated/b.py", "src/c.py"], ["generated/"], ["."], "."],
            "returns": ("src/c.py", "src/generated/b.py"),
        },
        "a file named as a root is read though a pattern covers it": {
            "args": [
                ["src/pkg/shared/a.py", "src/pkg/shared/deep/b.py", "src/pkg/c.py"],
                ["src/pkg/shared"],
                ["src/pkg/shared/a.py"],
                ".",
            ],
            "returns": ("src/pkg/shared/a.py",),
        },
        "a directory named as a root is not itself matched": {
            "args": [["src/pkg/a.py"], ["pkg"], ["src/pkg"], "."],
            "returns": ("src/pkg/a.py",),
        },
        "a pattern is read from the folder holding the config, not the working directory": {
            "args": [
                ["src/pkg/shared/a.py", "src/pkg/shared/deep/b.py", "src/pkg/c.py"],
                ["src/pkg/shared"],
                ["pkg"],
                "src",
            ],
            "returns": ("src/pkg/c.py",),
        },
        "the built-in skips still apply beside exclude": {
            "args": [["src/build/a.py", "src/pkg/shared/b.py", "src/pkg/c.py"], ["src/pkg/shared"], ["src"], "."],
            "returns": ("src/pkg/c.py",),
        },
        "a pattern matching nothing is no error": {
            "args": [["src/pkg/c.py"], ["nowhere"], ["src"], "."],
            "returns": ("src/pkg/c.py",),
        },
    },
    "glob": {
        "a segment matches as fnmatch reads it": {"args": [("src", "*.py"), ("src", "a.py")], "returns": True},
        "a star does not cross a segment": {"args": [("src", "*"), ("src", "pkg", "a.py")], "returns": False},
        "a double star matches no segment at all": {"args": [("src", "**", "a.py"), ("src", "a.py")], "returns": True},
        "a double star matches many segments": {
            "args": [("src", "**", "a.py"), ("src", "x", "y", "a.py")],
            "returns": True,
        },
        "a double star alone matches anything": {"args": [("**",), ("x", "y")], "returns": True},
        "a pattern longer than the path does not match": {"args": [("src", "pkg"), ("src",)], "returns": False},
        "a path longer than the pattern does not match": {"args": [("src",), ("src", "pkg")], "returns": False},
    },
    "malformed": {
        "a syntax error names the file it is in": {
            "args": ["[tool.pyqa\n"],
            "raises": (ValueError, "pyproject\\.toml is not valid TOML"),
        },
        "a pyqa that is no table is refused": {
            "args": ["[tool]\npyqa = 3\n"],
            "raises": (ValueError, "pyqa\\]` in .*pyproject\\.toml must be a table"),
        },
        "a pyqa written as a list is refused": {
            "args": ['[tool]\npyqa = ["select"]\n'],
            "raises": (ValueError, "pyqa\\]` in .*pyproject\\.toml must be a table"),
        },
        "a tool that is no table is refused": {
            "args": ["tool = 3\n"],
            "raises": (ValueError, "pyqa\\]` in .*pyproject\\.toml must be a table"),
        },
    },
    "fix_off": {
        "both order codes off leaves the module unsorted": {"args": [{"select": ["DSC"]}], "returns": (0, False)},
        "one order code off still sorts": {"args": [{"ignore": ["ORD002"]}], "returns": (0, True)},
    },
    "docs_markers": {
        "a private docstring's section is exempted on its def line": {
            "args": [
                '"""Cases."""\n\n\ndef _one() -> int:  # pyqa: disable=doc-extra-section\n    """A summary.\n\n    Returns:\n        A value.\n    """\n    return 1\n'
            ],
            "returns": (),
        },
        "a missing section is exempted on its def line": {
            "args": [
                '"""Cases."""\n\n\ndef one(value: int) -> None:  # pyqa: disable=DSC002\n    """A summary."""\n    assert value\n'
            ],
            "returns": (),
        },
        "a decorated definition is exempted on its def line": {
            "args": [
                '"""Cases."""\n\nimport functools\n\n\n@functools.cache\ndef one(value: int) -> int:  # pyqa: disable=doc-missing-section\n    """A summary."""\n    return value\n'
            ],
            "returns": (),
        },
        "a marker on a decorator's line exempts nothing": {
            "args": [
                '"""Cases."""\n\nimport functools\n\n\n@functools.cache  # pyqa: disable=DSC002\ndef one(value: int) -> int:\n    """A summary."""\n    return value\n'
            ],
            "returns": (
                "one.py:7:1: DSC002 `one` omits Args, Returns (doc-missing-section)",
                "one.py:6:1: MRK001 `# pyqa: disable=DSC002` exempts nothing (marker-unused)",
            ),
        },
        "a misspelled header is exempted on its def line": {
            "args": [
                '"""Cases."""\n\n\ndef one() -> int:  # pyqa: disable=DSC004\n    """A summary.\n\n    Return:\n        A value.\n    """\n    return 1\n'
            ],
            "returns": (),
        },
        "a one-line docstring's run-on summary is exempted on its line": {
            "args": ['"""Cases. And a second."""  # pyqa: disable=doc-run-on-summary\n'],
            "returns": (),
        },
        "a multi-line docstring's run-on summary is exempted on its closing line": {
            "args": ['"""Cases. And a second.\n\nMore.\n"""  # pyqa: disable=DSC003\n'],
            "returns": (),
        },
        "a run-on summary's marker on its def line exempts nothing": {
            "args": [
                '"""Cases."""\n\n\ndef one() -> None:  # pyqa: disable=doc-run-on-summary\n    """A summary. And a second.\n\n    More.\n    """\n'
            ],
            "returns": (
                "one.py:5:1: DSC003 `one` runs on into And a second. (doc-run-on-summary)",
                "one.py:4:1: MRK001 `# pyqa: disable=doc-run-on-summary` exempts nothing (marker-unused)",
            ),
        },
    },
    "marker_pass": {
        "a link code is stale in any run that reads Python": {
            "args": ["VALUE = 1  # pyqa: disable=link-missing-file\n", "--debugging"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=link-missing-file` exempts nothing (marker-unused)",),
        },
        "a stale-marker code is stale in any run that reads Python": {
            "args": ["VALUE = 1  # pyqa: disable=MRK001\n", "--debugging"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=MRK001` exempts nothing (marker-unused)",),
        },
        "a marker for a group that did not run is not judged": {
            "args": ['"""Cases."""  # pyqa: disable=DSC003\n', "--debugging"],
            "returns": (),
        },
        "an unused docstring marker is stale": {
            "args": ['"""Cases."""  # pyqa: disable=doc-run-on-summary\n', "--docs"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=doc-run-on-summary` exempts nothing (marker-unused)",),
        },
        "a code no rule carries is stale": {
            "args": ["VALUE = 1  # pyqa: disable=DSC999\n", "--docs"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=DSC999` exempts nothing (marker-unused)",),
        },
        "a name no code carries is stale even when no group reading its family ran": {
            "args": ["VALUE = 1  # pyqa: disable=doc-run-on\n", "--order"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=doc-run-on` exempts nothing (marker-unused)",),
        },
        "a repeat is stale even when no group reading its family ran": {
            "args": ["VALUE = 1  # pyqa: disable=DSC003, doc-run-on-summary\n", "--debugging"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=doc-run-on-summary` exempts nothing (marker-unused)",),
        },
        "a debugging marker is not judged when only docs run": {
            "args": ["VALUE = 1  # pyqa: disable=DBG002\n", "--docs"],
            "returns": (),
        },
        "a debugging marker is not judged when only order runs": {
            "args": ["VALUE = 1  # pyqa: disable=debug-breakpoint\n", "--order"],
            "returns": (),
        },
        "an order marker on a statement already in place is stale": {
            "args": ["def alpha(): ...  # pyqa: disable=order-alpha\n\n\ndef beta(): ...\n", "--order"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=order-alpha` exempts nothing (marker-unused)",),
        },
        "an order marker holding a statement in place is in use": {
            "args": [
                "def zebra(): ...\n\n\ndef beta(): ...\n\n\ndef alpha(): ...  # pyqa: disable=ORD001\n",
                "--order",
            ],
            "returns": ("one.py:4:1: ORD001 `beta` belongs first (order-alpha)",),
        },
        "of two freezes that each make the other redundant, the first is stale and the second in use": {
            "args": [
                "def b(): ...  # pyqa: disable=ORD001\n\n\ndef a(): ...  # pyqa: disable=order-alpha\n",
                "--order",
            ],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=ORD001` exempts nothing (marker-unused)",),
        },
        "a freeze at the head of a body is in use though the report reads the same without it": {
            "args": ["def b(): ...  # pyqa: disable=order-alpha\n\n\ndef c(): ...\n\n\ndef a(): ...\n", "--order"],
            "returns": ("one.py:7:1: ORD001 `a` belongs first (order-alpha)",),
        },
        "a freeze on an assignment carrying an attribute docstring is judged with the docstring it carries": {
            "args": ['X = 1  # pyqa: disable=ORD002\n"""Doc."""\n\n\ndef a(): ...\n\n\ndef b(): ...\n', "--order"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=ORD002` exempts nothing (marker-unused)",),
        },
        "a debugging code beside an order code is left to its own group": {
            "args": ["def zebra(): ...\n\n\ndef alpha(): ...  # pyqa: disable=order-alpha, DBG002\n", "--order"],
            "returns": (),
        },
        "an assignment freeze holding a class attribute below a method is in use": {
            "args": [
                "class One:\n    def b(self): ...\n\n    LIMIT = 3  # pyqa: disable=ORD002\n\n    def a(self): ...\n",
                "--order",
            ],
            "returns": (),
        },
        # The freeze keeps the comment between `DEBUG` and `ROOT` out of any run. Without it the run spans the comment,
        # which belongs to no statement, so `--fix` refuses the module and leaves `a` where the report says it is wrong.
        "a freeze `--fix` needs to write the module at all is in use": {
            "args": [
                '"""Doc."""\n\nimport os\n\nDEBUG = False\n\n# Paths\n\nROOT = os.getcwd()  # pyqa: disable=order-assignment\nHOME = ROOT\n\n\ndef b():\n    """B."""\n\n\ndef a():\n    """A."""\n',
                "--order",
            ],
            "returns": ("one.py:17:1: ORD001 `a` belongs after `HOME` (order-alpha)",),
        },
        "an order marker naming the other kind is stale": {
            "args": ["def zebra(): ...\n\n\ndef alpha(): ...  # pyqa: disable=order-assignment\n", "--order"],
            "returns": (
                "one.py:4:1: ORD001 `alpha` belongs first (order-alpha)",
                "one.py:4:1: MRK001 `# pyqa: disable=order-assignment` exempts nothing (marker-unused)",
            ),
        },
    },
    "file_markers": {
        "a file marker exempts every print in its module, wherever it sits": {
            "args": [
                "print(1)\n\n\ndef one() -> None:\n    print(2)\n\n\n# pyqa: disable-file=debug-print\n",
                "--debugging",
            ],
            "returns": (),
        },
        "a file marker naming the print code leaves a breakpoint reported": {
            "args": ["# pyqa: disable-file=DBG001\nprint(1)\nbreakpoint()\n", "--debugging"],
            "returns": ("one.py:3:1: DBG002 `breakpoint()` called (debug-breakpoint)",),
        },
        "a file marker exempts a run-on summary with no marker on its docstring": {
            "args": ['"""Cases. And a second.\n\nMore.\n"""\n# pyqa: disable-file=doc-run-on-summary\n', "--docs"],
            "returns": (),
        },
        "a file marker exempting nothing is stale": {
            "args": ["# pyqa: disable-file=debug-print\nVALUE = 1\n", "--debugging"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable-file=debug-print` exempts nothing (marker-unused)",),
        },
        "a line marker under a file marker naming its code is stale": {
            "args": ["# pyqa: disable-file=debug-print\nprint(1)  # pyqa: disable=DBG001\n", "--debugging"],
            "returns": ("one.py:2:1: MRK001 `# pyqa: disable=DBG001` exempts nothing (marker-unused)",),
        },
        "a line marker under a file marker naming its code is stale even when its group did not run": {
            "args": ["# pyqa: disable-file=debug-print\nprint(1)  # pyqa: disable=DBG001\n", "--docs"],
            "returns": ("one.py:2:1: MRK001 `# pyqa: disable=DBG001` exempts nothing (marker-unused)",),
        },
        "a line marker is set aside under a file marker below it as much as one above": {
            "args": ["print(1)  # pyqa: disable=DBG001\n# pyqa: disable-file=debug-print\n", "--debugging"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable=DBG001` exempts nothing (marker-unused)",),
        },
        "a code two file markers name is stale the second time": {
            "args": ["# pyqa: disable-file=DBG001\n# pyqa: disable-file=debug-print\nprint(1)\n", "--debugging"],
            "returns": ("one.py:2:1: MRK001 `# pyqa: disable-file=debug-print` exempts nothing (marker-unused)",),
        },
        "a file marker naming the definition code holds every definition in place": {
            "args": ["# pyqa: disable-file=order-alpha\ndef zebra(): ...\n\n\ndef alpha(): ...\n", "--order"],
            "returns": (),
        },
        "a file freeze nothing needs is stale": {
            "args": ["# pyqa: disable-file=order-alpha\ndef alpha(): ...\n\n\ndef beta(): ...\n", "--order"],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable-file=order-alpha` exempts nothing (marker-unused)",),
        },
        "an assignment freeze beside a freeze of every definition changes nothing": {
            "args": [
                "# pyqa: disable-file=order-alpha, order-assignment\ndef zebra(): ...\n\n\ndef alpha(): ...\n\n\nLIMIT = 3\n",
                "--order",
            ],
            "returns": ("one.py:1:1: MRK001 `# pyqa: disable-file=order-assignment` exempts nothing (marker-unused)",),
        },
        "a forward reference among the assignments keeps the assignment freeze in use beside the definition one": {
            "args": [
                "# pyqa: disable-file=order-alpha, order-assignment\ndef zebra(): ...\n\n\ndef alpha(): ...\n\n\nFIRST: Kind = 1\nKind = int\n",
                "--order",
            ],
            "returns": (),
        },
        "an assignment freeze alone leaves the definitions to sort": {
            "args": [
                "# pyqa: disable-file=order-assignment\ndef zebra(): ...\n\n\ndef alpha(): ...\n\n\nLIMIT = 3\n",
                "--order",
            ],
            "returns": ("one.py:5:1: ORD001 `alpha` belongs first (order-alpha)",),
        },
        "a line freeze under a file freeze of its code is stale": {
            "args": [
                "# pyqa: disable-file=order-alpha\ndef b(): ...  # pyqa: disable=ORD001\n\n\ndef a(): ...\n",
                "--order",
            ],
            "returns": ("one.py:2:1: MRK001 `# pyqa: disable=ORD001` exempts nothing (marker-unused)",),
        },
        "a module carrying the old skip-file comment is reported": {
            "args": ["# order: skip-file\ndef zebra(): ...\n\n\ndef alpha(): ...\n", "--order"],
            "returns": ("one.py:5:1: ORD001 `alpha` belongs first (order-alpha)",),
        },
        "a file freeze leaves a rebinding below the statement reading the name": {
            "args": [
                "# pyqa: disable-file=order-alpha\nDEBUG = False\n\n\ndef configure(): ...\n\n\nVERBOSE = DEBUG\nDEBUG = True\n",
                "--order",
            ],
            "returns": (),
        },
    },
    "defined": {
        "an assignment binds each of its targets": {"args": ["FIRST = SECOND = 1\n"], "returns": {"FIRST", "SECOND"}},
        "a definition binds its name and none its body binds": {
            "args": ["def one():\n    two = 1\n"],
            "returns": {"one"},
        },
        "an import binds its first name or its alias": {
            "args": ["import os.path, json as data\n"],
            "returns": {"data", "os"},
        },
        "a loop binds its target": {"args": ["for item in range(3):\n    pass\n"], "returns": {"item"}},
        "a walrus binds in the body around it": {"args": ["VALUE = (size := 3)\n"], "returns": {"VALUE", "size"}},
        "a comprehension's variable stays in the comprehension": {
            "args": ["VALUE = [item for item in range(3)]\n"],
            "returns": {"VALUE"},
        },
    },
    "order": {
        "a comment above a definition travels with it": {
            "args": ["def zebra(): ...\n\n\n# About alpha.\ndef alpha(): ...\n"],
            "returns": "# About alpha.\ndef alpha(): ...\n\n\ndef zebra(): ...\n",
        },
        "a comment run under a definition that a blank line closes travels with it": {
            "args": ["def zebra(): ...\n# About zebra.\n\n\ndef alpha(): ...\n"],
            "returns": "def alpha(): ...\n\n\ndef zebra(): ...\n# About zebra.\n",
        },
        "a comment run the end of the file closes travels with the definition above it": {
            "args": ["def zebra(): ...\n\n\ndef alpha(): ...\n# About alpha.\n"],
            "returns": "def alpha(): ...\n# About alpha.\n\n\ndef zebra(): ...\n",
        },
        "a comment run nothing closes opens the definition below it": {
            "args": ["def zebra(): ...\n\n\ndef beta(): ...\n# About alpha.\ndef alpha(): ...\n"],
            "returns": "# About alpha.\ndef alpha(): ...\n\n\ndef beta(): ...\n\n\ndef zebra(): ...\n",
        },
        "an attribute docstring travels with the assignment it documents": {
            "args": ['def zebra(): ...\n\n\nLIMIT = 3\n"""The limit."""\n'],
            "returns": 'LIMIT = 3\n"""The limit."""\n\n\ndef zebra(): ...\n',
        },
        "an assignment below a definition rises to the assignments above it": {
            "args": ["FIRST = 1\n\n\ndef zebra(): ...\n\n\nSECOND = 2\n"],
            "returns": "FIRST = 1\n\nSECOND = 2\n\n\ndef zebra(): ...\n",
        },
        "neighboring assignments keep the gap between them as they move": {
            "args": ["def zebra(): ...\n\n\nFIRST = 1\nSECOND = 2\n"],
            "returns": "FIRST = 1\nSECOND = 2\n\n\ndef zebra(): ...\n",
        },
        "a class body ranks attributes, nested classes, then `__init__`, dunders and methods": {
            "args": [
                "class One:\n    def method(self): ...\n\n    def __repr__(self): ...\n\n    def __init__(self): ...\n\n    class Nested: ...\n\n    LIMIT = 3\n",
            ],
            "returns": "class One:\n    LIMIT = 3\n\n    class Nested: ...\n\n    def __init__(self): ...\n\n    def __repr__(self): ...\n\n    def method(self): ...\n",
        },
        "a class attribute rising to the attributes above it sits against them": {
            "args": ["class One:\n    FIRST = 1\n\n    def method(self): ...\n\n    SECOND = 2\n"],
            "returns": "class One:\n    FIRST = 1\n    SECOND = 2\n\n    def method(self): ...\n",
        },
        # A dataclass builds its constructor from its fields in the order written, so its fields rise without sorting.
        "a dataclass field rises above a method and stays after the fields written before it": {
            "args": [
                "import dataclasses\n\n\n@dataclasses.dataclass\nclass Config:\n    zeta: int\n\n    def describe(self) -> str: ...\n\n    alpha: int = 0\n"
            ],
            "returns": "import dataclasses\n\n\n@dataclasses.dataclass\nclass Config:\n    zeta: int\n    alpha: int = 0\n\n    def describe(self) -> str: ...\n",
        },
        "a dataclass keeps its fields in the order written while its nested classes sort": {
            "args": [
                "import dataclasses\n\n\n@dataclasses.dataclass\nclass Config:\n    @dataclasses.dataclass\n    class Database:\n        url: str = 'x'\n\n    @dataclasses.dataclass\n    class Cache:\n        ttl: int = 1\n\n    database: Database\n    cache: Cache\n"
            ],
            "returns": "import dataclasses\n\n\n@dataclasses.dataclass\nclass Config:\n    @dataclasses.dataclass\n    class Cache:\n        ttl: int = 1\n\n    @dataclasses.dataclass\n    class Database:\n        url: str = 'x'\n\n    database: Database\n    cache: Cache\n",
        },
        "class attributes keep their order among themselves while the classes they read sort": {
            "args": [
                "class Registry:\n    ORDER = []\n\n    class Zeta: ...\n\n    class Alpha: ...\n\n    ZETA = ORDER.append(Zeta)\n    ALPHA = ORDER.append(Alpha)\n"
            ],
            "returns": "class Registry:\n    ORDER = []\n\n    class Alpha: ...\n\n    class Zeta: ...\n\n    ZETA = ORDER.append(Zeta)\n    ALPHA = ORDER.append(Alpha)\n",
        },
        "module assignments keep their order among themselves while the classes they read sort": {
            "args": [
                "ORDER = []\n\n\nclass Zeta: ...\n\n\nclass Alpha: ...\n\n\nZETA = ORDER.append(Zeta)\nALPHA = ORDER.append(Alpha)\n"
            ],
            "returns": "ORDER = []\n\n\nclass Alpha: ...\n\n\nclass Zeta: ...\n\n\nZETA = ORDER.append(Zeta)\nALPHA = ORDER.append(Alpha)\n",
        },
        "a subclass stays below the base it reads, whatever their names": {
            "args": ["class Zulu: ...\n\n\nclass Alpha(Zulu): ...\n\n\nclass Beta: ...\n"],
            "returns": "class Beta: ...\n\n\nclass Zulu: ...\n\n\nclass Alpha(Zulu): ...\n",
        },
        "a subclass waits below every base it reads, not only the first placed": {
            "args": ["class Alpha(Yankee, Zulu): ...\n\n\nclass Zulu: ...\n\n\nclass Yankee: ...\n"],
            "returns": "class Yankee: ...\n\n\nclass Zulu: ...\n\n\nclass Alpha(Yankee, Zulu): ...\n",
        },
        # `from __future__ import annotations` stores a module annotation as a string that nothing evaluates.
        "a module annotation is not read while annotations are deferred": {
            "args": [
                "from __future__ import annotations\n\nREGISTRY: dict[str, Widget] = {}\nOTHER = 1\n\n\nclass Widget: ...\n"
            ],
            "returns": "from __future__ import annotations\n\nREGISTRY: dict[str, Widget] = {}\nOTHER = 1\n\n\nclass Widget: ...\n",
        },
        "a module annotation is read while annotations are evaluated": {
            "args": ["REGISTRY: dict[str, Widget] = {}\nOTHER = 1\n\n\nclass Widget: ...\n"],
            "returns": "class Widget: ...\n\n\nREGISTRY: dict[str, Widget] = {}\nOTHER = 1\n",
        },
        "the value of an annotated assignment is read while annotations are deferred": {
            "args": ["from __future__ import annotations\n\nLIMIT: int = zebra()\n\n\ndef zebra(): ...\n"],
            "returns": "from __future__ import annotations\n\n\ndef zebra(): ...\n\n\nLIMIT: int = zebra()\n",
        },
        "a class body annotation is read while annotations are deferred": {
            "args": ["from __future__ import annotations\n\n\nclass Model:\n    item: Widget\n\n\nclass Widget: ...\n"],
            "returns": "from __future__ import annotations\n\n\nclass Widget: ...\n\n\nclass Model:\n    item: Widget\n",
        },
        # A run opens below the statement that stays, so the binding the read sees sits above the run, not in it.
        "a read stays above a later rebinding of the name it reads": {
            "args": ["DEBUG = False\nprint(DEBUG)\nVERBOSE = DEBUG\nDEBUG = True\n"],
            "returns": "DEBUG = False\nprint(DEBUG)\nVERBOSE = DEBUG\nDEBUG = True\n",
        },
        "an imported name is bound above the run that rebinds it": {
            "args": ["import os.path\n\nprint(os)\nPATH = os.sep\nos = None\n"],
            "returns": "import os.path\n\nprint(os)\nPATH = os.sep\nos = None\n",
        },
        "a read of a builtin stays above a later rebinding of it": {
            "args": ["def zebra(): ...\n\n\nFORMAT = format(1, 'x')\nformat = str\n"],
            "returns": "FORMAT = format(1, 'x')\nformat = str\n\n\ndef zebra(): ...\n",
        },
        # A class is built when its statement runs, so its methods' decorators and defaults are read then, too.
        "a class stays below a decorator its method reads": {
            "args": ["def _deco(func):\n    return func\n\n\nclass One:\n    @_deco\n    def method(self): ...\n"],
            "returns": "def _deco(func):\n    return func\n\n\nclass One:\n    @_deco\n    def method(self): ...\n",
        },
        "a class stays above a later rebinding of a default its method reads": {
            "args": ["LIMIT = 3\n\n\nclass One:\n    def method(self, limit=LIMIT): ...\n\n\nLIMIT = 4\n"],
            "returns": "LIMIT = 3\n\n\nclass One:\n    def method(self, limit=LIMIT): ...\n\n\nLIMIT = 4\n",
        },
        "an augmented assignment reads the name it rebinds": {
            "args": ["COUNT = 1\n\n\nclass One:\n    COUNT += 1\n\n\nCOUNT = 5\n"],
            "returns": "COUNT = 1\n\n\nclass One:\n    COUNT += 1\n\n\nCOUNT = 5\n",
        },
        "a rebinding stays below the binding it replaces": {
            "args": ["def legacy(): ...\n\n\nlegacy = str\n"],
            "returns": "def legacy(): ...\n\n\nlegacy = str\n",
        },
        "a read of two names one statement binds waits on that statement once": {
            "args": ["def zebra(): ...\n\n\nTOTAL = FIRST + SECOND\nFIRST = SECOND = 1\n"],
            "returns": "FIRST = SECOND = 1\n\nTOTAL = FIRST + SECOND\n\n\ndef zebra(): ...\n",
        },
        "a class attribute reading a module name stays above the class's own rebinding of it": {
            "args": ["LIMIT = 1\n\n\nclass One:\n    VALUE = LIMIT\n\n    def method(self): ...\n\n    LIMIT = 3\n"],
            "returns": "LIMIT = 1\n\n\nclass One:\n    VALUE = LIMIT\n    LIMIT = 3\n\n    def method(self): ...\n",
        },
        "a definition rising to the import block takes two blank lines under it": {
            "args": ["import os\n\nLIMIT = zebra()\n\n\ndef zebra(): ...\n"],
            "returns": "import os\n\n\ndef zebra(): ...\n\n\nLIMIT = zebra()\n",
        },
        "an assignment rising to the import block takes one blank line under it": {
            "args": ["import os\n\n\ndef zebra(): ...\n\n\nLIMIT = os.sep\n"],
            "returns": "import os\n\nLIMIT = os.sep\n\n\ndef zebra(): ...\n",
        },
        "`main` stays where it is": {
            "args": ["def zebra(): ...\n\n\ndef main(): ...\n\n\ndef alpha(): ...\n"],
            "returns": "def zebra(): ...\n\n\ndef main(): ...\n\n\ndef alpha(): ...\n",
        },
        "a marker on a definition's first line freezes it": {
            "args": ["def zebra(): ...\n\n\ndef beta(): ...\n\n\ndef alpha(): ...  # pyqa: disable=order-alpha\n"],
            "returns": "def beta(): ...\n\n\ndef zebra(): ...\n\n\ndef alpha(): ...  # pyqa: disable=order-alpha\n",
        },
        "a marker on an assignment's first line freezes it": {
            "args": ["def zebra(): ...\n\n\nLIMIT = 3  # pyqa: disable=ORD002\n"],
            "returns": "def zebra(): ...\n\n\nLIMIT = 3  # pyqa: disable=ORD002\n",
        },
        "a marker naming the other kind freezes nothing": {
            "args": ["def zebra(): ...\n\n\ndef alpha(): ...  # pyqa: disable=order-assignment\n"],
            "returns": "def alpha(): ...  # pyqa: disable=order-assignment\n\n\ndef zebra(): ...\n",
        },
        # A pass moves lines, so a marker is read again from the text each pass sorts rather than once from the start.
        "a marker in a class body that moves is read again on the pass that sorts the body": {
            "args": [
                "class Zulu:\n    def zebra(self): ...  # pyqa: disable=ORD001\n\n    def alpha(self): ...\n\n\nclass Alpha: ...\n"
            ],
            "returns": "class Alpha: ...\n\n\nclass Zulu:\n    def zebra(self): ...  # pyqa: disable=ORD001\n\n    def alpha(self): ...\n",
        },
        "a file marker freezes every definition in every body": {
            "args": [
                "# pyqa: disable-file=order-alpha\nclass Zulu:\n    def zebra(self): ...\n\n    def alpha(self): ...\n\n\nclass Alpha: ...\n"
            ],
            "returns": "# pyqa: disable-file=order-alpha\nclass Zulu:\n    def zebra(self): ...\n\n    def alpha(self): ...\n\n\nclass Alpha: ...\n",
        },
        "a file marker freezing every assignment leaves the definitions between them to sort": {
            "args": [
                "FIRST = 1\n\n\ndef zebra(): ...\n\n\ndef alpha(): ...\n\n\nSECOND = 2\n# pyqa: disable-file=ORD002\n"
            ],
            "returns": "FIRST = 1\n\n\ndef alpha(): ...\n\n\ndef zebra(): ...\n\n\nSECOND = 2\n# pyqa: disable-file=ORD002\n",
        },
        "a statement that runs stays, and each side of it sorts on its own": {
            "args": [
                "def zebra(): ...\n\n\ndef alpha(): ...\n\n\nprint()\n\n\ndef delta(): ...\n\n\ndef charlie(): ...\n"
            ],
            "returns": "def alpha(): ...\n\n\ndef zebra(): ...\n\n\nprint()\n\n\ndef charlie(): ...\n\n\ndef delta(): ...\n",
        },
        # The statement under the run is what an edit taken from lines another edit already replaced would eat.
        "a class body inside a run that moves is sorted on a later pass": {
            "args": [
                "class Zulu:\n    def zebra(self): ...\n\n    def alpha(self): ...\n\n\nclass Alpha: ...\n\n\nprint()\n"
            ],
            "returns": "class Alpha: ...\n\n\nclass Zulu:\n    def alpha(self): ...\n\n    def zebra(self): ...\n\n\nprint()\n",
        },
        # The pass limit is what stops a rewrite that never settles, so one pass shows what a pass reaches.
        "one pass sorts the outermost run and leaves the class body inside it": {
            "args": ["class Zulu:\n    def zebra(self): ...\n\n    def alpha(self): ...\n\n\nclass Alpha: ...\n"],
            "patches": [(pyqa, "MAX_PASSES", 1)],
            "returns": "class Alpha: ...\n\n\nclass Zulu:\n    def zebra(self): ...\n\n    def alpha(self): ...\n",
        },
    },
    "faithful": {
        "every line kept, in an order that still parses, is trusted": {
            "args": ["FIRST = 1\nSECOND = 2\n", "SECOND = 2\nFIRST = 1\n"],
            "returns": True,
        },
        "a line lost on the way is refused": {
            "args": ["FIRST = 1\nSECOND = 2\n", "FIRST = 1\n"],
            "returns": False,
        },
        "every line kept, in an order that no longer parses, is refused": {
            "args": ["if FLAG:\n    pass\n", "    pass\nif FLAG:\n"],
            "returns": False,
        },
    },
}


@pytest.fixture(name="pyqa_report")
def fixture_pyqa_report(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture) -> Callable[[str, str], tuple[str, ...]]:
    """Return a callable that runs one group over one module's source and answers with every finding it reports."""

    def pyqa_report(source: str, flag: str) -> tuple[str, ...]:
        """Every finding one module's source reports under one group's flag, named relative to the temporary root."""
        (tmp_path / "one.py").write_text(source)
        pyqa.main([flag, str(tmp_path)])
        printed = capsys.readouterr().out.splitlines()
        return tuple(one.removeprefix(f"{tmp_path}/") for one in printed if ":1: " in one)

    return pyqa_report


def test_a_bad_config_fails_the_run_before_fix_writes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A config that fails the run fails it before `--fix` rewrites anything.

    Mutation: let `--fix` rewrite before `main` reads the config. The module is sorted, and only then does the run fail.
    """
    (tmp_path / "pyproject.toml").write_text('[tool.pyqa]\nignore = ["DSX"]\n')
    path = tmp_path / "one.py"
    path.write_text(GROUP_MODULE)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="names no code, name or prefix"):
        pyqa.main(["--order", "--fix", "."])
    assert path.read_text() == GROUP_MODULE


def test_a_bare_fragment_resolves_against_its_own_document(tmp_path: pathlib.Path) -> None:
    """A link with no path names a heading in the file it is written in."""
    (tmp_path / "a.md").write_text("## Here\n\n[one](#here) and [two](#there).\n", encoding="utf-8")
    findings = pyqa._unresolved([tmp_path / "a.md"], tmp_path)
    assert [one.detail for one in findings] == ["no heading `#there` in `a.md`"]


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["debugging"])
def test_a_debugging_call_is_reported_unless_its_own_line_exempts_it(
    test_case: dict,
    pyqa_report: Callable,
    function_tester: Callable,
) -> None:
    """A `print` reports as DBG001 and a `breakpoint` or `pdb.set_trace` as DBG002, kept by a marker naming its code.

    Mutation: match the callee by its last name rather than its whole spelling in `_debug_findings`. `self.print()`,
    and every other method sharing one of these names, reports as a debugging call.
    """
    function_tester(test_case, functools.partial(pyqa_report, flag="--debugging"))


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["stale"])
def test_a_debugging_exemption_that_exempts_nothing_is_reported(
    test_case: dict,
    pyqa_report: Callable,
    function_tester: Callable,
) -> None:
    """A marker entry naming a `DBG` code that nothing on its line reports is stale, and reports as MRK001.

    Mutation: report only the families no comment can exempt in `_is_stale`, dropping the judged check. A
    `disable=DBG002` left behind after its call is deleted reads as load-bearing.
    """
    function_tester(test_case, functools.partial(pyqa_report, flag="--debugging"))


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


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["docs_markers"])
def test_a_docstring_finding_is_exempted_where_a_comment_can_sit(
    test_case: dict,
    pyqa_report: Callable,
    function_tester: Callable,
) -> None:
    """A docstring finding is exempted from its definition's line, or for a run-on summary from its docstring's last.

    Mutation: anchor `DSC003` at the docstring's first line in `_overruns`. A multi-line docstring's run-on summary can
    never be exempted, since no comment fits on that line.
    """
    function_tester(test_case, functools.partial(pyqa_report, flag="--docs"))


def test_a_docstring_in_a_test_module_is_still_read(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture) -> None:
    """DSC002 exempts a test module because its inputs arrive as fixtures; a run-on summary has no such reason.

    Mutation: move the DSC003 pass below `_docs_findings`' `_is_test` skip. Every test module stops being read.
    """
    (tmp_path / "test_one.py").write_text('"""Cases."""\n\n\ndef test_one() -> None:\n    """A case. And more."""\n')
    status = pyqa.main(["--docs", str(tmp_path)])
    reported = [line for line in capsys.readouterr().out.splitlines() if ":1: DSC003" in line]
    assert status == 1
    assert reported == [
        f"{tmp_path / 'test_one.py'}:5:1: DSC003 `test_one` runs on into And more. (doc-run-on-summary)"
    ]


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


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["file_markers"])
def test_a_file_marker_covers_its_whole_module(
    test_case: dict,
    pyqa_report: Callable,
    function_tester: Callable,
) -> None:
    """A file entry exempts or freezes every finding of its code in the module, and a line entry under it is stale.

    Mutation: seed each line marker's scope empty rather than with the file entries in `_markers`. A line entry under a
    file entry of its code reads as in use, so a marker the module no longer needs is never cut.
    """
    function_tester(test_case, pyqa_report)


def test_a_file_named_as_a_root_is_read_alone(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture) -> None:
    """A root naming one file reads that file, and none of its siblings.

    Mutation: drop the `is_file` branch from `_files`. A file globs to nothing, so a root naming one reads as a clean
    pass over zero files.
    """
    (tmp_path / "one.py").write_text('"""One sentence. And a second."""\n')
    (tmp_path / "two.py").write_text('"""One sentence. And a second."""\n')
    status = pyqa.main(["--docs", str(tmp_path / "one.py")])
    reported = [line for line in capsys.readouterr().out.splitlines() if ":1: DSC003" in line]
    assert status == 1
    assert reported == [f"{tmp_path / 'one.py'}:1:1: DSC003 `<module>` runs on into And a second. (doc-run-on-summary)"]


def test_a_file_named_as_a_root_is_read_only_by_the_groups_of_its_kind(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """A root naming one file is read by the groups reading its suffix, and passed over by the rest.

    Mutation: drop the suffix test from the `is_file` branch of `_files`. A module named as a root is also read as a
    document, so a link written in its source is reported as one that does not resolve.
    """
    (tmp_path / "one.py").write_text('"""One sentence."""\n\nLINK = "[gone](gone.md)"\n')
    status = pyqa.main(["--links", str(tmp_path / "one.py")])
    output = capsys.readouterr().out
    assert status == 0
    assert output.startswith(f"scanned 0 documents under {tmp_path / 'one.py'}:")


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["glob"])
def test_a_glob_matches_segment_by_segment(test_case: dict, function_tester: Callable) -> None:
    """A pattern's segments match a path's one by one, `*` within a segment and `**` across any number of them.

    Mutation: let `**` stand for one segment or more in `_glob`. A double star between two names no longer matches the
    path that holds the two side by side.
    """
    function_tester(test_case, pyqa._glob)


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["groups"])
def test_a_group_flag_runs_that_check_and_no_other(
    test_case: dict,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    function_tester: Callable,
) -> None:
    """Each flag selects one group over a tree holding one violation of every group, and no flag selects them all.

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
    findings = pyqa._unresolved([tmp_path / "a.md"], tmp_path)
    assert [one.code for one in findings] == ["LNK001"]


def test_a_link_to_a_file_that_is_not_there_is_reported(tmp_path: pathlib.Path) -> None:
    """A target this repository does not hold fails the check, as LNK001."""
    (tmp_path / "a.md").write_text("See [it](gone.md).\n", encoding="utf-8")
    findings = pyqa._unresolved([tmp_path / "a.md"], tmp_path)
    assert [one.code for one in findings] == ["LNK001"]


def test_a_link_to_a_file_that_is_there_passes(tmp_path: pathlib.Path) -> None:
    """A target this repository holds passes, and a fragment into one that is not a document is not read as a heading.

    Mutation: drop the suffix test from `_unresolved`. A line anchor into a source file is read as a heading that no
    source file holds, and reports as LNK002.
    """
    (tmp_path / "a.md").write_text("See [b](b.md) and [the code](one.py#L3).\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("## Here\n", encoding="utf-8")
    (tmp_path / "one.py").write_text("ONE = 1\n")
    assert pyqa._unresolved([tmp_path / "a.md"], tmp_path) == []


def test_a_link_to_a_heading_the_target_lacks_is_reported(tmp_path: pathlib.Path) -> None:
    """A fragment no heading produces fails the check, as LNK002."""
    (tmp_path / "a.md").write_text("See [it](b.md#nope).\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("## Here\n", encoding="utf-8")
    findings = pyqa._unresolved([tmp_path / "a.md", tmp_path / "b.md"], tmp_path)
    assert [str(one) for one in findings] == ["a.md:1:1: LNK002 no heading `#nope` in `b.md` (link-missing-heading)"]


def test_a_links_run_finds_no_module(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run of the links group alone never walks the tree for modules, which only the Python groups read.

    Mutation: find the modules whatever groups run in `main`. A `--links` run walks every module beneath its roots.
    """
    suffixes: list[str] = []
    files = pyqa._files

    def recorded(roots: list[str], suffix: str, config: pyqa.Config) -> list[pathlib.Path]:
        """Note which suffix a run looks for, then find the files as the tool does."""
        suffixes.append(suffix)
        return files(roots, suffix, config)

    monkeypatch.setattr(pyqa, "_files", recorded)
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    pyqa.main(["--links", str(tmp_path)])
    assert suffixes == [".md"]


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["malformed"])
def test_a_malformed_config_fails_the_run_naming_its_file(
    test_case: dict,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    function_tester: Callable,
) -> None:
    """A `pyproject.toml` pyqa cannot read as a table fails the run with a message naming the file.

    Mutation: let `_table` read `tool.pyqa` without checking it is a table. A list or a number reaches the key check
    and fails on a traceback rather than a message.
    """

    def read(text: str) -> int:
        """Run every group under one `pyproject.toml` holding the given text."""
        (tmp_path / "pyproject.toml").write_text(text)
        monkeypatch.chdir(tmp_path)
        return pyqa.main(["."])

    function_tester(test_case, read)


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["marker_pass"])
def test_a_marker_is_judged_for_the_groups_that_ran(
    test_case: dict,
    pyqa_report: Callable,
    function_tester: Callable,
) -> None:
    """A marker is held to the groups the run read, and one naming no rule, or one no comment can use, in any run.

    Mutation: judge every pyqa family in `_is_stale`, whatever ran. `make docstrings` fails on a `disable=DBG002` it
    had no way to weigh.
    """
    function_tester(test_case, pyqa_report)


def test_a_missing_root_fails_the_run_naming_it(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A root that is not there stops the run on one line naming it, rather than on a traceback.

    Mutation: pass over a root that does not exist in `_files`. A typo in a recipe's source root reads as a clean
    pass over zero files.
    """
    gone = tmp_path / "gone"
    monkeypatch.setattr(sys, "argv", ["pyqa.py", str(gone)])
    with pytest.raises(SystemExit) as refused:
        runpy.run_path(pyqa.__file__, run_name="__main__")
    assert refused.value.code == f"pyqa: no such root to read: {gone}"


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
    assert reported == [f"{tmp_path / 'one.py'}:4:1: DSC001 `_one` declares Returns (doc-extra-section)"]


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
    assert reported == [f"{tmp_path / 'one.py'}:4:1: DSC004 `one` misspells Returns (doc-misspelled-section)"]


def test_a_module_freezing_every_definition_is_neither_reported_nor_sorted(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """A file marker freezing every definition holds a module of definitions in place, under report and fix alike.

    Mutation: match a line entry alone in `_exempting`. The file marker freezes nothing, the report asks for a sort,
    and `--fix` makes it.
    """
    path = tmp_path / "one.py"
    source = f"# pyqa: disable-file=order-alpha\n{GROUP_MODULE}"
    path.write_text(source)
    assert pyqa.main(["--order", str(tmp_path)]) == 0
    assert pyqa.main(["--order", "--fix", str(tmp_path)]) == 0
    assert path.read_text() == source
    assert ":1: " not in capsys.readouterr().out


def test_a_one_sentence_summary_passes(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture) -> None:
    """A summary holding one sentence is what the gate is asking for.

    Mutation: treat a semicolon as a sentence end. This line reports, and every joined summary in the tree does.
    """
    (tmp_path / "one.py").write_text('"""Report that the service is up; deliberately nothing else."""\n')
    status = pyqa.main(["--docs", str(tmp_path)])
    assert status == 0
    assert ":1: DSC003" not in capsys.readouterr().out


def test_a_path_pattern_never_covers_a_file_outside_the_config_folder() -> None:
    """A pattern with a `/` reads paths from the config's folder, so nothing beneath a root outside it matches one.

    Mutation: read a root outside the config's folder as though it sat at the folder in `_covers`. A file outside the
    folder matches a path written from it.
    """
    assert not pyqa._covers("src/shared", ("src", "shared"), None)


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["tools"])
def test_a_private_tool_docstring_keeps_the_sections_a_model_reads(
    test_case: dict,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    function_tester: Callable,
) -> None:
    """A model reads a tool's docstring as its description, so a private tool keeps the sections DSC001 would cut.

    Mutation: drop the `_is_tool` test from `_is_covered`. Every private tool is told to cut the description of its
    arguments that the model calling it reads.
    """

    def reported(decorator: str) -> tuple[str, ...]:
        """Every code a private function stating its whole contract reports under one decorator."""
        (tmp_path / "one.py").write_text(TOOL_MODULE.format(decorator=decorator))
        pyqa.main(["--docs", str(tmp_path)])
        printed = capsys.readouterr().out.splitlines()
        return tuple(sorted({one.split()[1] for one in printed if ":1: " in one}))

    function_tester(test_case, reported)


def test_a_raise_inside_a_nested_function_asks_nothing_of_the_outer_docstring(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """A `raise` belongs to the definition it is written in, so a closure's asks nothing of the definition holding it.

    Mutation: walk the whole definition with `ast.walk` in `_omitted_sections`, rather than `_owned`. The outer
    docstring is asked to declare a `raise` that only the closure holds.
    """
    source = '"""Cases."""\n\n\ndef outer() -> None:\n    """Run the check."""\n\n    def inner() -> None:\n        raise ValueError("bad")\n\n    inner()\n'
    (tmp_path / "one.py").write_text(source)
    status = pyqa.main(["--docs", str(tmp_path)])
    assert status == 0
    assert ":1: DSC" not in capsys.readouterr().out


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["faithful"])
def test_a_rewrite_is_trusted_only_holding_every_line_and_still_parsing(
    test_case: dict,
    function_tester: Callable,
) -> None:
    """A sort is written back only when it moved lines rather than losing or breaking any.

    Mutation: drop the parse from `_is_faithful`. A sort that keeps every line but lifts one out of its block is
    written to disk as a module that no longer imports.
    """
    function_tester(test_case, pyqa._is_faithful)


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["layout"])
def test_a_run_prints_its_findings_then_its_summary_then_its_rules(
    test_case: dict,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    function_tester: Callable,
) -> None:
    """A run prints its findings in the order the groups run, then its summary line, then the tally and each rule.

    Mutation: sort the findings by path and line before printing them in `_report`. The document's link finding rises
    above the module's docstring finding, and a reader loses the group each finding came from.
    """

    def printed(files: dict[str, str], flags: list[str], config: dict) -> tuple[int, tuple[str, ...]]:
        """The status one run leaves over a tree holding these files, under this config, and every line it prints."""
        for name, text in files.items():
            (tmp_path / name).write_text(text, encoding="utf-8")
        table = "".join(f"{key} = {json.dumps(value)}\n" for key, value in config.items())
        (tmp_path / "pyproject.toml").write_text(f"[tool.pyqa]\n{table}")
        monkeypatch.chdir(tmp_path)
        status = pyqa.main([*flags, "."])
        return status, tuple(capsys.readouterr().out.splitlines())

    function_tester(test_case, printed)


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


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["defined"])
def test_a_statement_binds_the_names_its_own_scope_holds(test_case: dict, function_tester: Callable) -> None:
    """A statement binds its targets, imports and definition names, and none a nested scope binds for itself.

    Mutation: descend into a comprehension in `_defined_names`. Its loop variable reads as bound in the body, so a
    read of that name before a later binding of it is never pulled below the binding.
    """

    def defined(source: str) -> frozenset[str]:
        """The names the first statement of one module's source binds."""
        return pyqa._defined_names(ast.parse(source).body[0])

    function_tester(test_case, defined)


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
    assert reported == [
        f"{tmp_path / 'one.py'}:1:1: DSC003 `<module>` runs on into Deliberately nothing else. (doc-run-on-summary)"
    ]


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["not_a_link"])
def test_an_example_or_a_route_is_not_followed_as_a_link(
    test_case: dict,
    tmp_path: pathlib.Path,
    function_tester: Callable,
) -> None:
    """A fence, a code span, a URL and a service route hold no file for a link to resolve to."""

    def unresolved(body: str) -> list[pyqa.Finding]:
        """Check the links in one document holding the given body."""
        path = tmp_path / "a.md"
        path.write_text(body, encoding="utf-8")
        return pyqa._unresolved([path], tmp_path)

    function_tester(test_case, unresolved)


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["exclude"])
def test_an_exclude_pattern_covers_what_it_names(
    test_case: dict,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    function_tester: Callable,
) -> None:
    """A pattern keeps a file out by its name or by its path from the config's folder, beneath the roots a run names.

    Mutation: match a pattern holding a `/` against the path from the root it was found under in `_files`. A pattern
    written from the project's folder matches nothing once the run names a root below it.
    """

    def reported(files: list[str], exclude: list[str], roots: list[str], where: str) -> tuple[str, ...]:
        """The files a debugging run reports, each holding one print, under one `exclude` and from one folder."""
        for name in files:
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text("print(1)\n")
        (tmp_path / "pyproject.toml").write_text(f"[tool.pyqa]\nexclude = {json.dumps(exclude)}\n")
        monkeypatch.chdir(tmp_path / where)
        pyqa.main(["--debugging", *roots])
        printed = capsys.readouterr().out.splitlines()
        found = {(tmp_path / where / one.split(":")[0]).resolve() for one in printed if ":1: " in one}
        return tuple(sorted(str(one.relative_to(tmp_path.resolve())) for one in found))

    function_tester(test_case, reported)


def test_an_excluded_document_is_not_followed(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`exclude` keeps a document out of the links group, as it keeps a module out of the others.

    Mutation: find the documents without the config's exclusions in `main`. The excluded document's broken link reports.
    """
    (tmp_path / "pyproject.toml").write_text('[tool.pyqa]\nexclude = ["docs/old"]\n')
    (tmp_path / "docs" / "old").mkdir(parents=True)
    (tmp_path / "docs" / "old" / "a.md").write_text(GROUP_DOCUMENT, encoding="utf-8")
    (tmp_path / "docs" / "b.md").write_text(GROUP_DOCUMENT, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    pyqa.main(["--links", "."])
    reported = [one for one in capsys.readouterr().out.splitlines() if ":1: " in one]
    assert reported == ["docs/b.md:1:1: LNK001 no such file `missing.md` (link-missing-file)"]


def test_an_interrupted_run_exits_the_way_a_shell_reports_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Ctrl+C stops the run with status 130, rather than on a traceback.

    Mutation: drop the `KeyboardInterrupt` branch from the `__main__` guard. The interrupt escapes as itself, and a
    recipe reads a traceback where the shell's own status belongs.
    """

    def interrupted(*_: object) -> None:
        """Stand in for a run that a Ctrl+C stops."""
        raise KeyboardInterrupt

    monkeypatch.setattr("argparse.ArgumentParser.parse_args", interrupted)
    with pytest.raises(SystemExit) as refused:
        runpy.run_path(pyqa.__file__, run_name="__main__")
    assert refused.value.code == 130


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["config"])
def test_an_off_code_is_never_reported_or_failed_on(
    test_case: dict,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    function_tester: Callable,
) -> None:
    """A code the config turns off, by its most specific selector, is never reported and never fails the run.

    Mutation: let `ignore` win only where it is strictly more specific in `_config`. A code selected and ignored by
    equally specific selectors stays on, and fails the run its config meant to clear.
    """

    def reported(config: dict) -> tuple[int, tuple[str, ...], tuple[str, ...]]:
        """The status one config leaves, the codes still reported under it, and the codes it turns off."""
        (tmp_path / "one.py").write_text(GROUP_MODULE)
        (tmp_path / "one.md").write_text(GROUP_DOCUMENT, encoding="utf-8")
        table = "".join(f"{key} = {json.dumps(value)}\n" for key, value in config.items())
        (tmp_path / "pyproject.toml").write_text(f"[tool.pyqa]\n{table}")
        monkeypatch.chdir(tmp_path)
        status = pyqa.main(["."])
        codes = tuple(sorted({one.split()[1] for one in capsys.readouterr().out.splitlines() if ":1: " in one}))
        return status, codes, tuple(sorted(pyqa._config().off))

    function_tester(test_case, reported)


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["order"])
def test_every_body_sorts_to_where_a_reader_looks_for_each_name(
    test_case: dict,
    monkeypatch: pytest.MonkeyPatch,
    function_tester: Callable,
) -> None:
    """Names sort within their rank, comments travel with them, and a read sits between its binding and any rebinding.

    Mutation: drop the `bound` test from `_read_edges`, so only a binding inside the run counts. A read of a name bound
    above the run is taken for a forward reference, and the rebinding below it rises over it.
    """

    def rewrite(source: str) -> str:
        """Sort one module's source the way `--fix` does, with the default pins."""
        return pyqa._rewrite(source, frozenset(pyqa.DEFAULT_PINNED))

    function_tester(test_case, rewrite, monkeypatch=monkeypatch)


def test_every_code_carries_one_name_of_its_own() -> None:
    """Every code a rule states has a name, no two codes share one, and none is spelled like a code.

    Mutation: give two codes one name in `NAMES`. An entry naming it resolves to the first code alone, so the second
    can never be named.
    """
    assert set(pyqa.NAMES) == set(pyqa.RULES)
    assert len(set(pyqa.NAMES.values())) == len(pyqa.NAMES)
    assert all(re.fullmatch(r"[a-z]+(?:-[a-z]+)+", one) for one in pyqa.NAMES.values())


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


def test_fix_leaves_a_module_already_in_order_untouched(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture) -> None:
    """`--fix` counts the modules it rewrote, and a module already in order is not one of them.

    Mutation: write every scanned module back in `_fix_order`, whatever the sort produced. The summary counts the
    module already in order as one it sorted.
    """
    (tmp_path / "one.py").write_text(GROUP_MODULE)
    (tmp_path / "two.py").write_text('"""Cases."""\n\n\ndef alpha() -> None:\n    """A summary."""\n')
    status = pyqa.main(["--order", "--fix", str(tmp_path)])
    assert status == 0
    assert capsys.readouterr().out.splitlines() == [f"scanned 2 modules under {tmp_path}: sorted 1"]


def test_fix_leaves_an_excluded_module_untouched(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--fix` neither reads nor rewrites a module `exclude` covers.

    Mutation: find the modules `--fix` sorts without the config's exclusions in `main`. The excluded module is sorted
    with the rest.
    """
    (tmp_path / "pyproject.toml").write_text('[tool.pyqa]\nexclude = ["vendor"]\n')
    (tmp_path / "vendor").mkdir()
    excluded = tmp_path / "vendor" / "one.py"
    excluded.write_text(GROUP_MODULE)
    sorted_module = tmp_path / "two.py"
    sorted_module.write_text(GROUP_MODULE)
    monkeypatch.chdir(tmp_path)
    assert pyqa.main(["--order", "--fix", "."]) == 0
    assert excluded.read_text() == GROUP_MODULE
    assert sorted_module.read_text() != GROUP_MODULE


def test_fix_runs_before_any_group_reads_the_files_it_rewrites(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """`--fix` sorts before the other groups read, so a marker the sort moves is matched at the line it moved to.

    Mutation: run `_fix_order` after the report groups in `main`. A `disable=debug-print` the sort moves reads as
    stale, and the run fails on a marker in use.
    """
    source = 'def zebra() -> None:\n    """Z."""\n    print(1)  # pyqa: disable=debug-print\n\n\ndef alpha() -> None:\n    """A."""\n'
    (tmp_path / "one.py").write_text(source)
    status = pyqa.main(["--fix", str(tmp_path)])
    assert status == 0
    assert "MRK001" not in capsys.readouterr().out


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["fix_off"])
def test_fix_sorts_nothing_while_both_order_codes_are_off(
    test_case: dict,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    function_tester: Callable,
) -> None:
    """`--fix` sorts only what the order codes the config leaves on would report, and nothing once both are off.

    Mutation: sort whatever the config turns off in `_fix_order`. A run selecting only the docstring codes rewrites the
    module's order all the same.
    """

    def fixed(config: dict) -> tuple[int, bool]:
        """The status a `--fix` run leaves under one config, and whether it rewrote the module."""
        path = tmp_path / "one.py"
        path.write_text(GROUP_MODULE)
        table = "".join(f"{key} = {json.dumps(value)}\n" for key, value in config.items())
        (tmp_path / "pyproject.toml").write_text(f"[tool.pyqa]\n{table}")
        monkeypatch.chdir(tmp_path)
        status = pyqa.main(["--order", "--fix", "."])
        return status, path.read_text() != GROUP_MODULE

    function_tester(test_case, fixed)


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
    assert capsys.readouterr().out.splitlines() == [f"scanned 1 module under {tmp_path}: sorted 1"]
    assert sorted_source.index("def alpha") < sorted_source.index("def zebra")
    assert pyqa.main(["--order", str(tmp_path)]) == 0


@pytest.mark.parametrize_test_case("test_case", TEST_CASES["summary"])
def test_one_line_sums_up_what_a_run_read_and_found(
    test_case: dict,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    function_tester: Callable,
) -> None:
    """One line counts the files a run read, the codes it checked and what it found, exempted and suppressed beside.

    Mutation: count the codes the config turns off under `checked` in `_report`. A run selecting one code reads as
    checking every code its groups report.
    """

    def summary(files: dict[str, str], flags: list[str], config: dict) -> str:
        """The line one run sums up in, over a tree holding these files, under this config."""
        for name, text in files.items():
            (tmp_path / name).write_text(text, encoding="utf-8")
        table = "".join(f"{key} = {json.dumps(value)}\n" for key, value in config.items())
        (tmp_path / "pyproject.toml").write_text(f"[tool.pyqa]\n{table}")
        monkeypatch.chdir(tmp_path)
        pyqa.main([*flags, "."])
        return next(one for one in capsys.readouterr().out.splitlines() if one.startswith("scanned "))

    function_tester(test_case, summary)


def test_the_report_names_every_code_it_prints(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture) -> None:
    """A finding ends with its code's name, and the tally and the rule printed under the run name the code the same way.

    Mutation: print the bare code in `_report`'s tally. A reader of the tally is back to looking the code up.
    """
    (tmp_path / "one.py").write_text('"""Cases. And a second."""\n')
    status = pyqa.main(["--docs", str(tmp_path)])
    printed = capsys.readouterr().out.splitlines()
    assert status == 1
    assert f"{tmp_path / 'one.py'}:1:1: DSC003 `<module>` runs on into And a second. (doc-run-on-summary)" in printed
    assert "reported 1 DSC003 (doc-run-on-summary)" in printed
    assert f"DSC003 (doc-run-on-summary)  {pyqa.RULES['DSC003']}" in printed
