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
    assert state == "done" and "personal loan (p2): " + led(v)["agreed"]["p2"]["amount"] in v["offer_summary"]
    assert "used for: car loan (p3)" in v["offer_summary"]
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
    promise = ot.api_tool(OPENAPI, "create_promise", baked=CID,
                          pre_hook=snippets.promise_guard_code({"check_dates": True, "max_attempts": 2}))
    marks, to_marks = b.agreed_marks(3, "negotiate", snippets)
    work = b.conversation("negotiate", changes.get("task", "{{product_facts}}"), tools=[],
                          transitions=[b.to_records("records_entry"), b.to_gate("offer_gate"), *to_marks],
                          max_turns=40, max_turn_target="wrap_up")
    nodes = [b.code_node("facts", snippets.date_facts_code(changes.get("policy", POLICY)), "negotiate"), work,
             *b.offer_walk("offer_gate", "offer_request", "negotiate", WALK, done_target="records"), *marks,
             b.records_entry("records_entry", "records", snippets),
             b.records_node("records", [promise, note], "closing"),
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
    assert [t["target_node"] for t in req["transitions"]] == ["offer_gate"] * 4 + ["negotiate"]
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


# --- fix17-ab: after done, no new offer; too_much per product; no early close -------------------------------------

def test_after_done_no_node_that_can_offer_is_reachable():
    wf = walk_workflow()
    assert report(wf)[0] == []
    gate_done = [t for t in wf["nodes"]["offer_gate"]["transitions"] if t["target_node"] != "offer_request"]
    assert [t["target_node"] for t in gate_done] == ["records"]
    records = wf["nodes"]["records"]
    assert [t["target_node"] for t in records["transitions"]] == ["closing"]          # no way back
    assert [t["function"]["name"] for t in records["tools"]] == ["create_promise", "add_note"]


def test_validator_rejects_done_into_the_working_node():
    """fix17-ab v2 message 17: done went back to 'work', which made up a car loan offer."""
    wf = walk_workflow()
    for t in wf["nodes"]["offer_gate"]["transitions"]:
        if t["target_node"] == "records":
            t["target_node"] = "negotiate"
    assert any("after the walk is done" in m for m in report(wf)[0])


def test_validator_rejects_a_records_node_that_leads_back_to_an_offer():
    wf = walk_workflow()
    wf["nodes"]["records"]["transitions"].append(b.to_gate("offer_gate"))
    assert any("after the walk is done" in m for m in report(wf)[0])


def test_too_much_only_when_said_and_only_for_that_product():
    v = start()
    v["full_too_much"] = "yes"                        # a call-wide flag (fix17-ab) is not read in the walk
    mark(v, 1)
    enter_gate(v)
    plans = json.loads(v["offer_plans"])["plans"]
    assert v["offer_line"].count(plans["p2"][0]["amount"]) == 1        # attempt 1: the full amount, not the floor
    state, pid = enter_gate(v, "refused")                              # a plain no is not too_much
    assert "too_much" not in led(v) and pid == "p2"
    v2 = start()
    mark(v2, 1)
    enter_gate(v2)
    enter_gate(v2, "too_much")                                         # said about p2 only
    assert led(v2)["too_much"] == {"p2": True}
    while v2["offer_product"] == "p2":
        enter_gate(v2, "refused")
    assert v2["offer_product"] == "p3" and plans["p3"][0]["amount"] in v2["offer_line"]   # p3 keeps its full amount


def test_too_much_with_no_attempt_left_offers_nothing_more():
    v = start("PUB-12")
    mark(v, 1)
    mark(v, 2)                                                         # only the car loan (early band, 1 attempt) open
    assert enter_gate(v) == ("offer", "p3")
    assert enter_gate(v, "too_much") == ("done", "")
    assert led(v)["state"]["p3"] == "refused" and led(v)["counts"]["p3"] == limit("PUB-12", "p3")


def test_too_much_uses_the_floor_when_the_plan_allows_it():
    """Brief A rule 30: attempt 1 is for the floor if the customer already said the full amount is too much."""
    v = start("PUB-06")                                               # one late credit card
    enter_gate(v)
    step = json.loads(v["offer_plans"])["plans"]["p1"]
    enter_gate(v, "too_much")
    assert step[1]["amount"] in v["offer_line"]                        # attempt 2: the floor on the deadline
    assert led(v)["counts"]["p1"] == 2 == limit("PUB-06", "p1")


def test_the_reply_classes_keep_a_plain_no_out_of_too_much():
    req = walk_workflow()["nodes"]["offer_request"]
    e = req["extraction_schema"][0]
    assert e["enum_values"] == ["accepted", "own_terms", "too_much", "refused", "other"]
    assert "a plain no is refused, never too_much" in e["description"]


def test_card_agreed_then_both_loans_refused_gives_the_records_summary():
    """PUB-12: card agreed in the customer's words (mark_agreed_1), loans refused after their requests."""
    v = start()
    mark(v, 1)
    assert refuse_everything(v) == ["p2", "p2", "p3"]
    summary = v["offer_summary"]
    assert "Agreed: credit card ending 9015 (p1): on the terms the customer said in this call" in summary
    assert "used for: personal loan (p2), car loan (p3)" in summary


def test_no_early_close_from_the_working_node():
    wf = walk_workflow()
    wf["nodes"]["negotiate"]["transitions"].insert(0, b.ready_to_close("closing"))
    assert any("can go straight to 'closing'" in m for m in report(wf)[0])
    wf = walk_workflow()
    close = wf["nodes"]["closing"]["tools"][0]
    wf["nodes"]["negotiate"]["tools"] = [close]
    assert any("holds the closing tool" in m for m in report(wf)[0])


def test_closing_and_records_have_speakable_content():
    wf = walk_workflow()
    for k in ("records", "closing"):
        oe = wf["nodes"][k]["on_enter"]
        assert oe["type"] == "llm" and oe["prompt"].strip()
    wf["nodes"]["closing"]["on_enter"]["prompt"] = ""
    assert any("on_enter llm without a prompt" in m for m in report(wf)[0])


# --- fix18-ab: own terms go into the gate; nothing after done sees the facts -----------------------------------

def test_own_terms_mark_the_product_and_the_walk_goes_on():
    """PUB-12 (fix18-ab): asked for the card, the customer said "I will pay the credit card tomorrow"."""
    v = start()
    assert enter_gate(v) == ("offer", "p1")
    state, pid = enter_gate(v, "own_terms")
    assert led(v)["state"]["p1"] == "agreed" and led(v)["agreed"]["p1"] == {"own_terms": True}
    assert (state, pid) == ("offer", "p2")                              # never back to the working node, never p1 again
    while state == "offer":
        state, pid = enter_gate(v, "refused")
    assert led(v)["counts"] == {"p1": 1, "p2": limit("PUB-12", "p2"), "p3": limit("PUB-12", "p3")}
    assert "credit card ending 9015 (p1): on the terms the customer said in this call" in v["offer_summary"]


def test_validator_rejects_facts_in_the_records_node():
    """fix18-ab: the records node's prompt held {{product_facts}}; it asked for the car loan's floor on its deadline."""
    wf = walk_workflow()
    wf["nodes"]["records"]["task_message"] += " Account facts: {{product_facts}}"
    assert any("after the walk is done the model here sees ['product_facts']" in m for m in report(wf)[0])
    wf = walk_workflow()
    wf["system_prompt"] = "Facts: {{date_facts}}"
    assert any("the system prompt shows ['date_facts']" in m for m in report(wf)[0])
    assert report(walk_workflow())[0] == []


# --- fix20-a: closing tool recognised by its parameters; one-product records close after the promise --------------

def test_only_the_closing_hook_counts_as_a_closing_tool():
    from builder.validate import _is_closer
    close = ot.api_tool(OPENAPI, "close_call", baked=CID, post_hook=b.close_post_hook(snippets))
    note = ot.api_tool(OPENAPI, "add_note", baked=CID, post_hook=snippets.post_result_code("Saved.", "Not saved."))
    assert _is_closer(close) and not _is_closer(note)      # post_result.py's comments mention call_closed
    assert "call_closed" in note["post_process_code"]


def test_records_node_closes_after_the_promise_in_a_one_product_brief():
    close = ot.api_tool(OPENAPI, "close_call", baked=CID, post_hook=b.close_post_hook(snippets))
    promise = ot.api_tool(OPENAPI, "create_promise", baked=CID)
    rec = b.records_node("records", [promise], "closing", close_after="create_promise")
    conds = [json.dumps(t["condition"]) for t in rec["transitions"]]
    assert all(t["target_node"] == "closing" for t in rec["transitions"])
    assert "create_promise_response.data.ok" in conds[0] and "create_promise_failure_count" in conds[1]
    assert rec["transitions"][-1]["is_tool_call"]
    plain = b.records_node("records", [promise], "closing")
    assert len(plain["transitions"]) == 1                     # several products: notes first, so no shortcut


# --- fix21-a PUB-06: "the full amount is too much" -> the floor once, same date, with the note (Brief A rule 26) ---

FLOOR_WALK = snippets.offer_gate_code({"line_template": "For your {product}, could you pay {amount} on {date}?",
                                       "product_labels": {"credit_card": "credit card ending {card_last4}"},
                                       "walk": True, "floor_counter_offer": True})


def floor_gate(v, reply=None):
    if reply is not None:
        v["offer_reply"] = reply
    exec(FLOOR_WALK, {"vars": v})
    return v["offer_state"], v.get("offer_product", "")


def test_pub06_too_much_said_before_the_walk_gets_the_floor_on_friday_with_the_note():
    want = CASES["PUB-06"]["expected"]["promises"][0]                   # 637.5 on 2026-10-16 in the practice customer
    v = start("PUB-06")
    exec(snippets.build("mark_position", {"product_index": 1, "mark": "too_much"}), {"vars": v})
    assert floor_gate(v) == ("offer", "p1")
    assert "%.2f" % want["amount"] in v["offer_line"] and want["date"] in v["offer_line"]
    assert "stays overdue" in v["offer_line"]
    assert led(v)["counts"]["p1"] == 1                                  # it is attempt 1 (rule 30: the floor if too much)
    floor_gate(v, "accepted")
    assert led(v)["agreed"]["p1"]["amount"] == "%.2f" % want["amount"] and led(v)["agreed"]["p1"]["date"] == want["date"]


def test_too_much_answered_to_a_request_gets_the_floor_once_on_the_same_date_uncounted():
    v = start("PUB-06")
    floor_gate(v)
    first = dict(led(v)["step"])
    assert floor_gate(v, "too_much") == ("offer", "p1")
    assert first["floor"] in v["offer_line"] and first["date"] in v["offer_line"] and "stays overdue" in v["offer_line"]
    assert led(v)["counts"]["p1"] == 1                                  # the counter-offer is not a persuasion attempt
    state, _ = floor_gate(v, "too_much")                                # said again: no second floor counter-offer
    assert led(v)["floor_offered"] == {"p1": True}
    assert led(v)["counts"]["p1"] <= limit("PUB-06", "p1")


def test_too_much_on_an_early_band_product_still_gets_the_floor_once():
    v = start("PUB-12")
    mark(v, 1)
    mark(v, 2)
    assert floor_gate(v) == ("offer", "p3")
    assert floor_gate(v, "too_much") == ("offer", "p3")                 # rule 26 applies in every band
    assert led(v)["counts"]["p3"] == limit("PUB-12", "p3") == 1
    assert floor_gate(v, "refused") == ("done", "")                     # refusing the floor is a flat refusal, none left


def test_too_much_marks_go_into_the_gate():
    nodes, transitions = b.too_much_marks(3, "offer_gate", snippets)
    assert [n["passthrough_default_transition"] for n in nodes] == ["offer_gate"] * 3
    assert all(t["is_tool_call"] for t in transitions)


def test_a_walk_with_too_much_marks_validates():
    wf = walk_workflow()
    tm_nodes, tm_trans = b.too_much_marks(3, "offer_gate", snippets)
    wf["nodes"]["negotiate"]["transitions"] += tm_trans
    for n in tm_nodes:
        wf["nodes"][n["name"]] = n
    assert report(wf)[0] == []



# --- fix22-a PUB-01: "I can pay the full amount today" -> records with the summary ------------------------------

def test_pub01_pay_today_reaches_the_records_with_the_agreement_in_the_summary():
    """The customer agreed at the first question; the walk never ran. The records entry writes the summary."""
    v = start("PUB-01")
    mark(v, 1)
    entry = snippets.offer_gate_code({"product_labels": {"credit_card": "credit card ending {card_last4}"},
                                      "walk": True, "summary_only": True})
    exec(entry, {"vars": v})
    assert v["offer_state"] == "done" and v.get("offer_line", "") == ""
    assert "credit card ending 4821 (p1): on the terms the customer said in this call" in v["offer_summary"]
    assert led(v)["counts"] == {}                                       # no request made or counted


def test_summary_only_never_makes_a_request_even_with_open_products():
    v = start("PUB-12")
    entry = snippets.offer_gate_code({"walk": True, "summary_only": True})
    exec(entry, {"vars": v})
    assert v["offer_state"] == "done" and led(v)["counts"] == {}


def test_validator_rejects_the_records_node_without_its_entry():
    wf = walk_workflow()
    for t in wf["nodes"]["negotiate"]["transitions"]:
        if t["target_node"] == "records_entry":
            t["target_node"] = "records"
    assert any("goes straight to the records node" in m for m in report(wf)[0])
    entry = wf["nodes"]["records_entry"]
    assert entry["passthrough_default_transition"] == "records"
