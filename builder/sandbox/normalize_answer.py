# pre_process_code for an identity-check api_call tool (docs/tools-and-code.md, "Pre and post Python hooks").
# The builder prepends one line `PARAMS = json.loads(...)`.
# It rewrites the model's answer in config["body"] before the request goes out:
#   date fields      -> YYYY-MM-DD ("14 March 1988", "the fourteenth of March nineteen eighty-eight", "14/03/1988")
#   numeric answers  -> digits only ("four four seven one", "44 71", "double four seven one")
# Anything it cannot read is left unchanged, so the service decides. It only rewrites the form of an answer, never
# picks a value the customer did not say: a wrong answer stays wrong and the service rejects it.
# A corrected answer ("12 July 1991, sorry, I mean 21 July 1992") counts by its last readable date: the latest exact
# answer decides.
# Turkish answers are read too (_turkish): month names, number words and correction words.
# Rules for this file: only json, re and datetime; never raise; no double braces.
#
# PARAMS keys:
#   field_key      body key that names the question, e.g. "field" (optional)
#   answer_key     body key that holds the answer, e.g. "answer"
#   date_fields    values of field_key whose answer is a full date
#   day_first      read an all-numeric date as day, month, year (true) or month, day, year (false)
#   numeric_fields values of field_key whose answer is digits only (optional; else any all-number answer)
#   all_dates      true: the answer is always a date, whatever field_key says (e.g. a promise's named day)
#   remember_field variable that keeps the field of the last question sent (for post_result.py passed_fields)
import json
import re
import datetime

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]
_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ORD = dict(first=1, second=2, third=3, fourth=4, fifth=5, sixth=6, seventh=7, eighth=8, ninth=9, tenth=10,
            eleventh=11, twelfth=12, thirteenth=13, fourteenth=14, fifteenth=15, sixteenth=16, seventeenth=17,
            eighteenth=18, nineteenth=19, twentieth=20, thirtieth=30)
_FILLER = ("the", "of", "and", "a", "it", "is", "its", "s", "my", "on", "born", "st", "nd", "rd", "th", "um", "uh")


def _month(tok):
    for i, m in enumerate(_MONTHS):
        if tok == m or tok == m[:3] or (tok == "sept" and i == 8):
            return i + 1
    return None


def _value(tok):
    """(number, digit_count, kind) for one token, or None. kind: digit, unit, teen, tens, ord."""
    if tok.isdigit():
        return int(tok), len(tok), "digit"
    m = re.match(r"^(\d+)(st|nd|rd|th)$", tok)
    if m:
        return int(m.group(1)), len(m.group(1)), "ord"
    if tok == "oh":
        return 0, 1, "unit"
    if tok in _UNITS:
        return _UNITS.index(tok), 1, "unit"
    if tok in _TEENS:
        return 10 + _TEENS.index(tok), 2, "teen"
    if tok in _TENS and tok:
        return 10 * _TENS.index(tok), 2, "tens"
    if tok in _ORD:
        return _ORD[tok], (1 if _ORD[tok] < 10 else 2), "ord"
    return None


def _tokens(text):
    t = text.lower().replace("-", " ").replace(",", " ")
    return re.findall(r"[a-z]+|\d+(?:st|nd|rd|th)?|[./]", t)


def _numbers(toks):
    """Group number tokens into numbers. Returns list of (value, digits, kind) and month positions."""
    out = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok in ("double", "triple") and i + 1 < len(toks):
            v = _value(toks[i + 1])
            if v and v[2] in ("unit", "digit") and v[1] == 1:
                n = 2 if tok == "double" else 3
                out.append((int(str(v[0]) * n), n, "digit"))
                i += 2
                continue
        v = _value(tok)
        if v is None:
            out.append((None, 0, tok))
            i += 1
            continue
        num, dig, kind = v
        # twenty two / twenty second
        if kind == "tens" and i + 1 < len(toks):
            w = _value(toks[i + 1])
            if w and w[2] in ("unit", "ord") and 1 <= w[0] <= 9:
                out.append((num + w[0], 2, "ord" if w[2] == "ord" else "two"))
                i += 2
                continue
        # two thousand (and) one / nineteen hundred
        if i + 1 < len(toks) and toks[i + 1] in ("thousand", "hundred"):
            mult = 1000 if toks[i + 1] == "thousand" else 100
            total = num * mult
            j = i + 2
            while j < len(toks) and toks[j] == "and":
                j += 1
            if j < len(toks):
                w = _value(toks[j])
                if w and w[2] in ("tens",) and j + 1 < len(toks):
                    w2 = _value(toks[j + 1])
                    if w2 and w2[2] == "unit" and w2[0] > 0:
                        out.append((total + w[0] + w2[0], 4, "year"))
                        i = j + 2
                        continue
                if w and w[2] in ("unit", "teen", "tens", "digit") and w[0] < 100:
                    out.append((total + w[0], 4, "year"))
                    i = j + 1
                    continue
            out.append((total, 4, "year"))
            i = j
            continue
        out.append((num, dig, kind if kind != "tens" else "two"))
        i += 1
    return out


