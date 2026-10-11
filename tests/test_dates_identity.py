"""Promise dates decided by code, and identity answers normalised without weakening the check.

Expected values come from the briefs (read here) and the public practice customers, not from earlier agent behaviour."""
import datetime
import json
import re

import pytest

from builder import blocks as b
from builder import openapi_tools as ot
from builder import snippets
from builder.facts import facts
from builder.validate import validate
from tests.conftest import KIT
from tests.test_offer_gate import CASES, POLICY, account, policy_from_brief

OPENAPI = json.loads((KIT / "tools" / "openapi.json").read_text(encoding="utf-8"))
NAMED = "customer_named_date"
GUARD = snippets.promise_guard_code({"check_dates": True, "check_amount": True, "named_date_key": NAMED, "max_attempts": 2})
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December"]


def call_vars(case_id, policy=POLICY):
    v = facts(policy, CASES[case_id]["variables"]["now"], account(case_id))
    assert v["date_facts_state"] == "ok"
    return v


def send(v, pid, amount, date, named):
    """The body the bank would receive, and the guard's reason ('' when it went out)."""
    body = {"product_id": pid, "amount": amount, "date": date, NAMED: named}
    out = snippets.run_pre_hook(GUARD, body, v)
    return out, v.get("promise_guard", "")


def business_day_n(today, n, holidays):
    """Independent count of the brief's 'the n-th business day after today' (Monday to Friday, not a holiday)."""
    d, k = today, 0
    while k < n:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in holidays:
            k += 1
    return d.isoformat()


# --- Problem A: the day the customer names decides the date, in code ----------------------------------------------

def test_pub07_holiday_26_october_records_27_october_and_never_the_deadline():
    case = CASES["PUB-07"]
    want = case["expected"]["promises"][0]["date"]                        # 2026-10-27 in the practice customer
    v = call_vars("PUB-07")
    wrong, why = send(v, "p1", 540.0, v["deadline_p1"], "2026-10-26")       # what fix4-a recorded
    assert wrong["product_id"] == "" and want in why                        # blocked, and told the right day
    assert "promise_attempts" not in v or json.loads(v["promise_attempts"]).get("p1", 0) == 0
    right, why = send(v, "p1", 540.0, want, "2026-10-26")
    assert right["product_id"] == "p1" and right["date"] == want and why == ""


def test_the_helper_field_never_reaches_the_bank_and_the_normalised_date_is_recorded():
    out, why = send(call_vars("PUB-07"), "p1", 540.0, "2026-10-27", "Monday 26 October 2026")   # spoken form
    assert out == {"product_id": "p1", "amount": 540.0, "date": "2026-10-27"} and why == ""   # nothing extra
    for named in ("", "agent_offer", "garbage"):                                                 # blocked too
        out, _ = send(call_vars("PUB-07"), "p1", 540.0, "2026-10-27", named)
        assert NAMED not in out


def test_a_day_after_the_deadline_gets_the_deadline_as_brief_rule_23_says():
    today = datetime.date.fromisoformat(CASES["PUB-07"]["variables"]["now"][:10])
    deadline = business_day_n(today, 12, POLICY["holidays"])               # early band: "the 12th business day"
    v = call_vars("PUB-07")
    assert v["deadline_p1"] == deadline == "2026-11-02"
    for later in ("2026-11-03", "2026-11-05", "2026-12-01"):
        assert send(call_vars("PUB-07"), "p1", 540.0, deadline, later)[0]["product_id"] == "p1"
        assert send(call_vars("PUB-07"), "p1", 540.0, "2026-10-30", later)[0]["product_id"] == ""


def test_a_day_before_today_cannot_be_recorded():
    out, why = send(call_vars("PUB-07"), "p1", 540.0, "2026-10-14", "2026-10-13")
    assert out["product_id"] == "" and "before today" in why


