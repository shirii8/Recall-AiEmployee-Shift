"""
Pack: processing customer returns. The "second job" that proves Shift generalizes.

Plain English: to teach Shift a whole new job, I wrote only THIS file (plus a
store endpoint for returns). The loop, the brain and the guardrail engine
didn't change by a single line. The new job also reuses an existing button
(issue_refund from store_ops), with its existing safety rules and approval.

The store's return policy, enforced in code below:
  - only paid orders can be returned,
  - the customer must have asked (a row in returns.csv),
  - the item must be unopened,
  - and the request must be within 7 days of delivery.
Anything else gets flagged for the owner, not forced.
"""
from agent.packs.store_ops import order_full
from agent.policy import PolicyDenied
from agent.tools import WRITE, Pack, Tool

RETURN_WINDOW_DAYS = 7


def _accept_return(ws, a):
    return ws.store.accept_return(a["order_id"])


def _rule_accept_return(ws, a):
    order = order_full(ws, a["order_id"])
    if order["status"] != "paid":
        raise PolicyDenied(f"order {order['id']} is '{order['status']}'; only paid orders can be returned")
    req = next((r for r in ws.read_csv("returns.csv") if r["order_id"] == order["id"]), None)
    if req is None:
        raise PolicyDenied(f"no return request for order {order['id']} in returns.csv")
    if str(req["condition"]).lower() != "unopened":
        raise PolicyDenied(f"item is '{req['condition']}'; policy only accepts unopened items. Flag it.")
    if req["days_since_delivery"] > RETURN_WINDOW_DAYS:
        raise PolicyDenied(f"requested {req['days_since_delivery']} days after delivery; "
                           f"window is {RETURN_WINDOW_DAYS} days. Flag it.")


def _snap_return(ws, a):
    o = ws.store.get_order(a["order_id"])
    stock = next(p["stock"] for p in ws.store.list_products() if p["sku"] == o["sku"])
    return {"status": o["status"], "stock_of_" + o["sku"]: stock}


def _verify_return(a, before, after):
    stock_key = next(k for k in after if k.startswith("stock_of_"))
    ok = after["status"] == "returned" and after[stock_key] > before[stock_key]
    return ok, f"store shows status={after['status']}, {stock_key}: {before[stock_key]} -> {after[stock_key]}"


PACK = Pack(
    name="returns",
    tools=[
        Tool("accept_return", "Accept a customer's return: marks the order returned and puts the item back in stock.",
             {"order_id": (int, "order id")}, WRITE, _accept_return, _rule_accept_return, _snap_return,
             _verify_return),
    ],
    guidance=f"""
- Return requests are in returns.csv. Policy: paid orders only, item unopened, within {RETURN_WINDOW_DAYS} days
  of delivery.
- For an eligible return: accept_return, then refund the order's payment in full with issue_refund (owner
  approval). The order's payment_id tells you which payment to refund.
- If a request breaks the policy, don't accept it: flag_order with the reason so the owner can decide.""",
)
