# mark_position: code for a passthrough code node, reached by a tool-call transition from the working node when the
# customer agreed an amount and a date for product number k (get_account order) in their own words. It marks that
# product "agreed" in the offer ledger so the offer gate's walk never asks for it. The builder prepends
# `PARAMS = json.loads(...)`. Rules for this file: only json, re and datetime; never raise; no double braces.
#
# Why a code node and not a tool hook: code nodes run one at a time, so this write cannot be lost. Seen in practice
# runs (fix13-ab): pre-hooks of two tool calls sent in one message both rewrote the ledger and one write was lost.
# It never touches the request counts, and it only moves an "open" product: a refused or settled one stays as it is.
#
# PARAMS keys: product_index (1-based), ledger_var (default "offer_ledger"), plans_var (default "offer_plans")
import json


def _load(raw):
    try:
        out = json.loads(raw) if isinstance(raw, str) and raw else (raw if isinstance(raw, dict) else dict())
    except Exception:
        out = dict()
    return out if isinstance(out, dict) else dict()


try:
    _lv = PARAMS.get("ledger_var") or "offer_ledger"
    _order = _load(vars.get(PARAMS.get("plans_var") or "offer_plans")).get("order") or []
    _k = int(PARAMS.get("product_index") or 0)
    if 1 <= _k <= len(_order):
        _pid = _order[_k - 1]
        _led = _load(vars.get(_lv))
        _led.setdefault("counts", dict())
        _st = _led.setdefault("state", dict())
        _led.setdefault("current", "")
        if _st.get(_pid, "open") == "open":
            _st[_pid] = "agreed"
        vars[_lv] = json.dumps(_led)
except Exception:
    pass
