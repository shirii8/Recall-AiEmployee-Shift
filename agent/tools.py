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
import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

READ, WRITE, IRREVERSIBLE, ASK_HUMAN = "read", "write", "irreversible", "ask_human"
CHANGES_THINGS = (WRITE, IRREVERSIBLE)


@dataclass
class Workspace:
    """Everything the tools can reach: the store API, the browser, the files, and the owner."""
    store: Any
    browser: Any
    files_dir: Path
    approver: Any = None
    owner_decisions: list = field(default_factory=list)   # answers the owner gave this run

    def read_csv(self, name: str) -> list[dict]:
        path = self.files_dir / Path(name).name           # only files inside the folder
        if not path.exists():
            return []
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        for r in rows:  # CSV gives text; turn plain whole numbers like "799" into 799
            for k, v in r.items():
                if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
                    r[k] = int(v)
        return rows

    def ask_owner(self, question: str, options: list[str]) -> int | None:
        """Ask the owner to pick one option. Returns its index, or None for 'none of these'."""
        if self.approver is None:
            return None
        return self.approver.choose(question, options)


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, tuple[type, str]]          # arg name -> (python type, description)
    risk: str
    run: Callable[[Workspace, dict], object]
    rule: Callable[[Workspace, dict], None] | None = None          # raises PolicyDenied if not allowed
    snapshot: Callable[[Workspace, dict], dict] | None = None      # read the state this tool changes
    verify: Callable[[dict, dict, dict], tuple[bool, str]] | None = None  # (args, before, after)
    retry_safe: bool = True                       # may we retry it automatically?
    optional: tuple[str, ...] = ()                # args the model may leave out


@dataclass
class Pack:
    """One job's worth of tools, plus a few lines telling the brain how that job works."""
    name: str
    tools: list[Tool]
    guidance: str = ""


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

    def describe(self) -> str:
        lines = []
        for t in self.tools.values():
            args = ", ".join(f'"{k}"{"?" if k in t.optional else ""}: {typ.__name__} ({d})'
                             for k, (typ, d) in t.params.items())
            lines.append(f"- {t.name} [{t.risk}]: {t.description} args: {{{args}}}")
        lines.append('- finish: call when the goal is done (or can\'t be done). args: {"summary": str}')
        return "\n".join(lines)

    def guidance(self) -> str:
        return "\n\n".join(f"[{p.name}]\n{p.guidance.strip()}" for p in self.packs if p.guidance.strip())
