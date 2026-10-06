"""
Live demo: watch Shift work on one scenario, and approve risky actions yourself.

    python run.py                                   # list scenarios
    python run.py ambiguous_match                   # it will ask YOU which order a payment belongs to
    python run.py duplicate_refund --show-browser   # see Chrome open (good for a demo video)
    python run.py razorpay_reconcile --goal "Mark paid orders as paid"   # your own wording

You are the store owner: when the agent wants to refund or email, it asks YOU in the terminal.
"""
import argparse
import tempfile
from pathlib import Path

from dotenv_lite import load_env, utf8_console
from agent.brain import BrainUnavailable, GeminiBrain
from agent.policy import TerminalApprover
from evals.harness import run_scenario
from evals.scenarios import BY_NAME, SCENARIOS


def main():
    utf8_console()
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?")
    ap.add_argument("--goal", help="give the agent your own goal instead of the scenario's")
    ap.add_argument("--show-browser", action="store_true", help="open a visible Chrome window")
    args = ap.parse_args()

    if not args.scenario or args.scenario not in BY_NAME:
        print("Pick a scenario:\n")
        for s in SCENARIOS:
            print(f"  {s.name:<22} {s.skill}")
        return

    scenario = BY_NAME[args.scenario]
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = run_scenario(scenario, GeminiBrain(), Path(tmp), approver=TerminalApprover(),
                             headless=not args.show_browser, goal=args.goal)
        except BrainUnavailable as e:
            raise SystemExit(f"Stopped: {e}")
    print("─" * 60)
    if args.goal:
        print("Custom goal, so the auto-grader may not apply. Check the trace in runs/.")
    print("GRADE:", "PASS" if r["passed"] else "FAIL")
    for p in r["problems"]:
        print("  -", p)
    print(f"steps={r['steps']} writes={r['writes']} approvals_asked={r['approvals_asked']} time={r['seconds']}s")
    print(f"Open the decision trace in your browser: {r['trace']}")


if __name__ == "__main__":
    main()