def test_offered_day_and_recorded_day_are_the_same_for_every_named_day():
    """The table the agent reads (date_actions_<band>) and the guard agree on every day up to the deadline: the day
    offered to the customer is the only day the guard lets through."""
    v = call_vars("PUB-07")
    table = v["date_actions_early"].split("; ")
    offers = {}
    for entry in table:
        m = re.match(r"^\w+ (\d{4}-\d{2}-\d{2}) \((?:holiday|weekend)\) -> offer \w+ (\d{4}-\d{2}-\d{2})$", entry)
        if m:
            offers[m.group(1)] = m.group(2)
    assert offers["2026-10-26"] == "2026-10-27" and len(offers) >= 6
    rules = json.loads(v["promise_rules"])["p1"]
    for named, offered in offers.items():
        assert send(call_vars("PUB-07"), "p1", 540.0, offered, named)[0]["product_id"] == "p1"
        for other in rules["dates"]:
            if other != offered:
                assert send(call_vars("PUB-07"), "p1", 540.0, other, named)[0]["product_id"] == "", (named, other)
    for allowed in rules["dates"]:                                         # a business day is recorded as named
        assert send(call_vars("PUB-07"), "p1", 540.0, allowed, allowed)[0]["product_id"] == "p1"


def test_holidays_next_to_a_weekend_move_to_the_first_business_day_after_all_of_them():
    policy = dict(POLICY, holidays=["2026-10-23", "2026-10-26"])           # Friday and Monday around a weekend
    v = call_vars("PUB-07", policy)
    for named in ("2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26"):
        assert send(call_vars("PUB-07", policy), "p1", 540.0, "2026-10-27", named)[0]["product_id"] == "p1"
        assert send(call_vars("PUB-07", policy), "p1", 540.0, "2026-10-22", named)[0]["product_id"] == ""
    assert "Saturday 2026-10-24 (weekend) -> offer Tuesday 2026-10-27" in v["date_actions_early"]


def test_previous_business_day_policy_never_goes_before_today():
    policy = dict(POLICY, non_business_day="previous")
    v = call_vars("PUB-07", policy)                                         # today is Wednesday 2026-10-14
    assert send(v, "p1", 540.0, "2026-10-16", "2026-10-17")[0]["product_id"] == "p1"   # Saturday -> Friday
    assert send(call_vars("PUB-07", policy), "p1", 540.0, "2026-10-23", "2026-10-26")[0]["product_id"] == "p1"


def test_no_valid_alternative_means_nothing_is_recorded():
    none_moved = dict(POLICY, non_business_day="")
    out, why = send(call_vars("PUB-07", none_moved), "p1", 540.0, "2026-10-27", "2026-10-26")
    assert out["product_id"] == "" and "nothing can be recorded" in why
    none_after = dict(POLICY, after_deadline="")
    out, why = send(call_vars("PUB-07", none_after), "p1", 540.0, "2026-11-02", "2026-11-09")
    assert out["product_id"] == "" and "after the deadline" in why


def test_a_persuasion_offer_is_recorded_as_an_agent_offer():
    """Brief A rule 30: attempt 1 proposes the last business day of this week; the customer accepts it."""
    v = call_vars("PUB-07")
    plan = json.loads(v["offer_plans"])["plans"]["p1"][0]["date"]
    assert plan == "2026-10-16"                                             # Friday of the week of 2026-10-14
    assert send(v, "p1", 540.0, plan, "agent_offer")[0]["product_id"] == "p1"


def test_an_agent_offer_is_only_one_of_the_brief_offers():
    """The brief's own proposals for PUB-07 (early band): attempt 1 on Friday 2026-10-16 (rule 30), today (rule 28)
    and the deadline for a customer with no exact day (rule 21). Nothing else."""
    today = CASES["PUB-07"]["variables"]["now"][:10]
    for token in ("agent_offer", "no_exact_day"):
        for date in ("2026-10-27", "2026-10-20", "2026-10-30"):
            out, why = send(call_vars("PUB-07"), "p1", 540.0, date, token)
            assert out["product_id"] == "" and "is not a date the brief has you propose" in why
        for date in (today, "2026-10-16", "2026-11-02"):
            assert send(call_vars("PUB-07"), "p1", 540.0, date, token)[0]["product_id"] == "p1"


def test_pub06_this_friday_labelled_no_exact_day_is_still_recorded():
    """fix10-a v2: the customer said 'this Friday', the model wrote no_exact_day, and the guard refused the right date.
    Friday 2026-10-16 is PUB-06's expected date and attempt 1 of its plan, a date the brief has the agent propose."""
    want = CASES["PUB-06"]["expected"]["promises"][0]
    out, why = send(call_vars("PUB-06"), "p1", want["amount"], want["date"], "no_exact_day")
    assert out["product_id"] == "p1" and why == ""
    out, why = send(call_vars("PUB-06"), "p1", want["amount"], want["date"], want["date"])      # the right way
    assert out["product_id"] == "p1" and why == ""


