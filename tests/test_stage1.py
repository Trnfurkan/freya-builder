"""Stage 1: one test per failure seen in real practice runs, each named after what went wrong.

Every expected value comes from the brief, the tool API file or the public practice customers (read from the kit's
files here), not from numbers typed into the test."""
import json

from builder import blocks as b
from builder import openapi_tools as ot
from builder import snippets
from builder.facts import facts
from builder.validate import validate
from tests.conftest import KIT
from tests.test_offer_gate import POLICY, PROMISE_HOOK, account, call_vars, gate, ledger

OPENAPI = json.loads((KIT / "tools" / "openapi.json").read_text(encoding="utf-8"))
CASES = {c["id"]: c for c in json.loads((KIT / "public-test-set.json").read_text(encoding="utf-8"))}


def band_of(days_late):
    return next(x for x in POLICY["bands"] if x["min_days_late"] <= days_late <= x["max_days_late"])


# PUB-07: a holiday before the deadline got the deadline instead of the next business day -----------------------

def test_pub07_holiday_before_deadline_gets_next_business_day():
    case = CASES["PUB-07"]
    expected = case["expected"]["promises"][0]["date"]                  # from the practice customer's expected record
    v = facts(POLICY, case["variables"]["now"], account("PUB-07"))
    table = v["date_actions_" + band_of(case["products"][0]["days_late"])["name"]]
    entry = [e for e in table.split("; ") if e.startswith("Monday 2026-10-26")][0]
    assert entry == "Monday 2026-10-26 (holiday) -> offer Tuesday " + expected


# Payment requests: late personal loan got 3 instead of 2, early car loan 2 instead of 1 --------------------------

def test_requests_per_product_never_exceed_the_band_limit_from_the_brief():
    v = call_vars("PUB-12")
    made = {}
    state, pid, _ = gate(v)
    while state == "offer":
        made[pid] = made.get(pid, 0) + 1
        state, pid, _ = gate(v, "refused")
    limits = {p["product_id"]: band_of(p["days_late"])["attempts"] for p in account("PUB-12")}   # from briefs/brief-a.md
    assert made == limits
    assert limits["p2"] == 2 and limits["p3"] == 1


def test_workflow_without_the_gate_is_not_deliverable_when_the_brief_limits_requests():
    close = ot.api_tool(OPENAPI, "close_call", baked={"customer_id": "{{customer_id}}"}, pre_hook=b.close_pre_hook(), post_hook=b.close_post_hook(snippets))

    def wf(policy):
        return b.workflow("facts", [
            b.code_node("facts", snippets.date_facts_code(policy), "talk"),
            b.conversation("talk", "{{product_facts}}", tools=[close], transitions=[b.closed_transition("end")],
                           max_turns=40, max_turn_target="end"),
            b.end_node("end")])

    def errors(w):
        return [m for lvl, _, m in validate(w, OPENAPI).items if lvl == "ERROR"]

    assert any("no offer gate" in m for m in errors(wf(POLICY)))
    unlimited = {**POLICY, "bands": [{**x, "attempts": 0} for x in POLICY["bands"]]}
    assert not any("no offer gate" in m for m in errors(wf(unlimited)))


# Requests the customer hears and calls to the bank are two counters -----------------------------------------------

def test_customer_requests_and_bank_calls_are_counted_separately_per_product():
    v = call_vars("PUB-12")
    gate(v)                                                              # one request heard, for p1
    body = {"product_id": "p1", "amount": 510.0, "date": "2026-10-15"}
    snippets.run_pre_hook(PROMISE_HOOK, body, v)                         # bank call 1 (fails with HTTP 500)
    snippets.run_pre_hook(PROMISE_HOOK, body, v)                         # bank call 2 (the brief's one retry)
    assert ledger(v)["counts"] == {"p1": 1}                              # requests the customer heard
    assert json.loads(v["promise_attempts"]) == {"p1": 2}                # calls that reached the bank
    other = {"product_id": "p2", "amount": 1320.0, "date": "2026-10-16"}
    assert snippets.run_pre_hook(PROMISE_HOOK, other, v)["product_id"] == "p2"
    assert json.loads(v["promise_attempts"]) == {"p1": 2, "p2": 1}       # p2 has its own counter


def test_the_brief_retry_limit_reaches_the_bank_at_most_once_more():
    tries = 1 + 1                                                        # brief A rule 37: "If it fails, try once more."
    hook = snippets.promise_guard_code({"max_attempts": tries})
    v = call_vars("PUB-10")
    body = {"product_id": "p1", "amount": 365.0, "date": "2026-10-16"}
    sent = [snippets.run_pre_hook(hook, body, v)["product_id"] for _ in range(4)]
    assert sent == ["p1", "p1", "", ""]


def test_tool_api_says_a_failed_promise_records_nothing():
    assert ot.repeatable_on_failure(OPENAPI, "create_promise")
    text = OPENAPI["paths"]["/create_promise"]["post"]["responses"]["500"]["description"]
    assert "Nothing was recorded" in text


# Recovery after a tool error: holding line, no technical talk, never "recorded" without ok true ---------------------

