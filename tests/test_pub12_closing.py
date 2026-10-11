"""PUB-12 (fix12-ab): a rejected duplicate promise read as a failure, close_call sent together with notes, and the
gate asking again for a product the customer had already agreed. Expected behaviour from the tool API file and
Brief B (rules 7, 8, 11), not from earlier agent runs."""
import json

from builder import blocks as b
from builder import openapi_tools as ot
from builder import snippets
from builder.validate import validate
from tests.conftest import KIT, no_attempts
from tests.test_offer_gate import GATE, POLICY, account, call_vars, gate, ledger

OPENAPI = json.loads((KIT / "tools" / "openapi.json").read_text(encoding="utf-8"))
CID = {"customer_id": "{{customer_id}}"}

PROMISE_POST = snippets.post_result_code(
    on_success="The promise is recorded.",
    on_failure="It was not recorded. Say only 'One moment, please.' and call create_promise again with the same values.",
    on_rejected="The bank did not carry this out: <error>. Do not repeat this call. If it says the product already has "
                "a promise, that promise is recorded: continue with the other products.",
    guard_var="promise_guard", fail_count_var="promise_fail_count", fail_limit=2,
    on_failure_final="It failed again. Say exactly: \"I could not record the promise.\"")


def post(result, v):
    return snippets.run_post_hook(PROMISE_POST, result, v)["data"]["what_to_do_next"]


# --- a rejection is not a service failure -----------------------------------------------------------------------

def test_tool_api_separates_a_rejection_from_a_service_failure():
    op = OPENAPI["paths"]["/create_promise"]["post"]["responses"]
    assert "not carried out" in op["200"]["description"]
    assert "Nothing was recorded. The call may be repeated." in op["500"]["description"]


def test_duplicate_promise_rejection_is_not_retried_and_not_counted():
    """fix12-ab v2: the second create_promise for p1 got ok false 'this product already has a promise'."""
    v = {}
    dup = {"status": "success", "status_code": 200, "data": {"ok": False, "error": "this product already has a promise"}}
    text = post(dup, v)
    assert "this product already has a promise" in text and "Do not repeat this call" in text
    assert "One moment" not in text and "promise_fail_count" not in v
    assert "could not record" not in post(dup, v)                     # a second rejection never becomes the final failure


def test_a_service_failure_still_counts_and_ends_with_the_final_line():
    v = {}
    down = {"status": "error", "status_code": 500, "data": {"ok": False, "error": "service unavailable"}}
    assert "One moment" in post(down, v) and v["promise_fail_count"] == 1
    assert "could not record" in post(down, v)


def test_a_guard_block_keeps_its_own_message():
    v = {"promise_guard": "NOT RECORDED, blocked before sending: date 2026-11-30 cannot be recorded for p1."}
    text = post({"status": "success", "status_code": 200, "data": {"ok": False, "error": "unknown product_id"}}, v)
    assert text.startswith("NOT RECORDED, blocked before sending") and "Do not repeat this call" not in text


def test_ok_true_is_still_a_success():
    assert post({"status": "success", "status_code": 200, "data": {"ok": True, "promise_id": "pr1"}}, {}) == \
        "The promise is recorded."


# --- close only after every product is settled ------------------------------------------------------------------

PROMISE_MARK = snippets.mark_product_code({"state": "promise_requested", "settled_flag": b.SETTLED_FLAG})
NOTE_MARK = snippets.mark_product_code({"state": "noted", "settled_flag": b.SETTLED_FLAG})


def test_the_flag_rises_only_when_every_product_has_a_promise_or_a_note():
    """Brief B rule 8: every product without a promise gets a note; PUB-12 has three products."""
    v = call_vars("PUB-12")
    assert [p["product_id"] for p in account("PUB-12")] == ["p1", "p2", "p3"]
    snippets.run_pre_hook(PROMISE_MARK, {"product_id": "p1"}, v)
    assert v[b.SETTLED_FLAG] == "no"
    snippets.run_pre_hook(NOTE_MARK, {"product_id": "p2", "reason": "refused"}, v)
    assert v[b.SETTLED_FLAG] == "no"
    snippets.run_pre_hook(NOTE_MARK, {"product_id": "p3", "reason": "refused"}, v)
    assert v[b.SETTLED_FLAG] == "yes"


def test_a_blocked_promise_does_not_settle_a_product():
    v = call_vars("PUB-12")
    for pid in ("", "p2", "p3"):
        snippets.run_pre_hook(PROMISE_MARK if not pid else NOTE_MARK, {"product_id": pid}, v)
    assert v[b.SETTLED_FLAG] == "no"                                      # p1's request was blanked by the guard


def _negotiate(tools, transitions):
    return b.conversation("negotiate", "{{product_facts}}", tools=tools, transitions=transitions, max_turns=40,
                          max_turn_target="wrap_up")


