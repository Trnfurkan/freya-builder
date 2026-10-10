# mark_product: pre_process_code that records in the offer ledger that a product was settled by a tool
# (a promise requested, a note added), so the offer gate makes no further request for it.
# It only sets a product's state; it never touches the request counts. The builder prepends `PARAMS = json.loads(...)`.
# Rules for this file: only json, re and datetime; never raise; no double braces.
#
# PARAMS keys: ledger_var (default "offer_ledger"), product_key (default "product_id"), state (e.g. "noted")
#   settled_flag    optional variable set to "yes" once every product (vars[plans_var]["order"], from date_facts.py) is
#                   in one of settled_states; a transition to the closing node waits for it. The routes are evaluated
#                   after the tool results come back, so the closing tool is offered only after the last write
#                   answered, and it can never go out in the same message as a note.
#                   Seen in a practice run: close_call sent together with two add_note calls; the bank closed the call
#                   first and refused both notes ("the call is already closed").
#   settled_states  default ["promise_requested", "noted"]
#   plans_var       default "offer_plans"
import json


def _load(raw):
    try:
        out = json.loads(raw) if isinstance(raw, str) and raw else (raw if isinstance(raw, dict) else dict())
    except Exception:
        out = dict()
    return out if isinstance(out, dict) else dict()


try:
    _b = config.get("body")
    _lv = PARAMS.get("ledger_var") or "offer_ledger"
    _pid = str(_b.get(PARAMS.get("product_key") or "product_id") or "") if isinstance(_b, dict) else ""
    if _pid:
        _led = _load(vars.get(_lv))
        _led.setdefault("counts", dict())
        _led.setdefault("state", dict())[_pid] = PARAMS.get("state") or "settled"
        _led.setdefault("current", "")
        vars[_lv] = json.dumps(_led)
        if PARAMS.get("settled_flag"):
            _order = _load(vars.get(PARAMS.get("plans_var") or "offer_plans")).get("order") or []
            _ok = PARAMS.get("settled_states") or ["promise_requested", "noted"]
            _all = bool(_order) and all(_led["state"].get(x) in _ok for x in _order)
            vars[PARAMS["settled_flag"]] = "yes" if _all else "no"
except Exception:
    pass
