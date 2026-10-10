import json

from builder import snippets
from builder.facts import facts


def test_public_case_deadlines(params_a):
    # PUB-04: late band -> 23 October. PUB-07 / PUB-13: early band deadline 2 November (26 October is a holiday).
    v = facts(params_a, "2026-10-14T11:00")
    assert v["date_facts_state"] == "ok"
    assert v["deadline_late"] == "2026-10-23"
    assert v["deadline_early"] == "2026-11-02"
    assert v["today_iso"] == "2026-10-14"
    assert v["next_business_day_iso"] == "2026-10-15"
    assert v["last_business_day_this_week_iso"] == "2026-10-16"   # PUB-06 / PUB-10: "this Friday"
    assert v["last_business_day_next_week_iso"] == "2026-10-23"
    assert "Wednesday 2026-10-14, a business day" in v["date_facts"]
    assert "Monday 2026-10-26" in v["date_facts"]                  # listed as a holiday
    early_line = [l for l in v["date_facts"].splitlines() if "Allowed" in l][0]
    assert "2026-10-26" not in early_line and "Tuesday 2026-10-27" in early_line


def test_today_is_last_business_day_of_week(params_a):
    v = facts(params_a, "2026-10-16T10:00")
    assert v["last_business_day_this_week_iso"] == "2026-10-16"
    assert "(that is today)" in v["date_facts"]
    assert v["last_business_day_next_week_iso"] == "2026-10-23"


def test_weekend_now_is_reported(params_a):
    v = facts(params_a, "2026-10-17T12:00")
    assert "Saturday 2026-10-17, NOT a business day" in v["date_facts"]
    assert "(already past)" in v["date_facts"]
    assert v["next_business_day_iso"] == "2026-10-19"
    # Business days after today: 19,20,21,22,23, (26 holiday), 27, 28 -> the 7th is Wed 28
    assert v["deadline_late"] == "2026-10-28"


def test_holiday_on_friday_moves_last_business_day(params_a):
    v = facts(params_a, "2026-12-21T09:00")
    assert v["last_business_day_this_week_iso"] == "2026-12-24"


def test_products_per_product_deadline(params_a):
    # PUB-13: credit card 4 days late (early), personal loan 20 days late (late)
    products = [
        {"product_id": "p1", "type": "credit_card", "card_last4": "6604", "past_due": 300.0, "days_late": 4, "minimum_payment": 90.0},
        {"product_id": "p2", "type": "personal_loan", "past_due": 900.0, "days_late": 20, "instalment_amount": 300.0},
    ]
    v = facts(params_a, "2026-10-14T11:00", products)
    assert v["deadline_p1"] == "2026-11-02"
    assert v["deadline_p2"] == "2026-10-23"
    assert v["product_count"] == "2"
    assert v["product_facts"].startswith("Several products")
    lines = [l for l in v["product_facts"].splitlines() if l.startswith("Product ")]
    assert "product_id p1" in lines[0] and "Floor (minimum_payment) 90.00" in lines[0] and "persuasion attempts 1" in lines[0]
    assert "product_id p2" in lines[1] and "Floor (instalment_amount) 300.00" in lines[1] and "persuasion attempts 2" in lines[1]
    assert "card_last4 6604" in lines[0]


def test_products_in_other_shapes(params_a):
    prods = [{"product_id": "p1", "type": "car_loan", "past_due": 75.0, "days_late": 3, "instalment_amount": 25.0}]
    code = snippets.date_facts_code(params_a)
    as_json = snippets.run_code_node(code, {"now": "2026-10-14T11:00", "products": json.dumps(prods)})
    assert as_json["deadline_p1"] == "2026-11-02"
    as_repr = snippets.run_code_node(code, {"now": "2026-10-14T11:00", "products": str(prods)})
    assert as_repr["deadline_p1"] == "2026-11-02"
    flat = {"now": "2026-10-14T11:00"}
    for k, val in prods[0].items():
        flat[f"get_account_response.data.products.0.{k}"] = val
    as_flat = snippets.run_code_node(code, flat)
    assert as_flat["deadline_p1"] == "2026-11-02"


