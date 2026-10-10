"""Payment requests through one gate: limits from the brief, independent counters, no reset, no bypass."""
import copy
import json
import re

import pytest

from builder import blocks as b
from builder import openapi_tools as ot
from builder import policy, snippets
from builder.facts import facts
from builder.validate import validate
from tests.conftest import KIT

ORDINAL = re.compile(r"the (\d+)(?:st|nd|rd|th) business day")


def policy_from_brief(path):
    """Read the band table and the holidays from the brief text itself, so the limits in these tests are the brief's,
    not numbers typed into the test. (The builder agent does the same reading when it writes date_params.json.)"""
    text = (KIT / path).read_text(encoding="utf-8")
    bands = []
    for row in re.findall(r"^\|\s*(\d+) to (\d+)\s*\|\s*(\w+)\s*\|([^|]+)\|\s*(\d+)\s*\|\s*$", text, re.M):
        lo, hi, name, deadline, attempts = row
        bands.append({"name": name, "min_days_late": int(lo), "max_days_late": int(hi),
                      "deadline_business_days": int(ORDINAL.search(deadline).group(1)), "attempts": int(attempts)})
    holidays = re.findall(r"\d{4}-\d{2}-\d{2}", text.split("except Freya Bank holidays:")[1].split("\n")[0])
    return {"now_var": "now", "weekend": [5, 6], "holidays": holidays, "bands": bands,
            "products_var": "products",
            "product_fields": {"id": "product_id", "type": "type", "days_late": "days_late", "amount": "past_due"},
            "floor_field_by_type": {"credit_card": "minimum_payment", "personal_loan": "instalment_amount", "car_loan": "instalment_amount"},
            # Brief A rule 30, in the policy's tokens: attempt 1 full (or floor) at the end of the week, attempt 2 floor on the deadline.
            "attempt_offers": [{"amount": "full_or_floor", "date": "end_of_week"}, {"amount": "floor", "date": "deadline"}],
            "non_business_day": "next", "after_deadline": "deadline"}


POLICY = policy_from_brief("briefs/brief-a.md")
CASES = {c["id"]: c for c in json.loads((KIT / "public-test-set.json").read_text(encoding="utf-8"))}


def account(case_id):
    """The case's products as get_account returns them (ids p1, p2, ... in order)."""
    return [dict(product_id=f"p{i + 1}", **p) for i, p in enumerate(CASES[case_id]["products"])]


GATE = snippets.offer_gate_code({"line_template": "For your {product}, could you pay {amount} on {date}?",
                                 "product_labels": {"credit_card": "credit card ending {card_last4}"}})


def call_vars(case_id):
    v = facts(POLICY, CASES[case_id]["variables"]["now"], account(case_id))
    assert v["date_facts_state"] == "ok"
    return v


def gate(v, reply=None):
    """Enter the gate, as the workflow does, optionally with the extracted answer to the last request."""
    if reply is not None:
        v["offer_reply"] = reply
    exec(GATE, {"vars": v})
    return v["offer_state"], v.get("offer_product", ""), v.get("offer_line", "")


def ledger(v):
    return json.loads(v["offer_ledger"])


def band_limit(case_id, pid):
    days = account(case_id)[int(pid[1:]) - 1]["days_late"]
    return next(x["attempts"] for x in POLICY["bands"] if x["min_days_late"] <= days <= x["max_days_late"])


# --- the limits come from the brief --------------------------------------------------------------------------

def test_limits_are_read_from_the_brief():
    by_name = {x["name"]: x for x in POLICY["bands"]}
    assert set(by_name) == {"early", "late"}
    assert (by_name["early"]["min_days_late"], by_name["early"]["max_days_late"], by_name["early"]["attempts"]) == (1, 7, 1)
    assert (by_name["late"]["min_days_late"], by_name["late"]["max_days_late"], by_name["late"]["attempts"]) == (8, 45, 2)
    assert "2026-10-26" in POLICY["holidays"]
    assert policy.check(POLICY) == ([], [])


# --- each product up to its own limit, never more --------------------------------------------------------------

