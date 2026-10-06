"""
Run the whole test set several times and write a reliability report.

    python -m evals.run_evals                 # every scenario, 3 runs each
    python -m evals.run_evals --runs 1        # quicker
    python -m evals.run_evals --only upi_dashboard prompt_injection

Plain English: one lucky run proves nothing, because AI models can answer
differently each time. So we run every scenario several times and report how
often it passes. That number is the honest measure of "reliable".
"""
import argparse
import json
import tempfile
import time
from pathlib import Path

from dotenv_lite import load_env, utf8_console
from agent.brain import BrainUnavailable, GeminiBrain
from evals.harness import run_scenario
from evals.scenarios import BY_NAME, SCENARIOS


def main():
    utf8_console()
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3, help="runs per scenario")
    ap.add_argument("--only", nargs="*", help="scenario names to run")
    ap.add_argument("--quiet", action="store_true", help="don't print every step")
    args = ap.parse_args()

    scenarios = [BY_NAME[n] for n in args.only] if args.only else SCENARIOS
    brain = GeminiBrain()
    log = (lambda *_: None) if args.quiet else print
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for s, i in [(s, i) for s in scenarios for i in range(1, args.runs + 1)]:
            print(f"\n> {s.name}  (run {i}/{args.runs})")
            try:
                r = run_scenario(s, brain, Path(tmp), log=log)
            except BrainUnavailable as e:  # nothing more can run, so don't record fake failures
                print(f"\nStopped early: {e}\nThe report below covers only the runs that completed.")
                break
            except Exception as e:  # an infrastructure crash counts as a failure, not a hidden one
                r = {"scenario": s.name, "skill": s.skill, "passed": False,
                     "problems": [f"crashed: {type(e).__name__}: {e}"], "steps": 0, "writes": 0,
                     "seconds": 0, "approvals_asked": 0, "summary": "", "tokens": 0}
            r["run"] = i
            results.append(r)
            print(("  PASS" if r["passed"] else "  FAIL") +
                  ("" if r["passed"] else "  → " + "; ".join(r["problems"])))

    out = Path("evals/results")
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"model": brain.model, "results": results}, indent=2),
                                      encoding="utf-8")
    report = make_report(results, brain.model, args.runs)
    (out / "report.md").write_text(report, encoding="utf-8")
    print("\n" + report)


def make_report(results: list[dict], model: str, runs: int) -> str:
    total_pass = sum(r["passed"] for r in results)
    lines = [
        "# Shift reliability report", "",
        f"Model: `{model}` · {runs} run(s) per scenario · {time.strftime('%Y-%m-%d %H:%M')}", "",
        f"**Overall: {total_pass}/{len(results)} runs passed ({100 * total_pass / max(1, len(results)):.0f}%)**", "",
        "| Scenario | What it tests | Passed | Avg steps | Avg seconds | Avg tokens |",
        "|---|---|---|---|---|---|",
    ]
    names = list(dict.fromkeys(r["scenario"] for r in results))
    for name in names:
        rs = [r for r in results if r["scenario"] == name]
        p = sum(r["passed"] for r in rs)
        lines.append(f"| `{name}` | {rs[0]['skill']} | {p}/{len(rs)} | "
                     f"{sum(r['steps'] for r in rs) / len(rs):.1f} | {sum(r['seconds'] for r in rs) / len(rs):.1f} | "
                     f"{sum(r.get('tokens', 0) for r in rs) / len(rs):,.0f} |")
    failures = [r for r in results if not r["passed"]]
    lines += ["", "## Failures", ""]
    if not failures:
        lines.append("None.")
    for r in failures:
        lines.append(f"- `{r['scenario']}` run {r['run']}: " + "; ".join(r["problems"])
                     + (f" (trace: `{r['trace']}`)" if r.get("trace") else ""))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
