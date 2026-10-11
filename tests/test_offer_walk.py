"""The controlled offer flow (the walk). From fix16-ab PUB-12 (call 752ae881): messages 12 and 18 were requests the
working node worded itself ("can you pay that today?"), never counted; 14 and 16 (offer_request_2) and 20
(offer_request_3) came from the gates. Limits come from the brief's band table, read here from briefs/brief-a.md
(Brief B uses Brief A's bands), never typed into the code or the tests."""
import copy
import json

from builder import blocks as b
from builder import openapi_tools as ot
from builder import snippets
from builder.facts import facts
from builder.validate import validate
from tests.conftest import KIT
from tests.test_offer_gate import CASES, account, policy_from_brief

OPENAPI = json.loads((KIT / "tools" / "openapi.json").read_text(encoding="utf-8"))
CID = {"customer_id": "{{customer_id}}"}
POLICY = {**policy_from_brief("briefs/brief-a.md"), "plans_in_facts": False}
WALK = snippets.offer_gate_code({"line_template": "For your {product}, could you pay {amount} on {date}?",
                                 "product_labels": {"credit_card": "credit card ending {card_last4}"}, "walk": True})


def start(case="PUB-12", policy=POLICY):
    v = facts(policy, CASES[case]["variables"]["now"], account(case))
    assert v["date_facts_state"] == "ok"
    return v


def enter_gate(v, reply=None):
    if reply is not None:
        v["offer_reply"] = reply
    exec(WALK, {"vars": v})
    return v["offer_state"], v.get("offer_product", "")


def mark(v, k):
    exec(snippets.build("mark_position", {"product_index": k}), {"vars": v})


def led(v):
    return json.loads(v["offer_ledger"])


def limit(case, pid, policy=POLICY):
    days = next(p["days_late"] for p in account(case) if p["product_id"] == pid)
    return next(x["attempts"] for x in policy["bands"] if x["min_days_late"] <= days <= x["max_days_late"])


def refuse_everything(v):
    """The customer refuses every request; returns the requests heard, product by product, in order."""
    heard = []
    state, pid = enter_gate(v)
    while state == "offer":
        heard.append(pid)
        state, pid = enter_gate(v, "refused")
    return heard


# --- the limits are the brief's -------------------------------------------------------------------------------

def test_band_limits_come_from_the_brief_table():
    bands = {x["name"]: x for x in policy_from_brief("briefs/brief-a.md")["bands"]}
    assert (bands["late"]["attempts"], bands["early"]["attempts"]) == (2, 1)            # brief A section 7 table
    assert [p["days_late"] for p in account("PUB-12")] == [16, 16, 7]                   # card, personal loan, car loan


def test_1_personal_loan_late_band_two_requests_then_no_third():
    v = start()
    mark(v, 1)                                                                          # the card was agreed (message 11)
    heard = refuse_everything(v)
    assert heard.count("p2") == limit("PUB-12", "p2") == 2
    assert enter_gate(v) == ("done", "")                                                # a third is never prepared
    assert enter_gate(v, "refused") == ("done", "")


def test_2_car_loan_early_band_one_request_then_no_second():
    v = start()
    mark(v, 1)
    heard = refuse_everything(v)
    assert heard.count("p3") == limit("PUB-12", "p3") == 1
    assert led(v)["state"]["p3"] == "refused"


def test_the_code_follows_the_policy_not_fixed_numbers():
    """Change the brief's numbers: the walk follows them (nothing is fixed at 2 and 1)."""
    more = copy.deepcopy(POLICY)
    for band in more["bands"]:
        band["attempts"] = band["attempts"] + 1
    more["attempt_offers"] = more["attempt_offers"] + [{"amount": "floor", "date": "deadline"}]
    v = start(policy=more)
    mark(v, 1)
    heard = refuse_everything(v)
    assert heard.count("p2") == 3 and heard.count("p3") == 2