def _spoken_digits(nums):
    """Concatenate a run of numbers into one digit string (phone digits, codes)."""
    s = ""
    for v, dig, kind in nums:
        if v is None:
            if kind in _FILLER or kind in (".", "/"):
                continue
            return None
        s += str(v).zfill(dig)
    return s or None


# Turkish answers ("14 Mart 1988", "on dort mart bin dokuz yuz seksen sekiz", "dort dort yedi bir") become the same
# answer in digits and English month names before the rules above read them. Only for text that looks Turkish: "on"
# is also an English word ("on 14 March").
_TR_FOLD = dict(zip("\u00e7\u011f\u0131\u00f6\u015f\u00fc\u00c7\u011e\u0130\u00d6\u015e\u00dc\u00e2\u00ee\u00fb", "cgiosucgiosuaiu"))
_TR_MONTHS = dict(ocak="january", subat="february", mart="march", nisan="april", mayis="may", haziran="june",
                  temmuz="july", agustos="august", eylul="september", ekim="october", kasim="november",
                  aralik="december")
_TR_UNITS = dict(sifir=0, bir=1, iki=2, uc=3, dort=4, dord=4, bes=5, alti=6, yedi=7, sekiz=8, dokuz=9)
_TR_TENS = dict(on=10, yirmi=20, otuz=30, kirk=40, elli=50, altmis=60, yetmis=70, seksen=80, doksan=90)
_TR_SUFFIX = ("inci", "nci", "uncu", "ncu", "inde", "unde", "unda", "inda", "nde", "nda", "sinde", "sinda",
              "sunda", "sunde", "incisi", "uncusu", "ncisi", "unu", "ini", "i", "u", "si", "su")
_TR_SIGNS = set(_TR_MONTHS) | (set(_TR_UNITS) - set(["bir"])) | (set(_TR_TENS) - set(["on"])) | set(["yuz", "bin"])
_TR_CORRECTION = re.compile(r"\b(?:pardon|yani|hayir|ozur dilerim|aslinda|duzeltiyorum|yanlis)\b")


def _tr_word(w):
    """(value, rank) of a Turkish number word, with an ordinal or case ending ("dordunde", "on besinci"), or None."""
    for table, rank in ((_TR_UNITS, 1), (_TR_TENS, 10)):
        if w in table:
            return table[w], rank
        for stem, v in table.items():
            if w.startswith(stem) and w[len(stem):] in _TR_SUFFIX:
                return v, rank
    if w == "yuz":
        return 100, 100
    if w == "bin":
        return 1000, 1000
    return None


