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
#   max_attempts                        requests per product that may reach the bank, retries included (0 = no limit);
#                                       counted in vars["promise_attempts"], a blocked request is not counted
#   named_date_key                      model-filled helper body key that says where the date comes from. It never
#                                       reaches the bank: it is removed here. When this key is set, it is REQUIRED:
#                                         YYYY-MM-DD    the last exact day the customer said for this product; the date
#                                                       must be the one the brief gives for that day (expected_date)
#                                         agent_offer   the customer accepted a date the agent proposed; allowed only for
#                                                       the brief's own offers: the product's persuasion plan dates
#                                                       (rules plan_dates) and agent_dates (date_params agent_offers)
#                                         no_exact_day  the customer gave no exact day; allowed only for the brief's date
#                                                       for that case (rules no_exact_day, date_params no_exact_day)
#                                       Missing, empty or unreadable: nothing is sent; the agent must confirm the day.
#   spoken_var                          optional variable an extraction fills with the last exact day the customer said
#                                       (YYYY-MM-DD), a witness independent of the tool call: a customer day in the
#                                       helper field must equal it, and while it holds a day, no_exact_day is refused and
#                                       agent_offer is limited to the persuasion plan.
#   spoken_required                     true: a customer day in the helper field is refused while spoken_var is empty
import json
import re


def _rules(vars):
    raw = vars.get("promise_rules")
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw) if raw else dict()
        return out if isinstance(out, dict) else dict()
    except Exception:
        return dict()


def expected_date(named, r):
    """(date to record, why) for a day the customer named, by the brief's choices kept in the product's rules.
    The date is None when nothing can be recorded for that day."""
    dates = r.get("dates") or []
    today, deadline = str(r.get("today") or (dates[0] if dates else "")), str(r.get("deadline") or "")
    if today and named < today:
        return None, "it is before today"
    if deadline and named > deadline:
        if r.get("after_deadline", "deadline") == "deadline":
            return deadline, "it is after the deadline " + deadline + ", so the latest date that can be recorded is " + deadline
        return None, "it is after the deadline " + deadline
    if named in dates:
        return named, "it is an allowed date"
    move = r.get("non_business_day", "next")
    later = [d for d in dates if d > named]
    earlier = [d for d in dates if d < named]
    if move == "next" and later:
        return later[0], "it is not a business day; the brief says to offer the next business day, " + later[0]
    if move == "previous" and (earlier or later):
        target = earlier[-1] if earlier else later[0]
        return target, "it is not a business day; the brief says to offer " + target
    return None, "it is not a business day and the brief gives no other day for it"


TOKENS = ("agent_offer", "no_exact_day")


def _iso(x):
    """'' for nothing, a token, 'YYYY-MM-DD', or '?<text>' for something unreadable."""
    x = str(x if x is not None else "").strip()
    if not x or x.startswith("{"):
        return ""
    if x.lower() in TOKENS:
        return x.lower()
    return x[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", x) else "?" + x


def source_check(date, named, r, pid, witness="", spoken_required=False):
    """None when the date agrees with where it came from, else the reason. `named` is _iso() of the helper field."""
    ask = (" Do not say it is recorded. Ask the customer which exact day they will pay, read the promise back, and after "
           "a clear yes send it again with customer_named_date set.")
    if not named:
        return "the request did not say where the date " + date + " comes from (customer_named_date is empty)." + ask
    if named.startswith("?"):
        return "customer_named_date must be YYYY-MM-DD, agent_offer or no_exact_day (got " + named[1:] + ")." + ask
    heard = witness if re.match(r"^\d{4}-\d{2}-\d{2}$", witness or "") else ""
    plan = list(r.get("plan_dates") or [])
    if named == "agent_offer":
        allowed = plan if heard else plan + list(r.get("agent_dates") or [])
        if date in allowed:
            return None
        return ("the agent never offered " + date + " for " + pid + " under the brief's rules (its own offers: "
                + (", ".join(sorted(set(allowed))) or "none") + (", and the customer said " + heard if heard else "")
                + ")." + ask)
    if named == "no_exact_day":
        if heard:
            return "the customer did say an exact day, " + heard + "; use it as customer_named_date." + ask
        if r.get("no_exact_day") and date == r.get("no_exact_day"):
            return None
        return ("without an exact day from the customer, the brief allows only " + (r.get("no_exact_day") or "no date")
                + " for " + pid + ", not " + date + "." + ask)
    if heard and heard != named:
        return "customer_named_date " + named + " is not the day the customer said (" + heard + ")." + ask
    if not heard and spoken_required:
        return "no exact day from the customer has been heard yet, so " + named + " cannot be used." + ask
    want, why = expected_date(named, r)
    if want is None:
        return ("the customer named " + named + " and nothing can be recorded for that day for " + pid + " (" + why
                + "). Tell the customer, do not record " + date + ".")
    if date != want:
        return ("the customer named " + named + ", so the date for " + pid + " must be " + want + " (" + why + "), not "
                + date + ". Offer " + want + " to the customer, read it back, and record it only after a clear yes.")
    return None


def check(body, rules, p, named=None, witness=""):
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
    if named is not None:
        why = source_check(date, named, r, pid, witness, bool(p.get("spoken_required")))
        if why:
            return why
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
        _named = _iso(_b.pop(PARAMS.get("named_date_key"), None)) if PARAMS.get("named_date_key") else None
        _seen = _iso(vars.get(PARAMS.get("spoken_var"))) if PARAMS.get("spoken_var") else ""
        _pid = str(_b.get(PARAMS.get("product_key") or "product_id") or "")
        _why = check(_b, _rules(vars), PARAMS, _named, _seen)
        _max = int(PARAMS.get("max_attempts") or 0)
        try:
            _att = json.loads(vars.get("promise_attempts") or "{}") if not isinstance(vars.get("promise_attempts"), dict) else vars.get("promise_attempts")
        except Exception:
            _att = dict()
        if not _why and _max and int(_att.get(_pid, 0)) >= _max:
            _why = ("the bank was already asked " + str(_att.get(_pid)) + " time(s) for " + _pid
                    + ", the most allowed. Do not try again; follow your rule for a promise that could not be recorded.")
        if not _why and _max:
            _att[_pid] = int(_att.get(_pid, 0)) + 1
            vars["promise_attempts"] = json.dumps(_att)
        if _why:
            vars[_gv] = "NOT RECORDED, blocked before sending: " + _why
            _b[PARAMS.get("product_key") or "product_id"] = ""
except Exception:
    pass
