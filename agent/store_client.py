"""
A tiny HTTP client for the store's API.

Plain English: the agent never touches the database directly. Like a human
employee, it goes through the store's "front door" (the API).
"""
import time

import requests  # Python library, send HTTP requests to APIs and servers.

class StoreError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(
            message
        )  # gives the error message to Python's built-in Exception class.
        self.status_code = status_code   # None = couldn't reach the store at all

    @property  # lets you access a method like an attribute. no () req while accessing methods
    def transient(self) -> bool:
        """Worth retrying? Yes for outages (5xx / unreachable), no for 'you asked for something invalid' (4xx)."""
        return self.status_code is None or self.status_code >= 500
    
# Send request → handle connection failure → check HTTP status → raise an error if needed → otherwise return the data.

class StoreClient:
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, **kwargs):
        # Reads are always safe to repeat, so retry them on temporary errors with a growing pause.
        # Writes are NOT retried here; the agent's executor decides that per tool (refunds never are).
        attempts = 3 if method == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                return self._once(
                    method, path, **kwargs
                )  # _call() delegates the actual HTTP request to _once().
            except StoreError as e:
                if not e.transient or attempt == attempts:
                    raise #not worth retrying
                time.sleep(0.2 * 2 ** attempt)

    def _once(self, method: str, path: str, **kwargs):
        try:
            r = requests.request(
                method, self.base_url + path, timeout=self.timeout, **kwargs
            )  # **kwargs- passes extra arguments, such as JSON data for a POST request.
        except requests.RequestException as e:
            raise StoreError(f"store unreachable: {e}") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get(
                    "detail", r.text
                )  # uses the "detail" field if present; otherwise, it uses the raw response text.
            except ValueError:  # handles cases where the response isn't valid JSON.
                detail = r.text
            raise StoreError(f"{r.status_code}: {detail}", r.status_code)
        return r.json()

    # reads
    def list_orders(self, status=None): 
        return self._call("GET", "/api/orders", params={"status": status} if status else None)

    def get_order(self, order_id: int):
        return self._call("GET", f"/api/orders/{order_id}")

    def get_order_full(self, order_id: int):
        """Trusted read for guardrail code only. Never exposed to the LLM as a tool."""
        return self._call("GET", f"/api/internal/orders/{order_id}")

    def list_products(self):
        return self._call("GET", "/api/products")

    def list_refunds(self):
        return self._call("GET", "/api/refunds")

    def list_emails(self):
        return self._call("GET", "/api/emails")

    # writes
    def mark_paid(self, order_id: int, payment_id: str):
        return self._call("POST", f"/api/orders/{order_id}/mark_paid", json={"payment_id": payment_id})

    def flag(self, order_id: int, reason: str):
        return self._call("POST", f"/api/orders/{order_id}/flag", json={"reason": reason})

    def set_stock(self, sku: str, stock: int):
        return self._call("POST", f"/api/products/{sku}/stock", json={"stock": stock})

    def refund(self, order_id: int, payment_id: str, amount: int):
        return self._call("POST", "/api/refunds",
                          json={"order_id": order_id, "payment_id": payment_id, "amount": amount})

    def accept_return(self, order_id: int):
        return self._call("POST", f"/api/orders/{order_id}/accept_return")

    def send_email(self, order_id: int, subject: str, body: str):
        return self._call("POST", "/api/emails", json={"order_id": order_id, "subject": subject, "body": body})