def test_no_products_and_bad_now_never_raise(params_a):
    v = facts(params_a, "2026-10-14T11:00")
    assert v["product_facts"] == "No product data yet." and v["product_count"] == "0"
    bad = facts(params_a, "not a date")
    assert bad["date_facts_state"] == "error"
    assert "could not be computed" in bad["date_facts"]


def test_other_calendar_rules():
    # A different brief: Friday/Saturday weekend, three bands, its own holiday.
    p = {"now_var": "call_time", "weekend": [4, 5], "holidays": ["2026-10-15"],
         "bands": [{"name": "a", "min_days_late": 1, "max_days_late": 10, "deadline_business_days": 3, "attempts": 1},
                   {"name": "b", "min_days_late": 11, "max_days_late": 30, "deadline_business_days": 2, "attempts": 2},
                   {"name": "c", "min_days_late": 31, "max_days_late": 90, "deadline_business_days": 1, "attempts": 3}]}
    v = facts(p, "2026-10-14T09:30")   # Wednesday; Thu 15 holiday, Fri/Sat weekend
    assert v["next_business_day_iso"] == "2026-10-18"
    assert v["deadline_a"] == "2026-10-20"
    assert v["deadline_c"] == "2026-10-18"
    assert v["last_business_day_this_week_iso"] == "2026-10-18"   # Sunday is a business day here


def test_code_has_no_double_braces(params_a):
    code = snippets.date_facts_code({**params_a, "nested": {"a": {"b": 1}}})
    assert "{{" not in code and "}}" not in code


def test_action_table_answers_named_days_per_band(params_a):
    # PUB-07: a holiday before the early-band deadline must get the next business day, not the deadline.
    v = facts(params_a, "2026-10-14T11:00")
    early, late = v["date_actions_early"], v["date_actions_late"]
    assert "Monday 2026-10-26 (holiday) -> offer Tuesday 2026-10-27" in early
    assert "Saturday 2026-10-17 (weekend) -> offer Monday 2026-10-19" in early
    assert early.endswith("any day after Monday 2026-11-02 -> say the latest date you can record is Monday 2026-11-02 and offer it")
    assert "2026-10-26" not in late.split("any day after")[0]          # after the late deadline: covered by the tail
    assert "any day after Friday 2026-10-23" in late
    assert "look it up here and say exactly what it says" in v["date_facts"]


def test_action_table_follows_the_brief_choices(params_a):
    prev = facts({**params_a, "non_business_day": "previous", "after_deadline": ""}, "2026-10-14T11:00")["date_actions_early"]
    assert "Monday 2026-10-26 (holiday) -> offer Friday 2026-10-23" in prev
    assert "Saturday 2026-10-17 (weekend) -> offer Friday 2026-10-16" in prev
    assert prev.endswith("any day after Monday 2026-11-02 -> cannot be recorded")
    none = facts({**params_a, "non_business_day": ""}, "2026-10-14T11:00")["date_actions_early"]
    assert "Monday 2026-10-26 (holiday) -> cannot be recorded" in none


def test_previous_business_day_never_before_today(params_a):
    # Saturday right after a Friday "today": the previous business day is today, which is allowed.
    v = facts({**params_a, "non_business_day": "previous"}, "2026-10-16T11:00")["date_actions_early"]
    assert "Saturday 2026-10-17 (weekend) -> offer Friday 2026-10-16" in v
    # Today is a Saturday: the previous business day is in the past, so the next one is offered.
    w = facts({**params_a, "non_business_day": "previous"}, "2026-10-17T11:00")["date_actions_early"]
    assert "Saturday 2026-10-17 (weekend) -> offer Monday 2026-10-19" in w


def test_product_line_points_to_its_band_table(params_a):
    prods = [{"product_id": "p1", "type": "personal_loan", "past_due": 540.0, "days_late": 5, "instalment_amount": 180.0}]
    v = facts(params_a, "2026-10-14T11:00", prods)
    assert "use band early's table" in v["product_facts"]


OFFERS = [{"amount": "full_or_floor", "date": "end_of_week"}, {"amount": "floor", "date": "deadline"}]
PUB12 = [{"product_id": "p1", "type": "credit_card", "card_last4": "9015", "past_due": 510.0, "days_late": 16, "minimum_payment": 153.0},
         {"product_id": "p2", "type": "personal_loan", "past_due": 1320.0, "days_late": 16, "instalment_amount": 440.0},
         {"product_id": "p3", "type": "car_loan", "past_due": 140.0, "days_late": 7, "instalment_amount": 46.0}]


