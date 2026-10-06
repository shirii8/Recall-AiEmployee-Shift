"""
The "brain": the AI model that decides the next step.

Plain English: the brain only ever does one thing. It reads the conversation
so far (goal + everything that happened) and answers with ONE next action as
JSON, like {"thought": "...", "tool": "get_order", "args": {"order_id": 3}}.
It never touches the store itself. The loop does that, after the guardrails
say yes.

Keeping the brain this small means we can swap models easily (Gemini today,
something else tomorrow) and test the whole agent with a fake, scripted brain.
"""
import json
import os
import sys
import time


class Brain:
    """Base class. `usage` counts model calls and tokens so the loop can enforce a budget."""

    def __init__(self):
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    def next_action(self, system: str, messages: list[dict]) -> str:
        """messages: [{"role": "user"|"model", "text": "..."}]. Returns raw text (should be JSON)."""
        raise NotImplementedError


class BrainUnavailable(RuntimeError):
    """The model can't be used right now and retrying won't help (e.g. the daily quota is spent)."""


class GeminiBrain(Brain):
    def __init__(self, model: str | None = None, api_key: str | None = None,
                 min_seconds_between_calls: float | None = None):
        from google import genai  # imported here so tests don't need the package configured

        super().__init__()
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("Set GEMINI_API_KEY (get a free key at https://aistudio.google.com/apikey)")
        self.client = genai.Client(api_key=key)
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
        # Free-tier keys have a small daily cap per model, so move to the next model when one runs out.
        names = os.environ.get("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash,gemini-3.1-flash-lite").split(",")
        self.fallbacks = [n.strip() for n in names if n.strip() and n.strip() != self.model]
        # Free-tier keys allow only a few requests per minute, so we can space calls out.
        self.gap = float(min_seconds_between_calls if min_seconds_between_calls is not None
                         else os.environ.get("GEMINI_MIN_SECONDS_BETWEEN_CALLS", "0"))
        self._last_call = 0.0

    def next_action(self, system: str, messages: list[dict]) -> str:
        from google.genai import types

        contents = [types.Content(role=m["role"], parts=[types.Part(text=m["text"])]) for m in messages]
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,                          # as repeatable as possible
            response_mime_type="application/json",  # ask Gemini for JSON only
            # We parse the JSON action ourselves, so the SDK's own tool-calling stays off.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        for attempt in range(5):
            wait = self.gap - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                self._last_call = time.time()
                resp = self.client.models.generate_content(model=self.model, contents=contents, config=config)
                meta = getattr(resp, "usage_metadata", None)
                self.usage["calls"] += 1
                self.usage["input_tokens"] += (getattr(meta, "prompt_token_count", 0) or 0)
                self.usage["output_tokens"] += ((getattr(meta, "candidates_token_count", 0) or 0)
                                                + (getattr(meta, "thoughts_token_count", 0) or 0))
                return resp.text or ""
            except Exception as e:  # rate limits / temporary server errors: wait and try again
                msg = str(e)
                if "PerDay" in msg:  # a daily quota resets in hours, so backing off for seconds is pointless
                    if not self.fallbacks:
                        raise BrainUnavailable(
                            f"Today's free quota is used up for {self.model} and every fallback model. "
                            f"Wait for it to reset, or set GEMINI_MODEL in .env to another model.") from e
                    spent, self.model = self.model, self.fallbacks.pop(0)
                    print(f"  daily quota for {spent} is used up; switching to {self.model}", file=sys.stderr)
                    continue
                offline = any(word in type(e).__name__ for word in ("Connect", "Timeout"))
                transient = offline or any(
                    code in msg for code in ("429", "500", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE"))
                if not transient or attempt == 4:
                    raise
                overloaded = "503" in msg or "UNAVAILABLE" in msg
                if overloaded and attempt >= 1 and self.fallbacks:  # still overloaded: stop waiting on it
                    busy, self.model = self.model, self.fallbacks.pop(0)
                    print(f"  {busy} is overloaded; switching to {self.model}", file=sys.stderr)
                    continue
                pause = min(60, 5 * 2 ** attempt)
                why = "no connection" if offline else "overloaded" if overloaded else "rate limited"
                print(f"  {self.model} unavailable ({why}); retrying in {pause}s", file=sys.stderr)
                time.sleep(pause)
        return ""


class ScriptedBrain(Brain):
    """A fake brain that replays pre-written actions. Lets us test everything except the AI itself."""

    def __init__(self, actions: list[dict]):
        super().__init__()
        self.actions = list(actions)
        self.seen: list[list[dict]] = []

    def next_action(self, system: str, messages: list[dict]) -> str:
        self.seen.append(list(messages))
        self.usage["calls"] += 1
        if not self.actions:
            return json.dumps({"thought": "script ended", "tool": "finish", "args": {"summary": "script ended"}})
        return json.dumps(self.actions.pop(0))
