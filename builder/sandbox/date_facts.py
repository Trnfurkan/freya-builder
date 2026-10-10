# Runs inside a Freya code node (see docs/tools-and-code.md, "Code nodes").
# The builder prepends one line `PARAMS = json.loads(...)` built from the brief.
# Rules for this file: only json, re and datetime; never raise; no double braces;
# read and write workflow variables only through `vars`.
#
# PARAMS keys:
#   now_var                 call variable that holds the current date and time, e.g. "2026-10-14T11:00"
#   weekend                 weekday numbers that are never business days (Monday=0 ... Sunday=6)
#   holidays                ISO dates that are not business days
#   bands                   list of {name, min_days_late, max_days_late, deadline_business_days, attempts}
#   products_var            variable that holds the account's product list (optional)
#   product_fields          {id, type, days_late, amount} field names in one product (optional)
#   floor_field_by_type     {product type: field holding that type's floor} (optional)
#   list_days               how many business days to list per band (optional, default 25)
#   non_business_day        what the brief says to offer for a named day that is not a business day, on or before the
#                           deadline: "next" (next business day), "previous" (previous business day) or "" (no advice)
#   after_deadline          what the brief says to offer for a named day after the deadline: "deadline" or "" (no advice)
#   attempt_offers          the brief's persuasion attempts in order, each {amount, date}; a product gets as many as its
#                           band's `attempts`. amount: "full", "floor" or "full_or_floor" (full, or the floor when the
#                           customer already said the full amount is too much). date: "today", "next_business_day",
#                           "end_of_week" (last business day of this week, or of next week when that is today or past),
#                           "end_of_next_week" or "deadline". Dates later than the product's deadline become the deadline.
#
# Writes: date_facts, product_facts (text for prompts), today_iso, next_business_day_iso,
#   last_business_day_this_week_iso, last_business_day_next_week_iso, deadline_<band>, deadline_<product_id>,
#   product_count, and promise_rules: a JSON string mapping each product_id to its dates, deadline, floor and full amount, that
#   builder/sandbox/promise_guard.py reads to check a promise before it is sent.
import json
import re
import datetime

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _d(x):
    return datetime.date.fromisoformat(str(x)[:10])


def _label(d):
    return _DAY_NAMES[d.weekday()] + " " + d.isoformat()


def _is_business(d, p):
    return d.weekday() not in p.get("weekend", [5, 6]) and d.isoformat() not in p.get("holidays", [])


def _next_business(d, p):
    d = d + datetime.timedelta(days=1)
    while not _is_business(d, p):
        d = d + datetime.timedelta(days=1)
    return d


def _nth_business_after(d, n, p):
    for _ in range(int(n)):
        d = _next_business(d, p)
    return d


def _last_business_of_week(d, p):
    monday = d - datetime.timedelta(days=d.weekday())
    for k in range(6, -1, -1):
        x = monday + datetime.timedelta(days=k)
        if _is_business(x, p):
            return x
    return None


def _prev_business(d, p):
    d = d - datetime.timedelta(days=1)
    while not _is_business(d, p):
        d = d - datetime.timedelta(days=1)
    return d


def _actions(today, deadline, p):
    """The days a customer may name that cannot be recorded for a band, each with what to offer instead.
    Practice agents compared dates wrongly (a holiday before the deadline treated as after it), so the answer is
    precomputed and the agent only looks it up."""
    move = p.get("non_business_day", "next")
    after = p.get("after_deadline", "deadline")
    items, d = [], today
    while d <= deadline:
        if not _is_business(d, p):
            kind = "holiday" if d.isoformat() in p.get("holidays", []) else "weekend"
            target = None
            if move == "next":
                target = _next_business(d, p)
            elif move == "previous":
                target = _prev_business(d, p)
                if target < today:
                    target = _next_business(d, p)
            if target is not None and target <= deadline:
                items.append(_label(d) + " (" + kind + ") -> offer " + _label(target))
            else:
                items.append(_label(d) + " (" + kind + ") -> cannot be recorded")
        d = d + datetime.timedelta(days=1)
    tail = "any day after " + _label(deadline) + (" -> say the latest date you can record is " + _label(deadline)
                                                 + " and offer it" if after == "deadline" else " -> cannot be recorded")
    head = "a day before today -> cannot be recorded"
    return [head] + items + [tail]


