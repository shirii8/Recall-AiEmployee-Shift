"""
The tool framework: how a "button" for the agent is described.

Plain English: think of each tool as a button on the employee's desk. The AI
can only press buttons that exist. Each button says:
  - its risk: "read" (just looking), "write" (changes something, can be
    undone), "irreversible" (money leaves / a customer gets an email), or
    "ask_human" (asks the owner a question),
  - run():      what pressing it does,
  - rule():     when pressing it is ALLOWED (checked in code before running),
  - snapshot(): how to read the relevant state from the store,
  - verify():   how to compare the before/after snapshots to PROVE it worked.

Buttons come in "packs" (one pack per kind of job). This file knows nothing
about orders, payments or refunds. That's on purpose: the core is generic, and
each business skill is a pack you plug in (see agent/packs/).
"""

# rule(): Checks whether the action is permitted

import csv  # Imports Python's built-in CSV module.
import re  # regular expression lets you check whether text follows a pattern.
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable  # type annotations

# constants by convention
READ, WRITE, IRREVERSIBLE, ASK_HUMAN = "read", "write", "irreversible", "ask_human"
CHANGES_THINGS = (WRITE, IRREVERSIBLE)


@dataclass
class Workspace:
    """Everything the tools can reach: the store API, the browser, the files, and the owner."""

    store: Any
    browser: Any
    files_dir: Path
    approver: Any = None
    owner_decisions: list = field(
        default_factory=list
    )  # answers the owner gave this run

    def read_csv(self, name: str) -> list[dict]:
        path = self.files_dir / Path(name).name  # only files inside the folder
        if not path.exists():
            return []
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        for r in rows:  # CSV gives text; turn plain whole numbers like "799" into 799
            for k, v in r.items():
                if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
                    r[k] = int(v)  # dictionary update
        return rows

    def ask_owner(self, question: str, options: list[str]) -> int | None:
        """Ask the owner to pick one option. Returns its index, or None for 'none of these'."""
        # py lists count from 0
        if self.approver is None:
            return None
        return self.approver.choose(question, options)

    # otherwise- AttributeError: 'NoneType' object has no attribute 'choose'


# describes actions that the AI can perform
@dataclass
class Tool:
    name: str
    description: str
    params: dict[
        str, tuple[type, str]
    ]  # arg name -> (python type, description) dict{key:value}
    risk: str  # "READ", "WRITE", "IRREVERSIBLE", "ASK_HUMAN"
    # callable describes the function type Not calling it
    run: Callable[
        [Workspace, dict], object
    ]  # describes 2 expected inputs , its return type
    rule: Callable[[Workspace, dict], None] | None = (
        None  # raises PolicyDenied if not allowed
    )
    snapshot: Callable[[Workspace, dict], dict] | None = (
        None  # read the state this tool changes
    )
    verify: Callable[[dict, dict, dict], tuple[bool, str]] | None = (
        None  # args=the requested action's inputs,before= the snapshot before the action,after=the snapshot after the action.
    )
    retry_safe: bool = True  # may we retry it automatically?
    optional: tuple[
        str, ...
    ] = ()  # args the model may leave out, a tuple containing 0 or more strings, takes an empty tuple by default


@dataclass
class Pack:
    """One job's worth of tools, plus a few lines telling the brain how that job works."""

    name: str
    tools: list[Tool]
    guidance: str = ""
    # The guidance helps the agent decide how to use the tools. It doesn't automatically enforce rules; enforcement belongs in the appropriate code.


class Registry:
    """All the tools from all the packs the agent has been given."""

    def __init__(self, packs: list[Pack]):
        self.packs = packs
        self.tools: dict[str, Tool] = {}
        for pack in packs:
            for t in pack.tools:
                if t.name in self.tools:
                    raise ValueError(f"two packs define a tool called {t.name}")
                self.tools[t.name] = t 
                # use the tool's name as the dictionary key and store the actual Tool object as its value.

    # so that AI know what tools exist, what they do, and what inputs they need
    def describe(self) -> str:
        lines = []
        for t in self.tools.values():
            #a string descibing the input
            args = ", ".join(
                f'"{k}"{"?" if k in t.optional else ""}: {typ.__name__} ({d})'
                for k, (typ, d) in t.params.items()
            )
            lines.append(f"- {t.name} [{t.risk}]: {t.description} args: {{{args}}}") #{{}} prints a literal {} and the {args} insrt args values in the var
        lines.append(
            '- finish: call when the goal is done (or can\'t be done). args: {"summary": str}' #finish tool expects an input called summary of type str
        )
        return "\n".join(lines)

    def guidance(self) -> str:
        return "\n\n".join(
            f"[{p.name}]\n{p.guidance.strip()}"
            for p in self.packs
            if p.guidance.strip()
        )
    #example - [Orders] 
    #          Check order status before cancelling.
