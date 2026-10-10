import json

from builder import snippets
from builder.facts import facts

GUARD = snippets.promise_guard_code({"product_key": "product_id", "date_key": "date", "amount_key": "amount",
                                     "check_dates": True, "check_amount": True, "guard_var": "promise_guard"})

PRODUCTS = [  # PUB-13 shape: credit card early band, personal loan late band
    {"product_id": "p1", "type": "credit_card", "card_last4": "6604", "past_due": 300.0, "days_late": 4, "minimum_payment": 90.0},
    {"product_id": "p2", "type": "personal_loan", "past_due": 900.0, "days_late": 20, "instalment_amount": 300.0},
]


def account_vars(params_a):
    return facts(params_a, "2026-10-14T11:00", PRODUCTS)


def send(v, **body):
    out = snippets.run_pre_hook(GUARD, {"customer_id": "c1", **body}, v)
    return out, v.get("promise_guard")


def test_promise_rules_written_by_date_facts(params_a):
    rules = json.loads(account_vars(params_a)["promise_rules"])
    assert set(rules) == {"p1", "p2"}
    assert rules["p1"]["deadline"] == "2026-11-02" and rules["p2"]["deadline"] == "2026-10-23"
    assert rules["p1"]["floor"] == 90.0 and rules["p1"]["full"] == 300.0
    assert "2026-10-26" not in rules["p1"]["dates"]                    # holiday
    assert rules["p2"]["dates"][0] == "2026-10-14" and rules["p2"]["dates"][-1] == "2026-10-23"


def test_valid_promises_go_out_unchanged(params_a):
    v = account_vars(params_a)
    for body in ({"product_id": "p1", "amount": 300.0, "date": "2026-10-30"},       # PUB-13 card
                 {"product_id": "p2", "amount": 900.0, "date": "2026-10-23"},       # PUB-13 loan on its deadline
                 {"product_id": "p1", "amount": 90.0, "date": "2026-10-14"},        # the floor, today
                 {"product_id": "p2", "amount": "450.5", "date": "2026-10-16"}):    # between floor and full, as text
        out, why = send(v, **body)
        assert out["product_id"] == body["product_id"] and not why, (body, why)


def test_holiday_weekend_and_late_dates_are_blocked(params_a):
    v = account_vars(params_a)
    out, why = send(v, product_id="p1", amount=300.0, date="2026-10-26")
    assert out["product_id"] == "" and "2026-10-26" in why and "next allowed date after it is 2026-10-27" in why
    out, why = send(v, product_id="p1", amount=300.0, date="2026-10-17")
    assert out["product_id"] == "" and "2026-10-19" in why
    out, why = send(v, product_id="p2", amount=900.0, date="2026-10-30")
    assert out["product_id"] == "" and "latest allowed date is 2026-10-23" in why
    out, why = send(v, product_id="p2", amount=900.0, date="2026-10-13")
    assert out["product_id"] == "" and "earliest allowed date is 2026-10-14" in why


def test_amounts_outside_floor_and_full_are_blocked(params_a):
    v = account_vars(params_a)
    out, why = send(v, product_id="p1", amount=89.99, date="2026-10-16")
    assert out["product_id"] == "" and "below the floor 90.00" in why
    out, why = send(v, product_id="p1", amount=300.01, date="2026-10-16")
    assert out["product_id"] == "" and "above the full past-due amount 300.00" in why
    out, why = send(v, product_id="p1", amount="a lot", date="2026-10-16")
    assert out["product_id"] == "" and "not a number" in why


def test_unknown_product_is_blocked(params_a):
    out, why = send(account_vars(params_a), product_id="p9", amount=10, date="2026-10-16")
    assert out["product_id"] == "" and "p1, p2" in why


def test_checks_follow_the_brief(params_a):
    v = account_vars(params_a)
    dates_only = snippets.promise_guard_code({"check_dates": True, "check_amount": False})
    out = snippets.run_pre_hook(dates_only, {"product_id": "p1", "amount": 1.0, "date": "2026-10-16"}, v)
    assert out["product_id"] == "p1"


def test_without_rules_nothing_is_blocked():
    for v in ({}, {"promise_rules": "{}"}, {"promise_rules": "not json"}):
        out, why = send(dict(v), product_id="p1", amount=1, date="1999-01-01")
        assert out["product_id"] == "p1" and not why


def test_a_previous_reason_is_cleared(params_a):
    v = account_vars(params_a)
    send(v, product_id="p1", amount=1, date="2026-10-16")
    assert v["promise_guard"]
    send(v, product_id="p1", amount=300.0, date="2026-10-16")
    assert v["promise_guard"] == ""


def test_non_business_days_listed_with_next_business_day(params_a):
    text = account_vars(params_a)["date_facts"]
    line = [l for l in text.splitlines() if l.startswith("Not business days")][0]
    assert "Monday 2026-10-26 -> Tuesday 2026-10-27" in line
    assert "Saturday 2026-10-17 -> Monday 2026-10-19" in line


def test_no_double_braces():
    assert "{{" not in GUARD and "}}" not in GUARD