def _workflow(work):
    close = ot.api_tool(OPENAPI, "close_call", baked=CID, pre_hook=b.close_pre_hook(), post_hook=b.close_post_hook(snippets))
    note = ot.api_tool(OPENAPI, "add_note", baked=CID, pre_hook=NOTE_MARK)
    return b.workflow("negotiate", [work, b.closing_node("closing", close, "end", extra_tools=[note]),
                                    b.wrap_up_node("wrap_up", close, "end"), b.end_node("end")])


def _errors(wf):
    return [m for lvl, _, m in validate(wf, OPENAPI).items if lvl == "ERROR"]


def test_settled_flag_closing_with_a_note_tool_is_now_rejected():
    """The settled-flag pattern failed live (fix13-ab); its closing node also holds a note tool next to close_call."""
    promise = ot.api_tool(OPENAPI, "create_promise", baked=CID, pre_hook=PROMISE_MARK)
    note = ot.api_tool(OPENAPI, "add_note", baked=CID, pre_hook=NOTE_MARK)
    wf = _workflow(_negotiate([promise, note], [b.settled_transition("closing")]))
    assert any("a note tool and the closing tool together" in m for m in _errors(wf))


# --- closing through a ready-to-close transition (fix14-ab: close_call sent with add_note in 4 of 30 calls) ------

def _ready_workflow(work_tools):
    close = ot.api_tool(OPENAPI, "close_call", baked=CID, pre_hook=b.close_pre_hook(), post_hook=b.close_post_hook(snippets))
    work = _negotiate(work_tools, [b.ready_to_close("closing")])
    return b.workflow("negotiate", [work, b.closing_node("closing", close, "end"),
                                    b.wrap_up_node("wrap_up", close, "end"), b.end_node("end")])


def test_ready_to_close_pattern_validates_and_keeps_close_call_away_from_notes():
    promise = ot.api_tool(OPENAPI, "create_promise", baked=CID)
    note = ot.api_tool(OPENAPI, "add_note", baked=CID, pre_hook=snippets.mark_product_code({"state": "noted"}))
    wf = _ready_workflow([promise, note])
    assert _errors(wf) == []
    work, closing = wf["nodes"]["negotiate"], wf["nodes"]["closing"]
    assert "close_call" not in [t["function"]["name"] for t in work["tools"]]
    assert [t["function"]["name"] for t in closing["tools"]] == ["close_call"]     # nothing to send with it
    assert work["transitions"][0]["is_tool_call"] and work["transitions"][0]["target_node"] == "closing"
    assert closing["on_enter"]["type"] == "llm" and "Call no other tool" in closing["task_message"]


def test_validator_rejects_a_note_tool_next_to_close_call():
    note = ot.api_tool(OPENAPI, "add_note", baked=CID)
    close = ot.api_tool(OPENAPI, "close_call", baked=CID, pre_hook=b.close_pre_hook(), post_hook=b.close_post_hook(snippets))
    wf = _ready_workflow([note, close])
    assert any("a note tool and the closing tool together" in m for m in _errors(wf))


def test_validator_rejects_close_call_next_to_settling_tools():
    promise = ot.api_tool(OPENAPI, "create_promise", baked=CID, pre_hook=PROMISE_MARK)
    note = ot.api_tool(OPENAPI, "add_note", baked=CID, pre_hook=NOTE_MARK)
    close = ot.api_tool(OPENAPI, "close_call", baked=CID, pre_hook=b.close_pre_hook(), post_hook=b.close_post_hook(snippets))
    wf = _workflow(_negotiate([promise, note, close], [b.settled_transition("closing"), b.closed_transition("end")]))
    assert any("next to tools that settle products" in m for m in _errors(wf))


def test_validator_wants_a_transition_on_the_flag():
    promise = ot.api_tool(OPENAPI, "create_promise", baked=CID, pre_hook=PROMISE_MARK)
    wf = _workflow(_negotiate([promise], [b.go("closing", "x", b.eq("something_else", "yes"))]))
    assert any("no transition on 'all_settled'" in m for m in _errors(wf))


# --- the gate does not ask again for a product the customer already agreed ---------------------------------------

def test_gate_skips_products_agreed_in_the_working_node():
    """PUB-12: 'I can pay the credit card tomorrow' (p1); the gate went on to ask for p1 again."""
    code = snippets.offer_gate_code({"line_template": "For your {product}, could you pay {amount} on {date}?",
                                     "settled_var": "settled_products"})
    v = call_vars("PUB-12")
    v["settled_products"] = "p1"
    asked = []
    for _ in range(10):
        exec(code, {"vars": v})
        if v["offer_state"] != "offer":
            break
        asked.append(v["offer_product"])
        v["offer_reply"] = "refused"
    assert "p1" not in asked and asked == ["p2", "p2", "p3"]               # brief A rule 30 limits: late 2, early 1
    assert ledger(v)["state"]["p1"] == "agreed"