def test_3_repeats_from_other_nodes_and_loops_never_pass_the_limit():
    v = start()
    mark(v, 1)
    for _ in range(6):                                  # the working node sends the call back to the gate again and again
        enter_gate(v)                                   # (no answer, a question, a hesitation: no reply recorded)
        enter_gate(v, "other")
        mark(v, 1)                                      # a mark node on the way never resets anything
    assert led(v)["counts"]["p2"] <= limit("PUB-12", "p2")
    assert led(v)["counts"].get("p3", 0) <= limit("PUB-12", "p3")
    assert sum(led(v)["counts"].values()) <= limit("PUB-12", "p2") + limit("PUB-12", "p3")


def test_4_each_product_has_its_own_counter():
    v = start()
    mark(v, 1)
    assert enter_gate(v) == ("offer", "p2")
    assert led(v)["counts"] == {"p2": 1}
    enter_gate(v, "refused")
    enter_gate(v, "refused")                            # p2 used up: the walk moves to p3 with p3's own count
    assert led(v)["counts"] == {"p2": 2, "p3": 1}


def test_5_refusal_hesitation_correction_and_return():
    v = start()
    mark(v, 1)
    assert enter_gate(v) == ("offer", "p2")             # attempt 1 for p2
    assert enter_gate(v, "other") == ("offer", "p2")    # hesitation / a question, then back: attempt 2 (counted)
    assert led(v)["counts"]["p2"] == 2
    state, pid = enter_gate(v, "accepted")             # the customer corrects themself and accepts attempt 2
    assert led(v)["state"]["p2"] == "agreed" and led(v)["agreed"]["p2"]["date"]
    assert (state, pid) == ("offer", "p3")              # the walk goes on by itself, never back to p2
    state, _ = enter_gate(v, "refused")
    assert state == "done" and "p2 (" + led(v)["agreed"]["p2"]["amount"] in v["offer_summary"]
    assert "used for: p3" in v["offer_summary"]
    assert enter_gate(v) == ("done", "")                # returning to the gate after a tool call asks nothing new


def test_a_mark_only_moves_an_open_product_and_never_touches_counts():
    v = start()
    mark(v, 1)
    enter_gate(v)
    enter_gate(v, "refused")
    enter_gate(v, "refused")                            # p2 refused after its two requests
    before = led(v)["counts"]
    mark(v, 2)                                          # a late "agreed" mark cannot reopen or rewrite it
    assert led(v)["state"]["p2"] == "refused" and led(v)["counts"] == before
    mark(v, 9)                                          # a position without a product changes nothing
    assert led(v)["counts"] == before


def test_6_promise_calls_have_their_own_counter_and_no_duplicate():
    """create_promise failing (HTTP 500) is retried once (brief A rule 37); the bank's own refusal of a second promise
    for a product is never retried; neither touches the requests the customer heard."""
    v = start()
    mark(v, 1)
    enter_gate(v)
    heard = copy.deepcopy(led(v)["counts"])
    guard = snippets.promise_guard_code({"check_dates": True, "check_amount": True, "max_attempts": 2})
    body = {"product_id": "p1", "amount": 510.0, "date": "2026-10-15"}
    sent = [snippets.run_pre_hook(guard, body, v)["product_id"] for _ in range(3)]
    assert sent == ["p1", "p1", ""]                     # first try, one retry, then nothing reaches the bank
    assert led(v)["counts"] == heard                    # the requests heard are a different counter
    post = snippets.post_result_code(on_success="Recorded.", on_failure="Retry once.", fail_count_var="pf",
                                     on_rejected="Not carried out: <error>. Do not repeat this call.")
    dup = {"status": "success", "status_code": 200, "data": {"ok": False, "error": "this product already has a promise"}}
    note = snippets.run_post_hook(post, dup, v)["data"]["what_to_do_next"]
    assert "Do not repeat" in note and "pf" not in v


# --- the workflow graph -------------------------------------------------------------------------------------

