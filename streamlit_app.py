"""
Shift on the web: the same live demo as run.py, in a browser tab.

    streamlit run streamlit_app.py

You are the store owner: pick a scenario, watch the decision trace appear step
by step, and when the agent wants to refund or email, it asks YOU on the page.

The agent runs in a background thread so the page stays alive while it works.
The page just re-reads that thread's log once a second.
"""
import datetime
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import streamlit as st

from agent.brain import Brain, BrainUnavailable, GeminiBrain
from agent.policy import WebApprover
from dotenv_lite import load_env
from evals.harness import run_scenario
from evals.scenarios import BY_NAME, SCENARIOS, Scenario

KEY_HELP = "https://aistudio.google.com/apikey"
SETTINGS = ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_FALLBACK_MODELS", "GEMINI_MIN_SECONDS_BETWEEN_CALLS")


def load_settings() -> None:
    """Settings come from .env on your laptop, or from Streamlit's secrets when deployed."""
    load_env()
    for name in SETTINGS + ("DEMO_RUNS_PER_DAY",):
        try:
            if name in st.secrets:
                os.environ.setdefault(name, str(st.secrets[name]))
        except Exception:   # no secrets file at all: that's fine
            return


@st.cache_resource
def shared() -> dict:
    """One copy for every visitor: today's demo-run count and how many runs are in flight."""
    return {"day": None, "demo_runs": 0, "lock": threading.Lock(), "slots": threading.Semaphore(2)}


@st.cache_resource(show_spinner="Getting the browser ready (first start only)...")
def install_browser() -> str:
    """Download Playwright's Chromium once per container. Returns an error message, or ''."""
    done = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                          capture_output=True, text=True)
    return "" if done.returncode == 0 else (done.stderr or done.stdout)[-600:]


def demo_runs_left() -> int:
    box, limit = shared(), int(os.environ.get("DEMO_RUNS_PER_DAY", "10"))
    with box["lock"]:
        if box["day"] != datetime.date.today():
            box["day"], box["demo_runs"] = datetime.date.today(), 0
        return max(0, limit - box["demo_runs"])


class LiveRun:
    """One scenario running in a background thread, with everything the page needs to show it."""

    def __init__(self, scenario: Scenario, goal: str | None, make_brain, on_done=lambda: None):
        self.scenario, self.goal = scenario, goal
        self.lines: list[str] = []
        self.approver = WebApprover()
        self.result: dict | None = None
        self.trace_html = ""
        self.error = ""
        self.done = False
        self._make_brain, self._on_done = make_brain, on_done
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                brain: Brain = self._make_brain()
                r = run_scenario(self.scenario, brain, Path(tmp), approver=self.approver,
                                 log=self.lines.append, goal=self.goal, trace_dir=Path(tmp) / "runs")
                if r["trace"]:
                    self.trace_html = Path(r["trace"]).read_text(encoding="utf-8")
                self.result = r
        except BrainUnavailable as e:
            self.error = f"Stopped: {e}"
        except Exception as e:   # a bad key, no network...: show it on the page instead of dying silently
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self._on_done()
            self.done = True


def answer(run: LiveRun, request: dict, value=None, pick_key: str = "") -> None:
    if run.approver.pending is not request:   # a late click on a question that's already closed
        return
    if pick_key:   # a "which one?" question: read the radio as it is at the moment of the click
        pick = st.session_state.get(pick_key)
        value = pick if isinstance(pick, int) and pick < len(request["options"]) else None
    run.approver.answer(value)


def show_question(run: LiveRun) -> None:
    request = run.approver.pending
    if request is None:
        return
    n = len(run.approver.requests)   # makes each question's buttons distinct
    with st.container(border=True):
        if request["kind"] == "approve":
            st.subheader(f"Approval needed: {request['tool']}")
            st.json(request["args"])
            st.write(f"**Agent's reason:** {request['why']}")
            yes, no = st.columns(2)
            yes.button("Approve", key=f"yes{n}", type="primary", use_container_width=True,
                       on_click=answer, args=(run, request, True))
            no.button("Decline", key=f"no{n}", use_container_width=True,
                      on_click=answer, args=(run, request, False))
        else:
            st.subheader("The agent is unsure and needs you")
            st.text(request["question"])
            options = request["options"] + ["None of these"]
            st.radio("Your choice", range(len(options)), format_func=lambda i: options[i], key=f"pick{n}")
            st.button("Answer", key=f"answer{n}", type="primary", on_click=answer,
                      args=(run, request), kwargs={"pick_key": f"pick{n}"})
        st.caption(f"No answer within {int(run.approver.timeout / 60)} minutes counts as no.")