def test_each_product_gets_requests_up_to_its_band_limit():
    v = call_vars("PUB-12")          # card 16 days (late), personal loan 16 days (late), car loan 7 days (early)
    made = []
    state, pid, line = gate(v)
    while state == "offer":
        made.append((pid, line))
        state, pid, line = gate(v, "refused")
    per = {p: sum(1 for x, _ in made if x == p) for p in ("p1", "p2", "p3")}
    assert per == {p: band_limit("PUB-12", p) for p in per} == {"p1": 2, "p2": 2, "p3": 1}
    assert made[0] == ("p1", "For your credit card ending 9015, could you pay 510.00 on Friday 2026-10-16?")
    assert made[1] == ("p1", "For your credit card ending 9015, could you pay 153.00 on Friday 2026-10-23?")
    assert made[4] == ("p3", "For your car loan, could you pay 140.00 on Friday 2026-10-16?")
    assert "used for: p1, p2, p3" in v["offer_summary"]


def test_one_request_more_is_blocked_whatever_the_path():
    v = call_vars("PUB-12")
    while gate(v, "refused")[0] == "offer":
        pass
    before = ledger(v)["counts"]
    # entering the gate again: from another node, after a topic change, with a stale or any answer
    for reply in (None, "refused", "other", "accepted", "", "REFUSED"):
        assert gate(v, reply) == ("done", "", "")
    assert ledger(v)["counts"] == before
    assert v["offer_line"] == ""


def test_counters_of_two_products_are_independent():
    v = call_vars("PUB-13")          # card 4 days (early: 1), personal loan 20 days (late: 2)
    assert gate(v)[1] == "p1"
    state, pid, _ = gate(v, "accepted")          # the card is agreed at its first request
    assert (state, pid) == ("offer", "p2")
    assert gate(v, "refused")[1] == "p2"
    assert gate(v, "refused")[0] == "done"
    assert ledger(v)["counts"] == {"p1": 1, "p2": 2}
    assert ledger(v)["state"]["p1"] == "agreed"


def test_topic_change_refusal_or_going_back_never_resets():
    v = call_vars("PUB-12")
    gate(v)                                      # p1, request 1
    v["offer_reply"] = "other"                   # customer talks about something else: the request node routes back
    v["offer_state"] = "whatever another node wrote"   # to the working node, not into the gate
    state, pid, line = gate(v)                   # later the gate is entered again
    assert (pid, ledger(v)["counts"]["p1"]) == ("p1", 2)
    assert "153.00" in line                      # request 2 of the plan, not request 1 again
    counts = []
    for reply in ("refused", "other", None, "refused", "other", "refused", "refused"):
        gate(v, reply)
        counts.append(sum(ledger(v)["counts"].values()))
    assert counts == sorted(counts)              # the total never goes down
    assert max(counts) == 5


def test_a_stale_answer_never_decides_the_next_request():
    v = call_vars("PUB-12")
    gate(v)
    gate(v, "refused")
    assert v["offer_reply"] == ""                # cleared by the gate once read


def test_too_much_uses_the_floor_where_the_brief_allows():
    v = call_vars("PUB-06")                      # one card, late band
    v["full_too_much"] = "yes"
    assert "637.50" in gate(v)[2]


# --- tool failures and the bank's retries -------------------------------------------------------------------------

PROMISE_HOOK = snippets.chain(
    snippets.promise_guard_code({"check_dates": True, "check_amount": True, "max_attempts": 2}),
    snippets.mark_product_code({"state": "promise_requested"}))


def test_retries_do_not_repeat_a_request_and_are_limited():
    v = call_vars("PUB-10")
    assert gate(v)[1] == "p1"
    counts = ledger(v)["counts"]
    body = {"customer_id": "c", "product_id": "p1", "amount": 365.0, "date": "2026-10-16"}
    first = snippets.run_pre_hook(PROMISE_HOOK, body, v)          # fails at the bank (HTTP 500)
    second = snippets.run_pre_hook(PROMISE_HOOK, body, v)         # the one retry the brief allows
    third = snippets.run_pre_hook(PROMISE_HOOK, body, v)          # one more: blocked before the bank
    assert first["product_id"] == second["product_id"] == "p1"
    assert third["product_id"] == "" and "already asked 2 time(s)" in v["promise_guard"]
    assert ledger(v)["counts"] == counts                          # retries are not requests to the customer
    assert ledger(v)["state"]["p1"] == "promise_requested"
    assert gate(v)[0] == "done"                                   # a product with a promise gets no more requests


def test_a_blocked_promise_does_not_settle_the_product():
    v = call_vars("PUB-07")
    gate(v)
    snippets.run_pre_hook(PROMISE_HOOK, {"product_id": "p1", "amount": 540.0, "date": "2026-10-26"}, v)   # a holiday
    assert ledger(v)["state"].get("p1", "open") == "open"


# --- missing or contradictory policy: an error, and nothing asked at run time --------------------------------------

