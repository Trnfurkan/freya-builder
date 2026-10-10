"""The collection policy a brief defines, as one structured object, and its checks.

    python -m builder.policy out/<slot>/date_params.json

The builder agent writes the policy from the brief into date_params.json (the same file the date facts code reads):
bands with their days-late range, deadline and number of persuasion attempts, the attempts' offers, the floor field
per product type and what to offer for a day that cannot be recorded. Nothing here knows any brief's numbers.

check() returns (errors, warnings). A missing or contradictory rule is an error, never a silent default: a build
with policy errors is not deliverable (the workflow could ask a customer for payment more often than the brief allows).
"""
import argparse
import json
import sys

AMOUNT_TOKENS = ("full", "floor", "full_or_floor")
DATE_TOKENS = ("today", "next_business_day", "end_of_week", "end_of_next_week", "deadline")
NON_BUSINESS = ("next", "previous", "")
AFTER_DEADLINE = ("deadline", "")


def _int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def check(params):
    errors, warnings = [], []
    bands = params.get("bands")
    if not isinstance(bands, list) or not bands:
        return ["bands: missing; the brief's bands (days late, deadline, attempts) are required"], warnings
    names = set()
    for i, b in enumerate(bands):
        where = f"bands[{i}]"
        if not isinstance(b, dict):
            errors.append(f"{where}: not an object")
            continue
        name = b.get("name")
        if not name:
            errors.append(f"{where}: name missing")
        elif name in names:
            errors.append(f"{where}: duplicate band name {name!r}")
        names.add(name)
        for key in ("min_days_late", "max_days_late", "deadline_business_days", "attempts"):
            if key not in b:
                errors.append(f"{where} ({name}): {key} missing")
            elif not _int(b[key]) or b[key] < 0:
                errors.append(f"{where} ({name}): {key} must be a whole number of 0 or more, got {b[key]!r}")
        if _int(b.get("min_days_late")) and _int(b.get("max_days_late")) and b["min_days_late"] > b["max_days_late"]:
            errors.append(f"{where} ({name}): min_days_late {b['min_days_late']} > max_days_late {b['max_days_late']}")
    ranges = sorted((b["min_days_late"], b["max_days_late"], b.get("name")) for b in bands
                    if isinstance(b, dict) and _int(b.get("min_days_late")) and _int(b.get("max_days_late")))
    for (lo1, hi1, n1), (lo2, hi2, n2) in zip(ranges, ranges[1:]):
        if lo2 <= hi1:
            errors.append(f"bands {n1!r} and {n2!r} overlap ({lo1}-{hi1} and {lo2}-{hi2}): a product would have two limits")
        elif lo2 > hi1 + 1:
            warnings.append(f"no band covers days late {hi1 + 1}-{lo2 - 1}: such a product gets no deadline and no offers")

    offers = params.get("attempt_offers")
    most = max((b.get("attempts") for b in bands if isinstance(b, dict) and _int(b.get("attempts"))), default=0)
    if most and offers is None:
        errors.append(f"attempt_offers missing although a band allows {most} persuasion attempt(s)")
    elif offers is not None:
        if not isinstance(offers, list):
            errors.append("attempt_offers must be a list")
            offers = []
        for i, o in enumerate(offers):
            if not isinstance(o, dict):
                errors.append(f"attempt_offers[{i}]: not an object")
                continue
            if o.get("amount") not in AMOUNT_TOKENS:
                errors.append(f"attempt_offers[{i}]: amount {o.get('amount')!r} is not one of {AMOUNT_TOKENS}")
            if o.get("date") not in DATE_TOKENS:
                errors.append(f"attempt_offers[{i}]: date {o.get('date')!r} is not one of {DATE_TOKENS}")
        if most > len(offers):
            errors.append(f"a band allows {most} persuasion attempts but attempt_offers defines only {len(offers)}")
        elif len(offers) > most:
            warnings.append(f"attempt_offers defines {len(offers)} offers but no band allows more than {most}")
        uses_floor = any(isinstance(o, dict) and o.get("amount") in ("floor", "full_or_floor") for o in offers)
        if uses_floor and not params.get("floor_field_by_type"):
            errors.append("attempt_offers use the floor but floor_field_by_type is missing")

    if params.get("non_business_day", "next") not in NON_BUSINESS:
        errors.append(f"non_business_day {params.get('non_business_day')!r} is not one of {NON_BUSINESS}")
    if params.get("after_deadline", "deadline") not in AFTER_DEADLINE:
        errors.append(f"after_deadline {params.get('after_deadline')!r} is not one of {AFTER_DEADLINE}")
    for h in params.get("holidays", []) or []:
        if not (isinstance(h, str) and len(h) == 10 and h[4] == "-" and h[7] == "-"):
            errors.append(f"holiday {h!r} is not YYYY-MM-DD")
    return errors, warnings


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("params")
    a = ap.parse_args(argv)
    with open(a.params, encoding="utf-8") as f:
        errors, warnings = check(json.load(f))
    for e in errors:
        print("ERROR", e)
    for w in warnings:
        print("WARN ", w)
    print("OK: policy complete" if not errors else f"{len(errors)} policy error(s): not deliverable")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