def test_gate_without_settled_var_behaves_as_before():
    v = call_vars("PUB-12")
    v["settled_products"] = "p1"
    state, pid, _ = gate(v)                                                # GATE has no settled_var
    assert pid == "p1"


def test_settled_products_extraction_block():
    e = b.settled_products_extraction()
    assert e["name"] == "settled_products" and "min_confidence" in e


# --- one gate per product position (fix14-ab PUB-12: 3-4 requests on the personal loan where 2 were allowed) ------

def _gate_code(k):
    return snippets.offer_gate_code({"line_template": "For your {product}, could you pay {amount} on {date}?",
                                     "product_index": k})


def _ask(v, k, reply=None):
    if reply is not None:
        v["offer_reply"] = reply
    exec(_gate_code(k), {"vars": v})
    return v["offer_state"], v.get("offer_product", "")


def test_a_gate_asks_only_for_its_own_product():
    """PUB-12: the customer agreed the card (p1) and refused both loans. Brief A rule 30 via Brief B rule 5: the late
    personal loan (p2) gets 2 requests, the early car loan (p3) 1, the card none."""
    v = call_vars("PUB-12")
    asked = {}
    for k in (2, 3):
        state, pid = _ask(v, k)
        while state == "offer":
            asked[pid] = asked.get(pid, 0) + 1
            state, pid = _ask(v, k, "refused")
    assert asked == {"p2": 2, "p3": 1}
    assert "p1" not in ledger(v)["counts"]


def test_a_gate_for_a_missing_product_asks_nothing():
    v = call_vars("PUB-07")                                                 # one product
    assert _ask(v, 2) == ("done", "")


def test_product_gates_validate_in_a_workflow():
    close = ot.api_tool(OPENAPI, "close_call", baked=CID, pre_hook=b.close_pre_hook(), post_hook=b.close_post_hook(snippets))
    note = ot.api_tool(OPENAPI, "add_note", baked=CID, pre_hook=snippets.mark_product_code({"state": "noted"}))
    gates, to_gates = b.product_offer_loops(3, "negotiate", _gate_code)
    work = _negotiate([note], [b.ready_to_close("closing"), *to_gates])
    facts = b.code_node("facts", snippets.date_facts_code({**POLICY, "plans_in_facts": False}), "negotiate")
    wf = b.workflow("facts", [facts, work, *gates, b.closing_node("closing", close, "end"),
                              b.wrap_up_node("wrap_up", close, "end"), b.end_node("end")])
    assert _errors(wf) == []
    assert [t["target_node"] for t in work["transitions"][1:]] == ["offer_gate_1", "offer_gate_2", "offer_gate_3"]
    assert wf["nodes"]["offer_request_2"]["transitions"][1]["target_node"] == "offer_gate_2"   # refusal: same product


# --- the working node does not see the attempts' amounts and dates (fix15-ab v2: attempt 1 asked word for word) ---

def test_without_plans_in_facts_the_prompt_has_no_attempt_amounts_or_dates():
    from builder.facts import facts
    with_plans = facts(POLICY, "2026-10-14T11:00", account("PUB-12"))
    gated = facts({**POLICY, "plans_in_facts": False}, "2026-10-14T11:00", account("PUB-12"))
    assert "attempt 1 = propose" in with_plans["product_facts"]
    assert "attempt 1 = propose" not in gated["product_facts"]
    assert "persuasion requests are made only by the offer gate for product 2" in gated["product_facts"]
    assert gated["offer_plans"] == with_plans["offer_plans"]            # the gates still get every attempt
    assert "1320.00" in gated["product_facts"]                          # the debt itself is still stated


def test_validator_wants_plans_out_of_the_prompt_when_a_gate_exists():
    close = ot.api_tool(OPENAPI, "close_call", baked=CID, pre_hook=b.close_pre_hook(), post_hook=b.close_post_hook(snippets))
    gates, to_gates = b.product_offer_loops(3, "negotiate", _gate_code)

    def wf(policy):
        work = _negotiate([], [b.ready_to_close("closing"), *to_gates])
        return b.workflow("facts", [b.code_node("facts", snippets.date_facts_code(policy), "negotiate"), work, *gates,
                                    b.closing_node("closing", close, "end"), b.wrap_up_node("wrap_up", close, "end"),
                                    b.end_node("end")])
    assert any("plans_in_facts" in m for m in _errors(wf(POLICY)))
    assert not any("plans_in_facts" in m for m in _errors(wf({**POLICY, "plans_in_facts": False})))