def _offer_date(token, today, deadline, p):
    if token == "today":
        d = today if _is_business(today, p) else _next_business(today, p)
    elif token == "next_business_day":
        d = _next_business(today, p)
    elif token == "end_of_week":
        d = _last_business_of_week(today, p)
        if d is None or d <= today:
            d = _last_business_of_week(today + datetime.timedelta(days=7), p)
    elif token == "end_of_next_week":
        d = _last_business_of_week(today + datetime.timedelta(days=7), p)
    else:
        d = deadline
    return min(d, deadline) if d is not None else deadline


def _offer_amount(token, full, floor):
    if token == "floor" and floor is not None:
        return _money(floor)
    if token == "full_or_floor" and floor is not None:
        return _money(full) + " (or the floor " + _money(floor) + " if the customer already said the full amount is too much)"
    return _money(full)


def _verdict(day, today, deadline, p):
    """What to record for one product when the customer names `day`, by the brief's choices."""
    if day < today:
        return "before today, cannot be recorded"
    if day > deadline:
        if p.get("after_deadline", "deadline") == "deadline":
            return "after its deadline, record " + _label(deadline) + " (its latest date) and say so"
        return "after its deadline, cannot be recorded"
    if _is_business(day, p):
        return "record " + _label(day)
    kind = "holiday" if day.isoformat() in p.get("holidays", []) else "weekend"
    move = p.get("non_business_day", "next")
    target = None
    if move == "next":
        target = _next_business(day, p)
    elif move == "previous":
        target = _prev_business(day, p)
        if target < today:
            target = _next_business(day, p)
    if target is not None and target <= deadline:
        return kind + ", offer " + _label(target)
    return kind + ", cannot be recorded"


def _lookup(today, items, p):
    """Day by day, what each product gets when the customer names that day. Practice agents compared dates wrongly
    with several products (an allowed date moved onto a later deadline), so they look the day up instead."""
    last = max(dl for _, dl in items)
    rows, d = [], today
    while d <= last + datetime.timedelta(days=7):
        rows.append(_label(d) + " -> " + "; ".join(pid + ": " + _verdict(d, today, dl, p) for pid, dl in items))
        d = d + datetime.timedelta(days=1)
    rows.append("any later day -> " + "; ".join(pid + ": " + _verdict(last + datetime.timedelta(days=30), today, dl, p)
                                                 for pid, dl in items))
    return rows


def _plan(pid, attempts, offers, today, deadline, full, floor, p):
    """The persuasion attempts for one product, worked out in full. Practice agents counted attempts wrongly per
    product (two offers where the band allowed one), so the agent reads the plan instead of counting."""
    n = int(attempts or 0)
    if not offers or n <= 0:
        return ""
    steps = []
    for i, o in enumerate(offers[:n]):
        d = _offer_date(o.get("date", "deadline"), today, deadline, p)
        steps.append("attempt " + str(i + 1) + " = propose " + _offer_amount(o.get("amount", "full"), full, floor)
                     + " on " + _label(d))
    return (pid + " persuasion plan (" + str(n) + " attempt" + ("s" if n != 1 else "") + " in all): "
            + "; ".join(steps) + ". After attempt " + str(min(n, len(offers))) + " is refused, make no more offers for "
            + pid + ".")


def _plan_steps(attempts, offers, today, deadline, full, floor, p):
    """The same plan as data, for the offer gate (builder/sandbox/offer_gate.py): one entry per allowed attempt."""
    n = int(attempts or 0)
    out = []
    for o in (offers or [])[:max(n, 0)]:
        d = _offer_date(o.get("date", "deadline"), today, deadline, p)
        token = o.get("amount", "full")
        amount = floor if token == "floor" and floor is not None else full
        alt = floor if token == "full_or_floor" and floor is not None else None
        out.append(dict(amount=_money(amount), alt_amount=_money(alt) if alt is not None else "", date=d.isoformat(),
                        date_label=_label(d)))
    return out