@pytest.mark.parametrize("change, expect", [
    (lambda p: p["bands"][0].pop("attempts"), "attempts missing"),
    (lambda p: p["bands"][1].update(min_days_late=5), "overlap"),
    (lambda p: p.update(attempt_offers=p["attempt_offers"][:1]), "allows 2 persuasion attempts but attempt_offers defines only 1"),
    (lambda p: p["attempt_offers"][0].update(date="whenever"), "date 'whenever'"),
    (lambda p: p.pop("attempt_offers"), "attempt_offers missing"),
    (lambda p: p.pop("floor_field_by_type"), "floor_field_by_type is missing"),
    (lambda p: p["bands"][0].update(attempts=-1), "0 or more"),
    (lambda p: p.update(bands=[]), "bands: missing"),
])
def test_missing_or_contradictory_policy_is_an_error(change, expect):
    p = copy.deepcopy(POLICY)
    change(p)
    errors, _ = policy.check(p)
    assert any(expect in e for e in errors), errors


def test_a_band_without_attempts_asks_for_nothing_at_run_time():
    p = copy.deepcopy(POLICY)
    for band in p["bands"]:
        band.pop("attempts")
    v = facts(p, "2026-10-14T11:00", account("PUB-12"))
    assert gate(v)[0] == "done"
    assert gate({"offer_plans": "not json"})[0] == "done"
    assert gate({})[0] == "done"


# --- the workflow cannot reach a request without the gate ----------------------------------------------------------

@pytest.fixture
def gated(openapi):
    cid = {"customer_id": "{{customer_id}}"}
    close = ot.api_tool(openapi, "close_call", baked=cid, post_hook=b.close_post_hook(snippets))
    promise = ot.api_tool(openapi, "create_promise", baked=cid, pre_hook=PROMISE_HOOK)
    note = ot.api_tool(openapi, "add_note", baked=cid, pre_hook=snippets.mark_product_code({"state": "noted"}))
    work = b.conversation("negotiate", "{{product_facts}}", tools=[promise, note, close],
                          transitions=[b.closed_transition("end"), b.to_gate("offer_gate")],
                          max_turns=60, max_turn_target="wrap_up")
    nodes = [b.code_node("facts", snippets.date_facts_code(POLICY), "negotiate"), work,
             *b.offer_loop("offer_gate", "offer_request", "negotiate", GATE),
             b.wrap_up_node("wrap_up", close, "end"), b.end_node("end")]
    robot = b.global_node("robot", {"operator": "matches_intent", "description": "asks if it is a robot"},
                          "Say what you are.", on_enter=b.say("I am a digital assistant."),
                          excluded=["end", "facts", "offer_gate", "offer_request"])
    return b.workflow("facts", nodes, global_nodes=[robot])


def gate_errors(wf, openapi):
    return [m for lvl, _, m in validate(wf, openapi, ["customer_id", "now"]).items if lvl == "ERROR"]


def test_gated_workflow_validates(gated, openapi):
    assert gate_errors(gated, openapi) == []
    req = gated["nodes"]["offer_request"]
    assert req["on_enter"] == {"type": "tts", "text": "{{offer_line}}"}
    assert [t["target_node"] for t in req["transitions"]] == ["negotiate", "offer_gate", "negotiate"]


@pytest.mark.parametrize("break_it, expect", [
    (lambda w: w["nodes"]["negotiate"]["transitions"].append(b.go("offer_request", "shortcut", b.is_set("x"))),
     "without passing the offer gate"),
    (lambda w: w["nodes"]["wrap_up"].update(max_turn_transition="offer_request"), "without passing the offer gate"),
    (lambda w: w["nodes"]["negotiate"].update(task_message="Say {{offer_line}} again."), "outside the request node"),
    (lambda w: w["global_nodes"]["robot"].update(excluded_nodes=["end"]), "re-enter ['offer_request']"),
    (lambda w: w["nodes"]["facts"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"].update(
        default="vars['offer_ledger'] = ''"), "outside the offer gate"),
    (lambda w: w.update(initial_node="offer_request"), "starts in a request node"),
])
def test_paths_around_the_gate_are_errors(gated, openapi, break_it, expect):
    w = copy.deepcopy(gated)
    break_it(w)
    assert any(expect in m for m in gate_errors(w, openapi)), gate_errors(w, openapi)


def test_gate_codes_have_no_double_braces():
    for code in (GATE, PROMISE_HOOK, snippets.mark_product_code({})):
        assert "{{" not in code and "}}" not in code