def test_a_late_band_persuasion_offer_on_the_deadline_is_allowed():
    """Brief A rule 30, attempt 2 (late band only): the floor on the deadline."""
    v = call_vars("PUB-12")
    late = next(p for p in account("PUB-12") if p["days_late"] >= 8)
    pid = late["product_id"]
    floor = json.loads(v["promise_rules"])[pid]["floor"]                  # brief rule 25: the floor per product type
    assert send(v, pid, floor, v["deadline_" + pid], "agent_offer")[0]["product_id"] == pid


# --- where the date comes from: required, readable, consistent ------------------------------------------------

def test_missing_helper_field_sends_nothing():
    v = call_vars("PUB-07")
    out = snippets.run_pre_hook(GUARD, {"product_id": "p1", "amount": 540.0, "date": "2026-10-27"}, v)
    assert out["product_id"] == "" and "customer_named_date is empty" in v["promise_guard"]
    assert "promise_attempts" not in v                                      # not counted as a bank call


@pytest.mark.parametrize("named", ["", "   ", "{{customer_named_date}}", "next Monday-ish", "sometime", "2026/13/45x"])
def test_empty_placeholder_or_corrupt_helper_field_sends_nothing(named):
    out, why = send(call_vars("PUB-07"), "p1", 540.0, "2026-10-27", named)
    assert out["product_id"] == "" and "call the tool again now" in why and "propose no other date" in why


def test_a_field_problem_is_resent_without_talking_to_the_customer():
    """The note the model gets after a blocked request: fix the field and resend; never offer another date for it."""
    hook = snippets.post_result_code(on_success="Recorded.", on_failure="Not recorded.", guard_var="promise_guard")
    v = call_vars("PUB-07")
    send(v, "p1", 540.0, "2026-10-27", "")
    note = snippets.run_post_hook(hook, {"status": "success", "status_code": 400, "data": {"ok": False}}, v)
    text = note["data"]["what_to_do_next"]
    assert "call the tool again now" in text and "Tell the customer what can be recorded" not in text
    assert "[resend]" not in text and v["promise_guard"] == ""
    v = call_vars("PUB-07")
    send(v, "p1", 540.0, "2026-11-02", "2026-10-26")                         # a wrong date: the customer must hear 27
    text = snippets.run_post_hook(hook, {"status": "success", "status_code": 400, "data": {"ok": False}}, v)["data"]
    assert "must be 2026-10-27" in text["what_to_do_next"]
    assert "Tell the customer what can be recorded" in text["what_to_do_next"]


def test_spoken_day_and_recorded_day_must_agree():
    out, why = send(call_vars("PUB-07"), "p1", 540.0, "2026-10-28", "2026-10-26")
    assert out["product_id"] == "" and "must be 2026-10-27" in why


def test_a_corrected_day_counts_by_the_last_one():
    said = "the twenty-sixth of October 2026, sorry, I mean 30 October 2026"
    assert send(call_vars("PUB-07"), "p1", 540.0, "2026-10-30", said)[0]["product_id"] == "p1"
    assert send(call_vars("PUB-07"), "p1", 540.0, "2026-10-27", said)[0]["product_id"] == ""


def test_no_exact_day_gets_the_deadline_from_rule_21_and_never_an_unplanned_day():
    v = call_vars("PUB-07")
    assert send(v, "p1", 540.0, v["deadline_p1"], "no_exact_day")[0]["product_id"] == "p1"
    out, why = send(call_vars("PUB-07"), "p1", 540.0, "2026-10-23", "no_exact_day")
    assert out["product_id"] == "" and v["deadline_p1"] in why


# --- the extraction witness: a day the customer never said cannot be invented -----------------------------------

WITNESSED = snippets.promise_guard_code({"check_dates": True, "check_amount": True, "named_date_key": NAMED,
                                         "spoken_var": "customer_spoken_date"})
STRICT = snippets.promise_guard_code({"check_dates": True, "check_amount": True, "named_date_key": NAMED,
                                      "spoken_var": "customer_spoken_date", "spoken_required": True})


def witnessed(heard, date, named, code=WITNESSED):
    v = call_vars("PUB-07")
    if heard is not None:
        v["customer_spoken_date"] = heard
    out = snippets.run_pre_hook(code, {"product_id": "p1", "amount": 540.0, "date": date, NAMED: named}, v)
    return out["product_id"] == "p1", v.get("promise_guard", "")