def _band(days_late, p):
    for b in p.get("bands", []):
        if int(b["min_days_late"]) <= int(days_late) <= int(b["max_days_late"]):
            return b
    return None


def _business_days_until(today, end, p, limit):
    out, d = [], today
    if _is_business(d, p):
        out.append(d)
    while len(out) < limit:
        d = _next_business(d, p)
        if d > end:
            break
        out.append(d)
    return out


def _load_products(vars, p):
    name = p.get("products_var") or "products"
    raw = vars.get(name)
    if raw is None:
        for k in vars:
            if str(k).endswith(".data." + name) or str(k).endswith("." + name):
                raw = vars[k]
                break
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            try:
                fixed = raw.replace("'", '"').replace("True", "true").replace("False", "false").replace("None", "null")
                raw = json.loads(fixed)
            except Exception:
                raw = None
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    # Flattened keys such as get_account_response.data.products.0.days_late
    found = {}
    pat = re.compile(r"(?:^|\.)" + re.escape(name) + r"\.(\d+)\.(\w+)$")
    for k, v in vars.items():
        m = pat.search(str(k))
        if m:
            found.setdefault(int(m.group(1)), dict())[m.group(2)] = v
    return [found[i] for i in sorted(found)]


def _num(x):
    try:
        return float(x)
    except Exception:
        return None


def _money(x):
    try:
        return "%.2f" % float(x)
    except Exception:
        return str(x)


