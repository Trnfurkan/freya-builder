# post_process_code for a bank tool, or code for a `code` node: Turkish speech forms of amounts, dates and digits.
# The builder prepends one line `PARAMS = json.loads(...)`.
# It only READS the bank's answer (or vars) and WRITES new variables; the request and the answer are never changed,
# so the raw values sent to and received from the bank stay as they are.
#   amount         5000.13, "TL" -> "bes bin Turk Lirasi on uc kurus" (words; zero kurus is left out)
#   amount_digits  12000.0, "TL" -> "12.000 Turk Lirasi"; 85.2, "EUR" -> "85,20 Euro"
#   number         12 -> "on iki"
#   date           "05.10.2026" or "2026-10-05" -> "5 Ekim 2026"; the call's own day -> today_word ("bugun")
#   digits         "4253" -> "dort iki bes uc" (one by one, never grouped)
# A value that is missing or unreadable gives "" (nothing is made up).
# Rules for this file: only json, re and datetime; never raise; no double braces.
#
# PARAMS keys:
#   fields      list of {"var", "path", "kind", "currency"?}: path is dotted inside the tool answer (post hook: the
#               HTTP body, e.g. "data.debtTL.statementDebt") or a variable name (code node, source "vars")
#   source      "result" (post hook, default) or "vars" (code node)
#   now_var     variable with the call's date and time (for "today"), default "now"
#   today_word  default "bugun"
#   units       {currency code: [unit, subunit]}; defaults below, override with the brief's own words
import json
import re
import datetime

_ONES = ["", "bir", "iki", "\u00fc\u00e7", "d\u00f6rt", "be\u015f", "alt\u0131", "yedi", "sekiz", "dokuz"]
_TENS = ["", "on", "yirmi", "otuz", "k\u0131rk", "elli", "altm\u0131\u015f", "yetmi\u015f", "seksen", "doksan"]
_BIG = [(10 ** 12, "trilyon"), (10 ** 9, "milyar"), (10 ** 6, "milyon"), (1000, "bin")]
_MONTHS = ["Ocak", "\u015eubat", "Mart", "Nisan", "May\u0131s", "Haziran", "Temmuz", "A\u011fustos", "Eyl\u00fcl", "Ekim", "Kas\u0131m",
           "Aral\u0131k"]
_DIGITS = ["s\u0131f\u0131r", "bir", "iki", "\u00fc\u00e7", "d\u00f6rt", "be\u015f", "alt\u0131", "yedi", "sekiz", "dokuz"]
_UNITS = {"TL": ["T\u00fcrk Liras\u0131", "kuru\u015f"], "TRY": ["T\u00fcrk Liras\u0131", "kuru\u015f"], "USD": ["Dolar", "sent"],
          "EUR": ["Euro", "sent"], "GBP": ["\u0130ngiliz Sterlini", "peni"]}


def _under_thousand(n):
    words = []
    h, rest = divmod(n, 100)
    if h:
        words.append("y\u00fcz" if h == 1 else _ONES[h] + " y\u00fcz")
    t, o = divmod(rest, 10)
    if t:
        words.append(_TENS[t])
    if o:
        words.append(_ONES[o])
    return " ".join(words)


def words(n):
    """Turkish words for a whole number: 1000 -> "bin", 1100 -> "bin y\u00fcz", 2001 -> "iki bin bir"."""
    n = int(n)
    if n == 0:
        return "s\u0131f\u0131r"
    if n < 0:
        return "eksi " + words(-n)
    out = []
    for size, name in _BIG:
        q, n = divmod(n, size)
        if q:
            out.append(name if (q == 1 and size == 1000) else _under_thousand(q) + " " + name)
    if n:
        out.append(_under_thousand(n))
    return " ".join(out)


def _cents(value):
    v = float(value)
    sign = -1 if v < 0 else 1
    c = int(round(abs(v) * 100))
    return sign, c // 100, c % 100


def amount(value, currency, units=None):
    unit, sub = (units or _UNITS).get(str(currency).upper(), [str(currency), ""])
    sign, whole, frac = _cents(value)
    text = words(whole) + " " + unit
    if frac:
        text += " " + words(frac) + (" " + sub if sub else "")
    return ("eksi " if sign < 0 and (whole or frac) else "") + text


def amount_digits(value, currency, units=None):
    unit = (units or _UNITS).get(str(currency).upper(), [str(currency), ""])[0]
    sign, whole, frac = _cents(value)
    grouped = "{:,}".format(whole).replace(",", ".")
    text = grouped + ("," + str(frac).zfill(2) if frac else "")
    return ("-" if sign < 0 and (whole or frac) else "") + text + " " + unit


def _parse_date(value):
    s = str(value or "").strip()
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", s)
    if m:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def date(value, today=None, today_word="bug\u00fcn"):
    d = _parse_date(value)
    if d is None:
        return ""
    if today is not None and d == today:
        return today_word
    return str(d.day) + " " + _MONTHS[d.month - 1] + " " + str(d.year)


def digits(value):
    s = re.sub(r"\D", "", str(value or ""))
    return " ".join(_DIGITS[int(c)] for c in s)


def _get(obj, path):
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def speak(kind, value, currency="", today=None, p=None):
    p = p or dict()
    units = dict(_UNITS)
    units.update(p.get("units") or dict())
    if value is None or value == "":
        return ""
    try:
        if kind == "amount":
            return amount(value, currency, units)
        if kind == "amount_digits":
            return amount_digits(value, currency, units)
        if kind == "number":
            return words(int(float(value)))
        if kind == "date":
            return date(value, today, p.get("today_word") or "bug\u00fcn")
        if kind == "digits":
            return digits(value)
    except Exception:
        return ""
    return ""


def run(source, store, p):
    today = _parse_date(str(store.get(p.get("now_var") or "now") or "")[:10]) if isinstance(store, dict) else None
    for f in p.get("fields") or []:
        try:
            value = _get(source, f.get("path")) if p.get("source", "result") == "result" else source.get(f.get("path"))
            store[f["var"]] = speak(f.get("kind"), value, f.get("currency", ""), today, p)
        except Exception:
            pass


try:
    if PARAMS.get("source", "result") == "result":
        _body = result.get("data") if isinstance(result, dict) else None
        run(_body if isinstance(_body, dict) else dict(), vars, PARAMS)
    else:
        run(vars, vars, PARAMS)
except Exception:
    pass