def test_the_witness_blocks_a_day_the_customer_did_not_say():
    assert witnessed("2026-10-26", "2026-10-27", "2026-10-26")[0]                 # PUB-07 done right
    ok, why = witnessed("2026-10-26", "2026-11-02", "2026-11-05")                 # invented a later day to get the deadline
    assert not ok and "not the day the customer said (2026-10-26)" in why


def test_the_witness_blocks_tokens_that_hide_a_spoken_day():
    """The fix4-a failure, done with a token instead: the customer said the 26th, the agent claims no exact day."""
    ok, why = witnessed("2026-10-26", "2026-11-02", "no_exact_day")
    assert not ok and "the customer said 2026-10-26" in why
    assert not witnessed("2026-10-26", "2026-11-02", "agent_offer")[0]
    assert not witnessed("2026-10-26", "2026-10-14", "agent_offer")[0]            # 'today' is only before a day is said
    assert witnessed("2026-10-26", "2026-10-16", "agent_offer")[0]                # the persuasion plan still works


def test_strict_witness_needs_a_heard_day():
    assert not witnessed(None, "2026-10-27", "2026-10-26", STRICT)[0]
    assert witnessed("2026-10-26", "2026-10-27", "2026-10-26", STRICT)[0]
    assert witnessed(None, "2026-10-27", "2026-10-26", WITNESSED)[0]               # default: no witness, no veto


def test_spoken_date_extraction_block():
    e = b.spoken_date()
    assert e["name"] == "customer_spoken_date" and e["validation_pattern"] == r"^\d{4}-\d{2}-\d{2}$"
    assert "min_confidence" in e


def test_pub13_one_named_date_for_two_products_with_different_deadlines():
    """Brief B rule 6: a product keeps the named date if its band allows it, else its own deadline."""
    policy = policy_from_brief("briefs/brief-a.md")                         # Brief B uses Brief A's dates and bands
    want = {p["product_id"]: p["date"] for p in CASES["PUB-13"]["expected"]["promises"]}
    v = call_vars("PUB-13", policy)
    named = "2026-10-30"                                                    # PUB-13's script: "both on 30 October"
    for pid, date in want.items():
        assert send(v, pid, account("PUB-13")[int(pid[1:]) - 1]["past_due"], date, named)[0]["product_id"] == pid
    assert send(call_vars("PUB-13", policy), "p1", 300.0, "2026-11-02", named)[0]["product_id"] == ""


@pytest.mark.parametrize("said", ["Monday 26 October 2026", "26/10/2026", "the twenty-sixth of October 2026"])
def test_the_named_day_goes_through_the_shared_date_normaliser(said):
    out, why = send(call_vars("PUB-07"), "p1", 540.0, "2026-10-27", said)
    assert out["product_id"] == "p1" and why == ""
    out, _ = send(call_vars("PUB-07"), "p1", 540.0, "2026-11-02", said)
    assert out["product_id"] == ""


def test_an_unreadable_named_day_is_sent_back_not_guessed():
    out, why = send(call_vars("PUB-07"), "p1", 540.0, "2026-10-27", "Monday the 26th")
    assert out["product_id"] == "" and "YYYY-MM-DD" in why


def test_promise_tool_with_the_helper_field_validates_and_needs_the_hook():
    cid = {"customer_id": "{{customer_id}}"}
    text = "The last exact day the customer named for this product, as YYYY-MM-DD; empty if none."
    tool = ot.api_tool(OPENAPI, "create_promise", baked=cid, pre_hook=GUARD, helper_fields={NAMED: text})
    assert tool["function"]["parameters"]["properties"]["body"]["properties"][NAMED]["default"] == "{{" + NAMED + "}}"
    with pytest.raises(ValueError):
        ot.api_tool(OPENAPI, "create_promise", baked=cid, helper_fields={NAMED: text})
    close = ot.api_tool(OPENAPI, "close_call", baked=cid, pre_hook=b.close_pre_hook(), post_hook=b.close_post_hook(snippets))
    wf = b.workflow("talk", [b.conversation("talk", "Talk.", tools=[tool, close], transitions=[b.closed_transition("end")],
                                            max_turns=30, max_turn_target="wrap_up"),
                             b.wrap_up_node("wrap_up", close, "end"), b.end_node("end")])
    assert not [m for lvl, _, m in validate(wf, OPENAPI).items if lvl == "ERROR" and NAMED in m]
    tool["pre_process_code"] = snippets.promise_guard_code({"check_dates": True})
    assert [m for lvl, _, m in validate(wf, OPENAPI).items if lvl == "ERROR" and NAMED in m]


