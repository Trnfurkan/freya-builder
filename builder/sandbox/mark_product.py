# mark_product: pre_process_code that records in the offer ledger that a product was settled by a tool
# (a promise requested, a note added), so the offer gate makes no further request for it.
# It only sets a product's state; it never touches the request counts. The builder prepends `PARAMS = json.loads(...)`.
# Rules for this file: only json, re and datetime; never raise; no double braces.
#
# PARAMS keys: ledger_var (default "offer_ledger"), product_key (default "product_id"), state (e.g. "noted")
import json

try:
    _b = config.get("body")
    _lv = PARAMS.get("ledger_var") or "offer_ledger"
    _pid = str(_b.get(PARAMS.get("product_key") or "product_id") or "") if isinstance(_b, dict) else ""
    if _pid:
        _raw = vars.get(_lv)
        try:
            _led = json.loads(_raw) if isinstance(_raw, str) and _raw else (_raw if isinstance(_raw, dict) else dict())
        except Exception:
            _led = dict()
        if not isinstance(_led, dict):
            _led = dict()
        _led.setdefault("counts", dict())
        _led.setdefault("state", dict())[_pid] = PARAMS.get("state") or "settled"
        _led.setdefault("current", "")
        vars[_lv] = json.dumps(_led)
except Exception:
    pass