def show_run(live: bool) -> None:
    run: LiveRun = st.session_state["run"]
    if live and run.done:
        st.rerun()   # leave live mode: redraw the whole page once, without the timer
    show_question(run)
    if not run.done:
        st.info("Shift is working..." if run.approver.pending is None else "Shift is waiting for you.")
    st.code("\n".join(run.lines) or "Starting the store...", language=None)
    if run.error:
        st.error(run.error)
    r = run.result
    if not r:
        return
    if run.goal:
        st.caption("Custom goal, so the auto-grader may not apply. Read the trace instead.")
    (st.success if r["passed"] else st.error)("GRADE: " + ("PASS" if r["passed"] else "FAIL"))
    for p in r["problems"]:
        st.write(f"- {p}")
    for col, (label, value) in zip(st.columns(4), [("Steps", r["steps"]), ("Changes", r["writes"]),
                                                   ("Owner asked", r["approvals_asked"]),
                                                   ("Seconds", r["seconds"])]):
        col.metric(label, value)
    if run.trace_html:
        st.download_button("Download the decision trace (HTML)", run.trace_html,
                           file_name=f"shift_{r['scenario']}.html", mime="text/html")
        with st.expander("Decision trace timeline", expanded=True):
            # trace_html escapes every value it prints, so it is safe to embed as a page.
            if hasattr(st, "iframe"):
                st.iframe(run.trace_html, height=700)
            else:   # older Streamlit
                import streamlit.components.v1 as components
                components.html(run.trace_html, height=700, scrolling=True)


def start(scenario: Scenario, goal: str, own_key: str) -> None:
    box, demo_key = shared(), os.environ.get("GEMINI_API_KEY", "")
    if not own_key and not demo_key:
        st.error(f"Paste a Gemini API key in the sidebar first. A free one takes a minute: {KEY_HELP}")
        return
    if not own_key and demo_runs_left() == 0:
        st.error(f"Today's shared demo runs are used up. Paste your own free Gemini key in the sidebar: {KEY_HELP}")
        return
    if not box["slots"].acquire(blocking=False):
        st.warning("Two other runs are in progress right now. Try again in a minute.")
        return
    if not own_key:
        with box["lock"]:
            box["demo_runs"] += 1
    key = own_key or demo_key
    st.session_state["run"] = LiveRun(scenario, goal.strip() or None, lambda: GeminiBrain(api_key=key),
                                      on_done=box["slots"].release)


def main() -> None:
    st.set_page_config(page_title="Shift: an AI operations employee", page_icon="🧾", layout="wide")
    load_settings()
    st.title("Shift: an AI operations employee you can trust")
    st.write("Give it a store owner's goal. It reads the bank export, checks orders through the store's API, "
             "opens the admin dashboard in a real browser when it has to, and makes the fixes. Every change is "
             "re-read and verified, guardrails in code refuse bad actions, and refunds and emails wait for **you**.")

    problem = install_browser()
    if problem:
        st.warning(f"The browser could not be installed, so scenarios that need the dashboard will fail.\n\n{problem}")

    with st.sidebar:
        st.header("Gemini API key")
        own_key = st.text_input("Your key", type="password", help="Used for this run only. It is never saved.")
        st.caption(f"Free key: {KEY_HELP}")
        if os.environ.get("GEMINI_API_KEY"):
            st.caption(f"No key? {demo_runs_left()} shared demo run(s) left today.")
        st.divider()
        st.caption("The store is a fake one that misbehaves on purpose (silent write failures, a 503, a slow "
                   "page). Each run gets a fresh copy, so nothing you do here is real.")

    run: LiveRun | None = st.session_state.get("run")
    busy = run is not None and not run.done
    name = st.selectbox("Scenario", [s.name for s in SCENARIOS], disabled=busy,
                        format_func=lambda n: f"{n}  ·  {BY_NAME[n].skill}")
    scenario = BY_NAME[name]
    st.caption(f"Goal: {scenario.goal}")
    goal = st.text_input("Or give it your own goal (optional)", disabled=busy,
                         placeholder="e.g. Mark paid orders as paid")
    if st.button("Run Shift", type="primary", disabled=busy):
        start(scenario, goal, own_key.strip())
        run = st.session_state.get("run")

    if run is not None:
        live = not run.done
        st.fragment(run_every=1 if live else None)(show_run)(live)


main()