# --- Problem B: identity answers --------------------------------------------------------------------------------

DOB = snippets.normalize_answer_code({"field_key": "field", "answer_key": "answer", "date_fields": ["date_of_birth"],
                                     "day_first": True})   # brief A rule 11: numbers only -> day, month, year


def dob(said):
    return snippets.run_pre_hook(DOB, {"field": "date_of_birth", "answer": said})["answer"]


def spoken(iso):
    d = datetime.date.fromisoformat(iso)
    return [f"{d.day} {MONTHS[d.month - 1]} {d.year}", f"{d.day:02d}/{d.month:02d}/{d.year}",
            f"{MONTHS[d.month - 1]} {d.day}, {d.year}", f"the {d.day} of {MONTHS[d.month - 1].lower()} {d.year}"]


def test_every_public_customer_date_of_birth_reads_back_as_the_test_data():
    for case in CASES.values():
        iso = case["identity"]["date_of_birth"]
        for said in spoken(iso):
            assert dob(said) == iso, (case["id"], said)


def test_dates_said_in_words_from_real_calls():
    assert dob("14 March 1988.") == CASES["PUB-01"]["identity"]["date_of_birth"]                # fix2-a PUB-01
    assert dob("Fifth of December nineteen seventy-five.") == CASES["PUB-07"]["identity"]["date_of_birth"]


def test_wrong_answers_stay_wrong():
    """PUB-03's script gives three wrong dates; none may come out as the date on file."""
    case = CASES["PUB-03"]
    said = re.findall(r"\d{1,2} [A-Z][a-z]+ \d{4}", case["script"])
    assert len(said) == 3
    for x in said:
        assert dob(x) != case["identity"]["date_of_birth"]


def test_a_correction_counts_by_the_last_date():
    on_file = CASES["PUB-03"]["identity"]["date_of_birth"]                                     # 1992-07-21
    assert dob("12 July 1991, sorry, I mean 21 July 1992") == on_file
    assert dob("21 July 1992, no wait, 21 July 1990") == "1990-07-21"                          # corrected to a wrong one
    assert dob("the fifth of December, no wait, the sixth of December 1975") == "1975-12-06"


def test_unreadable_answers_are_left_for_the_service():
    for said in ("I'd rather not say", "March", "the fourteenth"):
        assert dob(said) == said


def test_a_wrong_answer_is_never_a_pass_and_the_account_waits_for_all_verified():
    hook = snippets.post_result_code(on_success="Next question.", on_failure="Ask again.")
    for left in (2, 1, 0):
        data = {"ok": True, "verified": False, "wrong_answers_left": left, "all_verified": False}
        note = snippets.run_post_hook(hook, {"status": "success", "status_code": 200, "data": data})["data"]["what_to_do_next"]
        assert note.startswith("Result: verified is FALSE, all_verified is FALSE.")
    t = b.go("load", "identity passed", b.is_true("verify_identity_response.data.all_verified"))
    assert {c["variable"] for c in t["condition"]["conditions"]} == {"verify_identity_response.data.all_verified"}


# --- The text-message sentence: only after a recorded promise -------------------------------------------------

SMS = re.search(r'^38\. After a recorded promise, say this sentence: "([^"]+)"', (KIT / "briefs" / "brief-a.md")
                .read_text(encoding="utf-8"), re.M).group(1)


def test_sms_sentence_is_read_from_the_brief():
    assert SMS == "You will receive a text message confirming this promise."


def test_sms_sentence_only_when_the_promise_is_recorded():
    hook = snippets.post_result_code(on_success="Say the promise is recorded, then say exactly: '" + SMS + "'",
                                     on_failure="Nothing was recorded. Do not say it is recorded.",
                                     guard_var="promise_guard", fail_count_var="promise_failures")

    def note(result, v=None):
        return snippets.run_post_hook(hook, result, v if v is not None else {})["data"]["what_to_do_next"]

    assert SMS in note({"status": "success", "status_code": 200, "data": {"ok": True, "promise_id": "pr1"}})
    assert SMS not in note({"status": "error", "status_code": 500, "data": {"ok": False}})
    assert SMS not in note({"status": "success", "status_code": 200, "data": {"ok": False}})
    blocked = {"promise_guard": "NOT RECORDED, blocked before sending: the customer named 2026-10-26."}
    assert SMS not in note({"status": "success", "status_code": 400, "data": {"ok": False}}, blocked)