def test_recovery_text_for_a_failed_promise():
    final = "I could not record the promise."
    text = b.failure_rules("create_promise", final, then="close with no_promise and service_error.")
    assert 'say only "One moment, please."' in text and "in the same message, call create_promise again" in text
    assert "Never describe a technical error, a system or a retry" in text
    assert "Only a create_promise result with ok true means it is recorded" in text
    assert "never end the call because of a failure on its own" in text
    assert 'If it fails again, say exactly: "' + final + '"' in text


# ok: true with verified: false was read as a success ------------------------------------------------------------

def test_wrong_security_answer_is_never_reported_as_correct():
    hook = snippets.post_result_code(on_success="Correct. Ask the next question.", on_failure="Ask again.")
    data = {"ok": True, "verified": False, "wrong_answers_left": 2, "all_verified": False}
    note = snippets.run_post_hook(hook, {"status": "success", "status_code": 200, "data": data})["data"]["what_to_do_next"]
    assert note.startswith("Result: verified is FALSE, all_verified is FALSE.")


def test_account_is_loaded_only_on_all_verified_true():
    t = b.go("load", "identity passed", b.is_true("verify_identity_response.data.all_verified"))
    conds = t["condition"]["conditions"]
    assert {c["operand"] for c in conds} == {"true", "True"}
    assert all(c["variable"].endswith("all_verified") for c in conds)


# close_call loop, early close, technical error said to the customer ---------------------------------------------------

def test_after_close_the_call_ends_on_any_answer_and_the_agent_says_one_word():
    kinds = {(c["variable"], c["operator"]) for c in b.closed_transition("end")["condition"]["conditions"] if "variable" in c}
    assert ("call_closed", "equals") in kinds and ("close_call_success_count", "greater_than") in kinds
    v = {}
    r = snippets.run_post_hook(b.close_post_hook(snippets), {"status": "success", "status_code": 200, "data": {"ok": True}}, v)
    assert v == {"call_closed": "yes"} and r["data"]["what_to_do_next"].endswith("Goodbye.")


def test_transferred_cannot_be_closed_before_the_transfer():
    others = ["promise", "paid", "no_promise", "not_verified", "not_reached"]
    work = ot.api_tool(OPENAPI, "close_call", enums={"outcome": others})
    assert "transferred" not in work["function"]["parameters"]["properties"]["body"]["properties"]["outcome"]["enum"]


def test_a_turn_cap_never_hangs_up_without_closing():
    close = ot.api_tool(OPENAPI, "close_call", baked={"customer_id": "{{customer_id}}"})
    wrap = b.wrap_up_node("wrap_up", close, "end")
    assert [t["function"]["name"] for t in wrap["tools"]] == ["close_call"]
    assert wrap["transitions"][0]["target_node"] == "end"


# --- raw-a: identity passed without all_verified; closing tool marks itself sent -----------------------------------

def _verify_hooks():
    pre = snippets.normalize_answer_code({"field_key": "field", "answer_key": "answer", "date_fields": ["date_of_birth"],
                                         "day_first": True, "remember_field": "verify_last_field"})
    post = snippets.post_result_code("Next.", "Again.", passed_fields={"field_var": "verify_last_field",
                                                                       "data_field": "verified", "set_var": "verified_fields",
                                                                       "need": 2, "flag": "identity_passed"})
    return pre, post


def _answer(pre, post, v, field, answer, verified):
    snippets.run_pre_hook(pre, {"field": field, "answer": answer}, v)
    snippets.run_post_hook(post, {"status": "success", "status_code": 200,
                                  "data": {"ok": True, "verified": verified, "wrong_answers_left": 3}}, v)


def test_pub05_both_answers_verified_without_all_verified_passes_identity():
    case = CASES["PUB-05"]
    pre, post = _verify_hooks()
    v = {}
    _answer(pre, post, v, "date_of_birth", case["identity"]["date_of_birth"], True)
    assert v.get("identity_passed") != "yes"
    _answer(pre, post, v, "phone_last4", case["identity"]["phone_last4"], True)
    assert v["identity_passed"] == "yes"
    t = b.identity_passed("load_account")
    assert {"variable": "identity_passed", "operator": "equals", "operand": "yes"} in t["condition"]["conditions"]


def test_identity_flag_needs_different_fields_and_true_answers():
    pre, post = _verify_hooks()
    v = {}
    _answer(pre, post, v, "date_of_birth", "1990-09-09", True)
    _answer(pre, post, v, "date_of_birth", "1990-09-09", True)             # the same question twice
    _answer(pre, post, v, "phone_last4", "0000", False)                    # a wrong answer
    assert v.get("identity_passed") != "yes"


def test_validator_wants_the_close_pre_hook():
    close = ot.api_tool(OPENAPI, "close_call", baked={"customer_id": "{{customer_id}}"}, post_hook=b.close_post_hook(snippets))
    wf = b.workflow("talk", [b.conversation("talk", "Talk.", tools=[close], transitions=[b.closed_transition("end")],
                                            max_turns=30, max_turn_target="end"), b.end_node("end")])
    assert any("close_pre_hook" in m for lvl, _, m in validate(wf, OPENAPI).items if lvl == "ERROR")