def walk_workflow(**changes):
    close = ot.api_tool(OPENAPI, "close_call", baked=CID, post_hook=b.close_post_hook(snippets))
    note = ot.api_tool(OPENAPI, "add_note", baked=CID, pre_hook=snippets.mark_product_code({"state": "noted"}))
    marks, to_marks = b.agreed_marks(3, "negotiate", snippets)
    work = b.conversation("negotiate", changes.get("task", "{{product_facts}} {{offer_summary}}"), tools=[note],
                          transitions=[b.ready_to_close("closing"), b.to_gate("offer_gate"), *to_marks],
                          max_turns=40, max_turn_target="wrap_up")
    nodes = [b.code_node("facts", snippets.date_facts_code(changes.get("policy", POLICY)), "negotiate"), work,
             *b.offer_walk("offer_gate", "offer_request", "negotiate", WALK), *marks,
             b.closing_node("closing", close, "end"), b.wrap_up_node("wrap_up", close, "end"), b.end_node("end")]
    return b.workflow("facts", nodes)


def report(wf):
    rep = validate(wf, OPENAPI, ["customer_id", "now"])
    return [m for lvl, _, m in rep.items if lvl == "ERROR"], [m for lvl, _, m in rep.items if lvl == "WARN"]


def test_the_walk_validates_and_answers_go_into_the_gate():
    wf = walk_workflow()
    errors, warnings = report(wf)
    assert errors == []
    req = wf["nodes"]["offer_request"]
    assert [t["target_node"] for t in req["transitions"]] == ["offer_gate", "offer_gate", "negotiate"]
    assert any("not provable from the graph" in w for w in warnings)   # the model's free text is reported, not hidden
    assert all(wf["nodes"][f"mark_agreed_{k}"]["passthrough_default_transition"] == "negotiate" for k in (1, 2, 3))


def test_validator_rejects_an_answer_that_skips_the_gate():
    wf = walk_workflow()
    wf["nodes"]["offer_request"]["transitions"][0]["target_node"] = "negotiate"     # accepted -> working node
    assert any("'accepted' answer must go back into the offer gate" in m for m in report(wf)[0])


def test_validator_rejects_a_path_around_the_counter():
    wf = walk_workflow()
    wf["nodes"]["negotiate"]["transitions"].append(b.go("offer_request", "shortcut", b.is_set("x")))
    assert any("without passing the offer gate" in m for m in report(wf)[0])


def test_validator_rejects_a_reset_through_a_loop():
    wf = walk_workflow()
    wf["nodes"]["mark_agreed_2"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"] = \
        "vars['offer_ledger'] = ''"                                                   # a loop node that wipes the counts
    assert any("outside the offer gate" in m for m in report(wf)[0])


def test_validator_rejects_a_plan_shown_to_the_model_and_plans_left_in_facts():
    assert any("{{offer_plan" in m for m in report(walk_workflow(task="{{offer_plan_p2}}"))[0])
    with_plans = {**POLICY, "plans_in_facts": True}
    assert any("plans_in_facts" in m for m in report(walk_workflow(policy=with_plans))[0])


def test_validator_rejects_limits_counted_only_at_the_bank():
    """A promise counter (max_attempts) without the gate leaves the spoken requests uncounted."""
    close = ot.api_tool(OPENAPI, "close_call", baked=CID, post_hook=b.close_post_hook(snippets))
    promise = ot.api_tool(OPENAPI, "create_promise", baked=CID,
                          pre_hook=snippets.promise_guard_code({"check_dates": True, "max_attempts": 2}))
    wf = b.workflow("facts", [b.code_node("facts", snippets.date_facts_code(POLICY), "negotiate"),
                              b.conversation("negotiate", "{{product_facts}}", tools=[promise],
                                             transitions=[b.ready_to_close("closing")], max_turns=30,
                                             max_turn_target="wrap_up"),
                              b.closing_node("closing", close, "end"), b.wrap_up_node("wrap_up", close, "end"),
                              b.end_node("end")])
    assert any("no offer gate" in m for m in report(wf)[0])
