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
    vars["date_facts"] = "\n".join(lines)

    products = _load_products(vars, p)
    f = p.get("product_fields") or dict()
    floors = p.get("floor_field_by_type") or dict()
    plines = []
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
            text += ". Band " + str(b["name"]) + ", deadline " + _label(dl) + ", persuasion attempts " + str(b.get("attempts", ""))
            vars["deadline_" + pid] = dl.isoformat()
            rules[pid] = dict(dates=[x.isoformat() for x in _business_days_until(today, dl, p, 400)], deadline=dl.isoformat(),
                              floor=_num(floor), full=_num(amount))
        elif late is not None:
            text += ". days_late " + str(late) + " matches no band"
        plines.append(text + ".")
    vars["product_count"] = str(len(products))
    vars["promise_rules"] = json.dumps(rules)
    vars["product_facts"] = "\n".join(plines) if plines else "No product data yet."
    vars["date_facts_state"] = "ok"


try:
    _main(vars, PARAMS)
except Exception as _e:
    vars["date_facts"] = "Date facts could not be computed: " + str(_e)
    vars["product_facts"] = vars.get("product_facts") or "No product data yet."
    vars["date_facts_state"] = "error"
