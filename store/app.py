"""
The fake online store: a JSON API plus a small admin dashboard.

Plain English: this pretends to be a real shop's backend (like Tessch). The
agent talks to it the way a human assistant would: through the API for most
work, and through the admin web page for things only the dashboard shows
(the UPI transaction id a customer typed in, and the customer's note).

It can also misbehave on purpose ("faults"), so we can prove the agent copes
with a flaky world: a write that silently doesn't save, a slow page, a page
that errors once.
"""
import html
import json
import threading
import time
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from store.db import connect

# Fields the public API hides. Only the admin dashboard (and the trusted
# guardrail code, via /internal) can see them.
DASHBOARD_ONLY = ("submitted_utr", "customer_note")


class MarkPaid(BaseModel):
    payment_id: str


class Flag(BaseModel):
    reason: str


class Stock(BaseModel):
    stock: int


class Refund(BaseModel):
    order_id: int
    payment_id: str
    amount: int


class Email(BaseModel):
    order_id: int
    subject: str
    body: str


def create_app(db_path: str, faults: dict | None = None) -> FastAPI:
    faults = dict(faults or {})
    app = FastAPI(title="Fake Store")

    @contextmanager
    def db():
        # Commit on success, roll back on error, and always close: an open handle keeps
        # the .db file locked on Windows, so the next run couldn't delete or replace it.
        conn = connect(db_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def public(order_row) -> dict:
        d = dict(order_row)
        for f in DASHBOARD_ONLY:
            d.pop(f, None)
        return d

    def order_or_404(conn, order_id: int):
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"order {order_id} not found")
        return row

    # ---------------- reads ----------------
    @app.get("/api/orders")
    def list_orders(status: str | None = None):
        with db() as conn:
            if status:
                rows = conn.execute("SELECT * FROM orders WHERE status = ? ORDER BY id", (status,))
            else:
                rows = conn.execute("SELECT * FROM orders ORDER BY id")
            return [public(r) for r in rows.fetchall()]

    @app.get("/api/orders/{order_id}")
    def get_order(order_id: int):
        with db() as conn:
            d = public(order_or_404(conn, order_id))
            d["flags"] = [r["reason"] for r in conn.execute(
                "SELECT reason FROM flags WHERE order_id = ?", (order_id,))]
            return d

    @app.get("/api/internal/orders/{order_id}")
    def get_order_full(order_id: int):
        """Full record incl. dashboard-only fields. Used by trusted guardrail code, never by the LLM."""
        with db() as conn:
            return dict(order_or_404(conn, order_id))

    @app.get("/api/products")
    def list_products():
        with db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM products ORDER BY sku")]

    @app.get("/api/refunds")
    def list_refunds():
        with db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM refunds ORDER BY id")]

    @app.get("/api/emails")
    def list_emails():
        with db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM emails ORDER BY id")]

    # ---------------- writes ----------------
    @app.post("/api/orders/{order_id}/mark_paid")
    def mark_paid(order_id: int, body: MarkPaid):
        if faults.pop("silent_write_fail_once", False):
            # Simulates a real-world bug: server says OK but nothing was saved.
            return {"ok": True}
        with db() as conn:
            order_or_404(conn, order_id)
            # Conditional update: only flips pending -> paid. If two workers race,
            # only one wins, because the WHERE clause re-checks the status.
            cur = conn.execute(
                "UPDATE orders SET status = 'paid', payment_id = ? "
                "WHERE id = ? AND status = 'pending_payment'",
                (body.payment_id, order_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(409, "order is not pending payment")
            return {"ok": True}

    @app.post("/api/orders/{order_id}/flag")
    def flag(order_id: int, body: Flag):
        with db() as conn:
            order_or_404(conn, order_id)
            conn.execute("INSERT INTO flags (order_id, reason) VALUES (?, ?)", (order_id, body.reason))
            return {"ok": True}

    @app.post("/api/products/{sku}/stock")
    def set_stock(sku: str, body: Stock):
        if faults.pop("stock_api_error_once", False):
            # Simulates a temporary outage: fails once, works on retry.
            raise HTTPException(503, "service temporarily unavailable")
        with db() as conn:
            cur = conn.execute("UPDATE products SET stock = ? WHERE sku = ?", (body.stock, sku))
            if cur.rowcount == 0:
                raise HTTPException(404, f"product {sku} not found")
            return {"ok": True}

    @app.post("/api/orders/{order_id}/accept_return")
    def accept_return(order_id: int):
        with db() as conn:  # one transaction: status change + restock succeed or fail together
            order = order_or_404(conn, order_id)
            cur = conn.execute("UPDATE orders SET status = 'returned' WHERE id = ? AND status = 'paid'", (order_id,))
            if cur.rowcount == 0:
                raise HTTPException(409, "only paid orders can be returned")
            conn.execute("UPDATE products SET stock = stock + ? WHERE sku = ?", (order["qty"], order["sku"]))
            return {"ok": True}

    @app.post("/api/refunds")
    def refund(body: Refund):
        with db() as conn:
            order_or_404(conn, body.order_id)
            try:
                conn.execute(
                    "INSERT INTO refunds (order_id, payment_id, amount) VALUES (?, ?, ?)",
                    (body.order_id, body.payment_id, body.amount),
                )
            except Exception:
                raise HTTPException(409, "payment already refunded")
            return {"ok": True}

    @app.post("/api/emails")
    def send_email(body: Email):
        with db() as conn:
            order = order_or_404(conn, body.order_id)
            conn.execute(
                "INSERT INTO emails (order_id, to_addr, subject, body) VALUES (?, ?, ?, ?)",
                (body.order_id, order["customer_email"], body.subject, body.body),
            )
            return {"ok": True}

    # ---------------- admin dashboard (HTML) ----------------
    @app.get("/admin/orders/{order_id}", response_class=HTMLResponse)
    def admin_order(order_id: int):
        if faults.pop("admin_page_error_once", False):
            raise HTTPException(500, "temporary dashboard error")
        with db() as conn:
            o = dict(order_or_404(conn, order_id))
        esc = lambda v: html.escape(str(v if v is not None else "-"))
        # Safe JS string literal (textContent never runs HTML, and "</" can't close the script tag).
        js = lambda v: json.dumps(str(v if v is not None else "-")).replace("</", "<\\/")
        slow = bool(faults.get("slow_admin_page"))
        # The UPI id and note load "late" via JS, like a real dashboard fetching data.
        loader = f"""
        <script>
          setTimeout(function () {{
            var u = document.getElementById('utr'); u.textContent = {js(o['submitted_utr'])};
            u.setAttribute('data-loaded', '1');
            document.getElementById('note').textContent = {js(o['customer_note'])};
          }}, {1500 if slow else 50});
        </script>"""
        return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Order #{o['id']} · Admin</title>
<style>body{{font-family:system-ui;max-width:640px;margin:40px auto;padding:0 16px}}
dt{{color:#666;margin-top:12px}}dd{{margin:2px 0 0;font-weight:600}}</style></head>
<body><h1>Order #{o['id']}</h1><dl>
<dt>Customer</dt><dd id="customer">{esc(o['customer_name'])}</dd>
<dt>Amount</dt><dd id="amount">₹{esc(o['amount'])}</dd>
<dt>Method</dt><dd id="method">{esc(o['method'])}</dd>
<dt>Status</dt><dd id="status">{esc(o['status'])}</dd>
<dt>UPI transaction id (submitted by customer)</dt><dd id="utr">loading…</dd>
<dt>Customer note</dt><dd id="note">loading…</dd>
</dl>{loader}</body></html>"""

    return app


class StoreServer:
    """Runs the store in a background thread so the agent can talk to it over HTTP."""

    def __init__(self, db_path: str, port: int = 8765, faults: dict | None = None):
        self.port = port
        config = uvicorn.Config(create_app(db_path, faults), host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("store server did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5)
