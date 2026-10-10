# pre_process_code for the tool that records a promise. The builder prepends one line `PARAMS = json.loads(...)`.
# It checks the model's request against `promise_rules`, written by date_facts.py from the account data:
#   - the product exists,
#   - the date is one of that product's allowed dates (check_dates),
#   - the amount is from the floor up to the full amount, both included (check_amount).
# A request that breaks a rule is not allowed to record anything: the product id is blanked, so the bank rejects it
# without a promise, and `vars[guard_var]` holds the reason for the post-hook (post_result.py, guard_var) to tell the
# model. When the rules are missing or empty it lets everything through: the bank stays the judge.
# Rules for this file: only json, re and datetime; never raise; no double braces.
#
# PARAMS keys:
#   product_key, date_key, amount_key   body keys of the promise request
#   check_dates, check_amount           booleans, from what the brief says
#   guard_var                           variable for the reason (default "promise_guard")
import json


def _rules(vars):
    raw = vars.get("promise_rules")
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw) if raw else dict()
        return out if isinstance(out, dict) else dict()
    except Exception:
        return dict()


def check(body, rules, p):
    """None when the request may go out, else the reason in plain words."""
    if not rules:
        return None
    pid = str(body.get(p.get("product_key") or "product_id") or "").strip()
    if pid not in rules:
        return "product_id " + (pid or "(empty)") + " is not one of the customer's products (" + ", ".join(sorted(rules)) + ")."
    r = rules[pid]
    date = str(body.get(p.get("date_key") or "date") or "").strip()[:10]
    if p.get("check_dates", True) and r.get("dates") and date not in r["dates"]:
        later = [d for d in r["dates"] if d > date]
        if date and date < r["dates"][0]:
            hint = "the earliest allowed date is " + r["dates"][0]
        elif later:
            hint = "the next allowed date after it is " + later[0]
        else:
            hint = "the latest allowed date is " + r["dates"][-1]
        return "date " + (date or "(empty)") + " cannot be recorded for " + pid + ": it is not an allowed promise date (" + hint + ")."
    if p.get("check_amount", True):
        try:
            amount = float(body.get(p.get("amount_key") or "amount"))
        except Exception:
            return "amount " + str(body.get(p.get("amount_key") or "amount")) + " is not a number."
        lo, hi = r.get("floor"), r.get("full")
        if lo is not None and amount < float(lo) - 0.005:
            return "amount %.2f is below the floor %.2f for %s." % (amount, float(lo), pid)
        if hi is not None and amount > float(hi) + 0.005:
            return "amount %.2f is above the full past-due amount %.2f for %s." % (amount, float(hi), pid)
    return None


try:
    _b = config.get("body")
    _gv = PARAMS.get("guard_var") or "promise_guard"
    vars[_gv] = ""
    if isinstance(_b, dict):
        _why = check(_b, _rules(vars), PARAMS)
        if _why:
            vars[_gv] = "NOT RECORDED, blocked before sending: " + _why
            _b[PARAMS.get("product_key") or "product_id"] = ""
except Exception:
    pass