def _main(vars, p):
    now = str(vars.get(p.get("now_var") or "now") or "")
    today = _d(now)
    time_now = now[11:16] if len(now) >= 16 else ""
    lines = []
    lines.append("Today is " + _label(today) + (", a business day." if _is_business(today, p) else ", NOT a business day.")
                 + (" Time now " + time_now + "." if time_now else ""))
    nb = _next_business(today, p)
    lines.append("Next business day: " + _label(nb) + ".")
    this_last = _last_business_of_week(today, p)
    next_last = _last_business_of_week(today + datetime.timedelta(days=7), p)
    if this_last is not None:
        lines.append("Last business day of this week: " + _label(this_last)
                     + (" (already past)." if this_last < today else (" (that is today)." if this_last == today else ".")))
    if next_last is not None:
        lines.append("Last business day of next week: " + _label(next_last) + ".")
    monday = today - datetime.timedelta(days=today.weekday())
    lines.append("This week (Monday to Sunday): " + ", ".join(_label(monday + datetime.timedelta(days=k)) for k in range(7)) + ".")
    lines.append("Next week (Monday to Sunday): " + ", ".join(_label(monday + datetime.timedelta(days=7 + k)) for k in range(7)) + ".")
    hol = [h for h in p.get("holidays", [])]
    if hol:
        lines.append("Bank holidays (never business days): " + ", ".join(_label(_d(h)) for h in hol) + ".")
    vars["today_iso"] = today.isoformat()
    vars["next_business_day_iso"] = nb.isoformat()
    vars["last_business_day_this_week_iso"] = this_last.isoformat() if this_last else ""
    vars["last_business_day_next_week_iso"] = next_last.isoformat() if next_last else ""
    limit = int(p.get("list_days", 25))
    latest = today
    for b in p.get("bands", []):
        latest = max(latest, _nth_business_after(today, b["deadline_business_days"], p))
    moves, d = [], today
    while d <= latest:
        if not _is_business(d, p):
            moves.append(_label(d) + " -> " + _label(_next_business(d, p)))
        d = d + datetime.timedelta(days=1)
    if moves:
        lines.append("Not business days, with the next business day after each: " + "; ".join(moves) + ".")
    for b in p.get("bands", []):
        dl = _nth_business_after(today, b["deadline_business_days"], p)
        vars["deadline_" + str(b["name"])] = dl.isoformat()
        days = _business_days_until(today, dl, p, limit)
        lines.append("Band " + str(b["name"]) + " (days late " + str(b["min_days_late"]) + " to " + str(b["max_days_late"])
                     + "): deadline " + _label(dl) + " (business day number " + str(b["deadline_business_days"])
                     + " after today); persuasion attempts " + str(b.get("attempts", "")) + ".")
        lines.append("  Allowed promise dates for this band: " + ", ".join(_label(x) for x in days) + ".")
        acts = _actions(today, dl, p)
        lines.append("  If the customer names a day that is not allowed for this band, look it up here and say exactly "
                     "what it says; never work it out yourself: " + "; ".join(acts) + ".")
        vars["date_actions_" + str(b["name"])] = "; ".join(acts)
    vars["date_facts"] = "\n".join(lines)

    products = _load_products(vars, p)
    f = p.get("product_fields") or dict()
    floors = p.get("floor_field_by_type") or dict()
    plines = []
    plan_lines = []
    lookup_items = []
    plans = dict()
    order = []
    labels = dict()
    rules = dict()
    for i, prod in enumerate(products):
        pid = str(prod.get(f.get("id", "product_id"), "p" + str(i + 1)))
        ptype = str(prod.get(f.get("type", "type"), ""))
        late = prod.get(f.get("days_late", "days_late"))
        amount = prod.get(f.get("amount", "past_due"))
        floor_field = floors.get(ptype, "")
        floor = prod.get(floor_field) if floor_field else None
        b = _band(late, p) if late is not None else None
        text = "Product " + str(i + 1) + ": product_id " + pid + ", type " + ptype
        extra = [k + " " + str(v) for k, v in prod.items() if k not in (f.get("id", "product_id"), f.get("type", "type"))]
        if extra:
            text += " (" + ", ".join(extra) + ")"
        if amount is not None:
            text += ". Full amount " + _money(amount)
        if floor is not None:
            text += ". Floor (" + floor_field + ") " + _money(floor)
        if b is not None:
            dl = _nth_business_after(today, b["deadline_business_days"], p)
            text += (". Band " + str(b["name"]) + ", deadline " + _label(dl) + ", persuasion attempts " + str(b.get("attempts", ""))
                     + ". For a day the customer names, use band " + str(b["name"]) + "'s table")
            vars["deadline_" + pid] = dl.isoformat()
            lookup_items.append((pid, dl))
            rules[pid] = dict(dates=[x.isoformat() for x in _business_days_until(today, dl, p, 400)], deadline=dl.isoformat(),
                              floor=_num(floor), full=_num(amount))
            plan = _plan(pid, b.get("attempts"), p.get("attempt_offers"), today, dl, amount, floor, p)
            plans[pid] = _plan_steps(b.get("attempts"), p.get("attempt_offers"), today, dl, amount, floor, p)
            order.append(pid)
            labels[pid] = dict((k, str(v)) for k, v in prod.items())
            if plan:
                vars["offer_plan_" + pid] = plan
                plan_lines.append("  " + plan)
        elif late is not None:
            text += ". days_late " + str(late) + " matches no band"
        plines.append(text + ".")
        plines.extend(plan_lines)
        plan_lines = []
    vars["product_count"] = str(len(products))
    vars["promise_rules"] = json.dumps(rules)
    vars["offer_plans"] = json.dumps(dict(order=order, plans=plans, products=labels))
    if len(lookup_items) > 1:
        rows = _lookup(today, lookup_items, p)
        vars["date_lookup"] = "\n".join(rows)
        plines.append("Date lookup for several products. When the customer names a date, find that date's line and do "
                      "exactly what it says for each product; never compare dates yourself:")
        plines.extend("  " + r for r in rows)
    if len(products) > 1:
        plines.insert(0, "Several products: when the customer names one date for several of them, check that date for each "
                         "product on its own, with that product's band (allowed dates, deadline, table). A product whose "
                         "band allows the date keeps exactly that date.")
    vars["product_facts"] = "\n".join(plines) if plines else "No product data yet."
    vars["date_facts_state"] = "ok"


try:
    _main(vars, PARAMS)
except Exception as _e:
    vars["date_facts"] = "Date facts could not be computed: " + str(_e)
    vars["product_facts"] = vars.get("product_facts") or "No product data yet."
    vars["date_facts_state"] = "error"
