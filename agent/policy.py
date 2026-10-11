"""
The guardrail engine: the rules the AI cannot talk its way past.

Plain English: the AI decides WHAT to do; this file decides whether it's
ALLOWED. It works like Row Level Security in Postgres: default deny.

  - A tool that changes anything must come with a rule. No rule = no
    permission (like a table with RLS on and no policy: nobody gets in).
  - The rule is plain Python, so the AI can't skip it by being clever, and it
    reads facts from trusted places (the store, the files, the owner's own
    answers), never from the AI's claims.
  - On top of that, anything irreversible also needs the owner to say yes.

The actual business rules live with their tools in agent/packs/. This file is
only the engine, so it works for any job.
"""
import threading

from agent.tools import IRREVERSIBLE, READ, Tool, Workspace


class PolicyDenied(Exception):
    pass


def validate_args(tool: Tool, args: dict) -> dict:
    """Default deny on shape: missing args, extra args or wrong types are refused."""
    if not isinstance(args, dict):
        raise PolicyDenied("args must be a JSON object")
    extra = set(args) - set(tool.params)
    if extra:
        raise PolicyDenied(f"unexpected args {sorted(extra)}")
    clean = {}
    for name, (typ, _) in tool.params.items():
        if name not in args or args[name] is None:
            if name in tool.optional:
                continue
            raise PolicyDenied(f"missing arg '{name}'")
        value = args[name]
        if typ is int and isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value)  # models sometimes send "42" instead of 42; harmless
        if typ is list and isinstance(value, list):
            value = [int(v) if isinstance(v, str) and v.strip().isdigit() else v for v in value]
        if not isinstance(value, typ) or (typ is int and isinstance(value, bool)):
            raise PolicyDenied(f"arg '{name}' must be {typ.__name__}")
        clean[name] = value
    return clean


def check(tool: Tool, args: dict, ws: Workspace) -> None:
    """Raise PolicyDenied unless a rule explicitly allows this action."""
    if tool.rule is not None:
        tool.rule(ws, args)
    elif tool.risk != READ:
        raise PolicyDenied(f"no rule permits '{tool.name}' (default deny)")


def needs_approval(tool: Tool) -> bool:
    return tool.risk == IRREVERSIBLE


# ------------------------------------------------------------------ approvers

class Approver:
    """The human owner. Subclasses decide HOW we ask. Every question is recorded."""

    def __init__(self):
        self.requests: list[dict] = []

    def ask(self, tool_name: str, args: dict, why: str) -> bool:
        """Yes/no approval for an irreversible action."""
        decision = self._decide(tool_name, args, why)
        self.requests.append({"tool": tool_name, "args": args, "why": why, "approved": decision})
        return decision

    def choose(self, question: str, options: list[str]) -> int | None:
        """Pick-one question when the agent is unsure. Returns option index or None."""
        idx = self._choose(question, options)
        if idx is not None and not 0 <= idx < len(options):
            idx = None
        self.requests.append({"tool": "ask_owner", "args": {}, "question": question,
                              "options": options, "choice": None if idx is None else options[idx]})
        return idx

    def _decide(self, tool_name, args, why) -> bool:
        return False   # default deny: if nobody answers, the answer is no

    def _choose(self, question, options) -> int | None:
        return None    # if nobody answers, nothing gets matched


class TerminalApprover(Approver):
    """Asks you in the terminal. Used for live demos."""

    @staticmethod
    def _input(prompt: str) -> str:
        try:
            return input(prompt).strip().lstrip("\ufeff")   # PowerShell pipes prepend a BOM
        except EOFError:   # nobody at the keyboard (piped / CI): no answer means no
            return ""

    def _decide(self, tool_name, args, why) -> bool:
        print("\n" + "=" * 64)
        print(f"  APPROVAL NEEDED: {tool_name}")
        for k, v in args.items():
            print(f"     {k}: {v}")
        print(f"  Agent's reason: {why}")
        answer = self._input("  Approve? [y/N] ").lower()
        print("=" * 64)
        return answer == "y"

    def _choose(self, question, options) -> int | None:
        print("\n" + "=" * 64)
        print(f"  THE AGENT IS UNSURE AND NEEDS YOU\n  {question}")
        for i, o in enumerate(options, 1):
            print(f"     {i}. {o}")
        print("     0. none of these")
        answer = self._input("  Your choice: ")
        print("=" * 64)
        return int(answer) - 1 if answer.isdigit() and answer != "0" else None


class WebApprover(Approver):
    """Asks you on a web page. The agent's thread waits here until the page answers.

    `pending` holds the open question for the page to show; the page calls
    `answer(...)`. If nobody answers within `timeout` seconds, the answer is no.
    """

    def __init__(self, timeout: float = 300):
        super().__init__()
        self.timeout = timeout
        self.pending: dict | None = None
        self._answered = threading.Event()
        self._answer = None

    def answer(self, value) -> None:
        """True/False for an approval; an option index (or None) for a question."""
        if self.pending is None:   # nothing is being asked (e.g. a double click)
            return
        self.pending = None
        self._answer = value
        self._answered.set()

    def _wait(self, request: dict):
        self._answer = None
        self._answered.clear()
        self.pending = request
        try:
            return self._answer if self._answered.wait(self.timeout) else None
        finally:
            self.pending = None

    def _decide(self, tool_name, args, why) -> bool:
        return self._wait({"kind": "approve", "tool": tool_name, "args": args, "why": why}) is True

    def _choose(self, question, options) -> int | None:
        idx = self._wait({"kind": "choose", "question": question, "options": options})
        return idx if isinstance(idx, int) and not isinstance(idx, bool) else None


class RuleApprover(Approver):
    """A pretend owner for automated tests."""

    def __init__(self, rule, choice_rule=None):
        super().__init__()
        self.rule = rule
        self.choice_rule = choice_rule or (lambda q, opts: None)

    def _decide(self, tool_name, args, why) -> bool:
        return bool(self.rule(tool_name, args))

    def _choose(self, question, options) -> int | None:
        return self.choice_rule(question, options)
