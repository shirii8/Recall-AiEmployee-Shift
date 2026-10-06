"""
The agent's browser: opens the store's admin page and reads it, like a human would.

Plain English: some information only lives on the admin dashboard (the UPI
transaction id a customer typed, and their note). So the agent opens the page
in a real (headless) Chrome and reads it.

Browsers are the flakiest part of any agent: pages load slowly, servers hiccup.
So this file does three careful things:
  1. Waits for the data to actually appear (not just for the page to open).
  2. Retries a few times with a growing pause if something goes wrong.
  3. Returns a clear error instead of crashing, so the agent can decide what to do.
"""
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


class BrowserError(Exception):
    pass


class Browser:
    def __init__(self, base_url: str, headless: bool = True, attempts: int = 3):
        self.base_url = base_url.rstrip("/")
        self.headless = headless
        self.attempts = attempts
        self._pw = None
        self._browser = None
        self._page = None

    def _ensure_open(self):
        # Start Chrome lazily: only if the agent actually needs the browser.
        if self._page is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._page = self._browser.new_page()

    def read_admin_order(self, order_id: int) -> dict:
        """Open /admin/orders/<id> and read the dashboard-only fields."""
        self._ensure_open()
        url = f"{self.base_url}/admin/orders/{int(order_id)}"
        last_error = None
        for attempt in range(1, self.attempts + 1):
            try:
                resp = self._page.goto(url, timeout=8000)
                if resp is None or resp.status >= 500:
                    raise BrowserError(f"page returned {resp.status if resp else 'nothing'}")
                if resp.status == 404:
                    raise BrowserError(f"order {order_id} not found")  # don't retry a real 404
                # Wait until the late-loading data has really arrived.
                self._page.wait_for_selector('#utr[data-loaded="1"]', timeout=5000)
                return {
                    "order_id": int(order_id),
                    "status": self._page.inner_text("#status").strip(),
                    "submitted_utr": self._page.inner_text("#utr").strip(),
                    "customer_note": self._page.inner_text("#note").strip(),
                    "attempts_needed": attempt,
                }
            except BrowserError as e:
                if "not found" in str(e):
                    raise
                last_error = e
            except PlaywrightError as e:
                last_error = e
            time.sleep(0.5 * attempt)  # back off a little more each time
        raise BrowserError(f"could not read admin page after {self.attempts} attempts: {last_error}")

    def close(self):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        self._pw = self._browser = self._page = None