def test_offer_plan_per_product_follows_its_band(params_a):
    v = facts({**params_a, "attempt_offers": OFFERS}, "2026-10-14T11:00", PUB12)
    p2, p3 = v["offer_plan_p2"], v["offer_plan_p3"]
    assert p2.startswith("p2 persuasion plan (2 attempts in all)")
    assert "attempt 1 = propose 1320.00 (or the floor 440.00 if" in p2 and "on Friday 2026-10-16" in p2
    assert "attempt 2 = propose 440.00 on Friday 2026-10-23" in p2
    assert p3.startswith("p3 persuasion plan (1 attempt in all)") and "attempt 2" not in p3
    assert "After attempt 1 is refused, make no more offers for p3." in p3
    lines = v["product_facts"].splitlines()
    assert lines[0].startswith("Several products")
    assert lines.index("  " + p2) == lines.index([l for l in lines if "product_id p2" in l][0]) + 1


def test_end_of_week_moves_to_next_week_and_is_capped(params_a):
    one = [{"product_id": "p1", "type": "car_loan", "past_due": 100.0, "days_late": 3, "instalment_amount": 30.0}]
    friday = facts({**params_a, "attempt_offers": OFFERS}, "2026-10-16T11:00", one)["offer_plan_p1"]
    assert "on Friday 2026-10-23" in friday                       # today is this week's last business day
    tight = {**params_a, "attempt_offers": OFFERS,
             "bands": [{"name": "x", "min_days_late": 1, "max_days_late": 99, "deadline_business_days": 1, "attempts": 1}]}
    capped = facts(tight, "2026-10-14T11:00", one)["offer_plan_p1"]
    assert "on Thursday 2026-10-15" in capped                     # end of week is after the deadline: the deadline


def test_no_plan_without_offers(params_a):
    v = facts(params_a, "2026-10-14T11:00", PUB12[:1])
    assert "offer_plan_p1" not in v and "persuasion plan" not in v["product_facts"]
    assert not v["product_facts"].startswith("Several products")


PUB13 = [{"product_id": "p1", "type": "credit_card", "card_last4": "6604", "past_due": 300.0, "days_late": 4, "minimum_payment": 90.0},
         {"product_id": "p2", "type": "personal_loan", "past_due": 900.0, "days_late": 20, "instalment_amount": 300.0}]


def test_date_lookup_for_several_products(params_a):
    # PUB-13: one date for both; the card keeps it, the loan moves to its own deadline.
    v = facts(params_a, "2026-10-14T11:00", PUB13)
    rows = {r.split(" -> ")[0]: r.split(" -> ")[1] for r in v["date_lookup"].splitlines()}
    assert rows["Friday 2026-10-30"] == ("p1: record Friday 2026-10-30; p2: after its deadline, record Friday 2026-10-23 "
                                         "(its latest date) and say so")
    assert rows["Monday 2026-10-26"].startswith("p1: holiday, offer Tuesday 2026-10-27; p2: after its deadline")
    assert rows["Wednesday 2026-10-21"] == "p1: record Wednesday 2026-10-21; p2: record Wednesday 2026-10-21"
    assert rows["any later day"].startswith("p1: after its deadline, record Monday 2026-11-02")
    assert "find that date's line" in v["product_facts"] and "Friday 2026-10-30 -> p1: record" in v["product_facts"]


def test_date_lookup_follows_the_brief_choices(params_a):
    v = facts({**params_a, "non_business_day": "previous", "after_deadline": ""}, "2026-10-14T11:00", PUB13)
    rows = {r.split(" -> ")[0]: r.split(" -> ")[1] for r in v["date_lookup"].splitlines()}
    assert rows["Monday 2026-10-26"] == "p1: holiday, offer Friday 2026-10-23; p2: after its deadline, cannot be recorded"


def test_no_date_lookup_for_one_product(params_a):
    v = facts(params_a, "2026-10-14T11:00", PUB13[:1])
    assert "date_lookup" not in v and "Date lookup" not in v["product_facts"]
