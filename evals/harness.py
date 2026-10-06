"""
Runs ONE scenario from start to finish: fresh store -> agent -> grade.
Used by both the eval runner and the live demo.
"""
import socket
from pathlib import Path

from agent.brain import Brain
from agent.browser import Browser
from agent.loop import Agent
from agent.policy import Approver, RuleApprover
from agent.store_client import StoreClient
from agent.tools import Workspace
from evals.scenarios import Scenario, grade
from store.app import StoreServer


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_scenario(scenario: Scenario, brain: Brain, workdir: Path, approver: Approver | None = None,
                 headless: bool = True, log=print, goal: str | None = None, max_steps: int = 30,
                 trace_dir: str | Path | None = "runs", port: int | None = None) -> dict:
    db_path, files_dir = scenario.setup(Path(workdir) / scenario.name)
    approver = approver or RuleApprover(scenario.owner_rule, scenario.owner_choice)
    with StoreServer(str(db_path), port=port or free_port(), faults=scenario.faults) as server:
        store = StoreClient(server.url)
        browser = Browser(server.url, headless=headless)
        try:
            agent = Agent(Workspace(store, browser, files_dir), brain, approver,
                          max_steps=max_steps, log=log, trace_dir=trace_dir)
            result = agent.run(goal or scenario.goal)
        finally:
            browser.close()
        problems = grade(scenario, store, approver.requests)
    return {
        "scenario": scenario.name, "skill": scenario.skill, "passed": not problems and result.finished,
        "problems": problems if result.finished else [f"did not finish: {result.stop_reason}"] + problems,
        "steps": result.steps, "writes": result.writes, "seconds": result.seconds,
        "approvals_asked": len(approver.requests), "summary": result.summary,
        "tokens": result.usage.get("input_tokens", 0) + result.usage.get("output_tokens", 0),
        "trace": result.trace_path,
    }