def _turkish(text):
    folded = "".join(_TR_FOLD.get(ch, ch) for ch in str(text)).lower()
    words = re.findall(r"[a-z]+|\d+|[^a-z\d\s]", folded)
    if not (set(words) & _TR_SIGNS or folded != str(text).lower()):
        return text
    out, cur = [], None          # cur: [total, part, last rank]
    for w in words + [""]:
        v = _tr_word(w)
        if v is None:
            if cur:
                out.append(str(cur[0] + cur[1]))
                cur = None
            if w:
                out.append(_TR_MONTHS.get(w, w))
            continue
        val, rank = v
        if rank == 1000:
            if cur and cur[2] < 1000:
                cur = [cur[0] + (cur[1] or 1) * 1000, 0, 1000]
            else:
                if cur:
                    out.append(str(cur[0] + cur[1]))
                cur = [1000, 0, 1000]
        elif rank == 100:
            if cur and cur[2] == 1 and cur[1] < 10:
                cur = [cur[0], cur[1] * 100, 100]
            elif cur and cur[2] > 100:
                cur = [cur[0], cur[1] + 100, 100]
            else:
                if cur:
                    out.append(str(cur[0] + cur[1]))
                cur = [0, 100, 100]
        elif cur and cur[2] > rank:
            cur = [cur[0], cur[1] + val, rank]
        else:
            if cur:
                out.append(str(cur[0] + cur[1]))
            cur = [0, val, rank]
    text = " ".join(out)
    return _TR_CORRECTION.sub(" sorry ", text)


_CORRECTION = re.compile(r"\b(?:sorry|i mean|i meant|no wait|wait|actually|correction|rather|no,? it is|no,? it's)\b",
                         re.IGNORECASE)


def _date(text, day_first):
    """The customer's last readable date in the text: after a correction word, the part after the last one wins."""
    parts = [x for x in _CORRECTION.split(text) if x and x.strip(" ,.;-")]
    for part in reversed(parts):
        got = _one_date(part.strip(" ,.;-"), day_first)
        if got:
            return got
    return None


def _one_date(text, day_first):
    raw = text.strip()
    m = re.match(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$", raw)
    if m:
        return _ok(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^\s*(\d{1,2})\s*[./\- ]\s*(\d{1,2})\s*[./\- ]\s*(\d{2}|\d{4})\s*$", raw)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            return None
        return _ok(y, b, a) if day_first else _ok(y, a, b)
    toks = _tokens(raw)
    month, mpos = None, -1
    for i, t in enumerate(toks):
        mm = _month(t) if not t.isdigit() else None
        if mm:
            month, mpos = mm, i
            break
    if month is None:
        return None
    before = [x for x in _numbers(toks[:mpos]) if x[0] is not None]
    after = [x for x in _numbers(toks[mpos + 1:]) if x[0] is not None]
    day = None
    if before:
        day = before[-1][0]
    elif after:
        day = after[0][0]
        after = after[1:]
    year = _year(after)
    if day is None or year is None:
        return None
    return _ok(year, month, day)


def _year(nums):
    if not nums:
        return None
    if nums[0][1] == 4 or nums[0][2] == "year":
        return nums[0][0]
    # nineteen eighty eight -> (19, 88); one nine eight eight -> digits
    if len(nums) >= 2 and nums[0][1] == 2 and nums[1][1] == 2:
        return nums[0][0] * 100 + nums[1][0]
    if len(nums) >= 2 and nums[0][1] == 2 and nums[1][0] is not None and nums[1][1] == 1 and nums[1][0] == 0 and len(nums) >= 3:
        return nums[0][0] * 100 + nums[2][0]  # nineteen oh five
    s = "".join(str(v).zfill(d) for v, d, k in nums)
    if len(s) == 4 and s.isdigit():
        return int(s)
    return None


def _ok(y, mo, d):
    try:
        return datetime.date(int(y), int(mo), int(d)).isoformat()
    except Exception:
        return None


def normalize(field, answer, p):
    if not isinstance(answer, (str, int, float)):
        return answer
    text = _turkish(str(answer).strip())
    if not text:
        return answer
    if p.get("all_dates") or (field is not None and str(field) in (p.get("date_fields") or [])):
        return _date(text, bool(p.get("day_first", True))) or answer
    numeric = p.get("numeric_fields")
    if numeric is not None and str(field) not in numeric:
        return answer
    digits = _spoken_digits(_numbers(_tokens(text)))
    return digits if digits else answer


try:
    _b = config.get("body")
    if isinstance(_b, dict):
        _k = PARAMS.get("answer_key") or "answer"
        _f = _b.get(PARAMS.get("field_key")) if PARAMS.get("field_key") else None
        if _k in _b:
            _b[_k] = normalize(_f, _b[_k], PARAMS)
        if PARAMS.get("remember_field"):
            vars[PARAMS["remember_field"]] = str(_f or "")
except Exception:
    pass
