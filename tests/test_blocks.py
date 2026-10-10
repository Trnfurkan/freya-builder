import pytest

from builder import blocks as b
from builder import openapi_tools as ot
from builder import snippets
from builder.validate import validate
from tests.conftest import no_attempts


def test_conditions_shapes():
    t = b.go("next", "verified", b.is_true("verify_identity_response.data.all_verified"))
    assert t["condition"]["logic"] == "or"
    assert [c["operand"] for c in t["condition"]["conditions"]] == ["true", "True"]
    t = b.go("next", "both", b.all_of(b.is_set("x"), b.is_true("y")))
    group = t["condition"]["conditions"][0]
    assert group["logic"] == "and"
    assert group["conditions"][0] == {"variable": "x", "operator": "is_set"}
    assert group["conditions"][1]["logic"] == "or"
    with pytest.raises(ValueError):
        b.go("next", "nothing")


def test_passthrough_nodes_always_end_with_always():
    n = b.code_node("setup", "x = 1", "talk", transitions=[b.go("err", "failed", b.gt("setup_failure_count", 0))])
    assert n["transitions"][-1] == b.always("talk")
    assert n["passthrough"] and n["passthrough_default_transition"] == "talk"
    assert n["pre_actions"][0]["function"]["handler"] == "__function__:code_exec"


def test_api_node_moves_mapping_to_the_node(openapi):
    tool = ot.api_tool(openapi, "get_account", baked={"customer_id": "{{customer_id}}"},
                       response_mapping=[{"path": "data.ok", "variable": "account_ok"}])
    n = b.api_node("load", tool, "facts", mapping=[{"path": "data.products", "variable": "products"}])
    assert "response_mapping" not in n["pre_actions"][0]
    assert n["response_mapping"] == [{"path": "data.products", "variable": "products"},
                                     {"path": "data.ok", "variable": "account_ok"}]
    assert "response_mapping" in tool   # the caller's tool is not changed


def test_end_node_modes():
    assert b.end_node("bye")["terminal"]["function"]["parameters"]["properties"]["message_mode"]["default"] == "none"
    n = b.end_node("bye", spoken="Goodbye.", function_name="closed")
    props = n["terminal"]["function"]["parameters"]["properties"]
    assert props["message_mode"]["default"] == "custom" and props["spoken_message"]["default"] == "Goodbye."
    assert n["terminal"]["function"]["name"] == "closed" and n["on_enter"] == {"type": "silent"}


def test_global_node_passthrough_with_fixed_line():
    g = b.global_node("robot", {"operator": "matches_intent", "description": "asks if it is a robot"},
                      "Say what you are.", on_enter=b.say("I am the bank's digital assistant."), excluded=["bye"])
    assert g["passthrough"] and g["passthrough_default_transition"] == "__previous__"
    g2 = b.global_node("other", {"operator": "matches_intent", "description": "x"}, "Answer.")
    assert "passthrough" not in g2


def test_duplicate_nodes_rejected():
    with pytest.raises(ValueError):
        b.workflow("a", [b.end_node("a"), b.end_node("a")])


def test_full_workflow_from_blocks_validates(openapi, params_a):
    facts = snippets.date_facts_code(no_attempts(params_a))
    cid = {"customer_id": "{{customer_id}}"}
    verify = ot.api_tool(openapi, "verify_identity", baked=cid,
                         pre_hook=snippets.normalize_answer_code({"field_key": "field", "answer_key": "answer",
                                                                  "date_fields": ["date_of_birth"], "day_first": True}),
                         post_hook=snippets.post_result_code("Next question.", "Say the check failed.",
                                                             [["verified", "false", "Ask again."]]))
    close = ot.api_tool(openapi, "close_call", baked=cid, enums={"outcome": ["not_verified", "not_reached"]})
    wf = b.workflow("setup", [
        b.code_node("setup", facts, "identity"),
        b.conversation("identity", "Check identity. {{date_facts}}", tools=[verify, close],
                       on_enter=b.say("Hello. Am I speaking with {{title}} {{last_name}}?"), first_entry_only=True,
                       transitions=[b.go("load_account", "verified", b.is_true("verify_identity_response.data.all_verified")),
                                    b.go("bye", "closed", b.gt("close_call_success_count", 0))],
                       max_turns=40, max_turn_target="bye"),
        b.api_node("load_account", ot.api_tool(openapi, "get_account", baked=cid), "facts",
                   mapping=[{"path": "data.products", "variable": "products"}]),
        b.code_node("facts", facts, "negotiate"),
        b.conversation("negotiate", "Facts: {{product_facts}}",
                       tools=[ot.api_tool(openapi, n, baked=cid) for n in ("create_promise", "add_note", "transfer_to_agent", "close_call")],
                       transitions=[b.go("bye", "closed", b.gt("close_call_success_count", 0))],
                       max_turns=60, max_turn_target="bye"),
        b.end_node("bye"),
    ], system_prompt="You are a bank's digital assistant.",
       global_nodes=[b.global_node("robot", {"operator": "matches_intent", "description": "asks if it is a robot"},
                                   "Say what you are.", on_enter=b.say("I am a digital assistant."), excluded=["bye", "setup"])])
    rep = validate(wf, openapi, ["customer_id", "first_name", "last_name", "title", "now"])
    assert rep.errors == [], rep.text()
    unused = [m for _, _, m in rep.items if "never used" in m]
    assert len(unused) == 1 and "schedule_callback" in unused[0]   # left out on purpose: the validator must say so


def test_closed_transition_fires_on_any_sign_of_an_answer():
    t = b.closed_transition("end")
    conds = t["condition"]["conditions"]
    assert t["target_node"] == "end" and t["condition"]["logic"] == "or"
    assert {"variable": "close_call_success_count", "operator": "greater_than", "operand": 0} in conds
    assert {"variable": "close_call_response.data.ok", "operator": "is_set"} in conds
    assert {"variable": "close_call_failure_count", "operator": "greater_than", "operand": 1} in conds
    assert {"variable": "call_closed", "operator": "equals", "operand": "yes"} in conds


def test_close_post_hook_sets_the_flag_and_asks_for_one_word():
    code = b.close_post_hook(snippets)
    v = {}
    r = snippets.run_post_hook(code, {"status": "success", "data": {"ok": True}, "status_code": 200}, v)
    assert v == {"call_closed": "yes"} and r["data"]["what_to_do_next"].endswith("Goodbye.")
    v = {}
    r = snippets.run_post_hook(code, {"status": "success", "data": {"ok": False}, "status_code": 200}, v)
    assert v == {} and "once more" in r["data"]["what_to_do_next"]


def test_wrap_up_node_calls_the_closing_tool_before_ending(openapi):
    close = ot.api_tool(openapi, "close_call", baked={"customer_id": "{{customer_id}}"})
    n = b.wrap_up_node("wrap_up", close, "end")
    assert [t["function"]["name"] for t in n["tools"]] == ["close_call"]
    assert n["on_enter"]["type"] == "llm" and "close_call" in n["task_message"]
    assert n["transitions"][0]["target_node"] == "end" and n["max_turn_transition"] == "end"


def test_validator_warns_when_a_turn_cap_skips_the_closing_tool(openapi):
    close = ot.api_tool(openapi, "close_call", baked={"customer_id": "{{customer_id}}"})
    def wf(cap_target):
        return b.workflow("talk", [
            b.conversation("talk", "Talk.", tools=[ot.api_tool(openapi, "verify_identity"), close],
                           transitions=[b.closed_transition("end")],
                           max_turns=30, max_turn_target=cap_target),
            b.wrap_up_node("wrap_up", close, "end"),
            b.end_node("end")])
    bad = [m for _, _, m in validate(wf("end"), openapi).items if "turn cap goes straight" in m]
    good = [m for _, _, m in validate(wf("wrap_up"), openapi).items if "turn cap goes straight" in m]
    assert len(bad) == 1 and good == []


def test_recommended_pattern_validates_without_closing_warnings(openapi, params_a):
    cid = {"customer_id": "{{customer_id}}"}
    facts = snippets.date_facts_code(no_attempts(params_a))
    close = ot.api_tool(openapi, "close_call", baked=cid, post_hook=b.close_post_hook(snippets))
    promise = ot.api_tool(openapi, "create_promise", baked=cid,
                          pre_hook=snippets.promise_guard_code({"check_dates": True, "check_amount": True}),
                          post_hook=snippets.post_result_code("Recorded.", "Not recorded.", guard_var="promise_guard"))
    wf = b.workflow("setup", [
        b.code_node("setup", facts, "identity"),
        b.conversation("identity", "Check identity.", tools=[ot.api_tool(openapi, "verify_identity", baked=cid), close],
                       transitions=[b.closed_transition("end"),
                                    b.go("load", "verified", b.is_true("verify_identity_response.data.all_verified"))],
                       max_turns=40, max_turn_target="wrap_up"),
        b.api_node("load", ot.api_tool(openapi, "get_account", baked=cid), "facts",
                   mapping=[{"path": "data.products", "variable": "products"}]),
        b.code_node("facts", facts, "negotiate"),
        b.conversation("negotiate", "{{product_facts}}", tools=[promise, close],
                       transitions=[b.closed_transition("end")], max_turns=60, max_turn_target="wrap_up"),
        b.wrap_up_node("wrap_up", close, "end"),
        b.end_node("end"),
    ])
    rep = validate(wf, openapi, ["customer_id", "now"])
    assert rep.errors == [], rep.text()
    assert not [m for _, _, m in rep.items if "turn cap" in m or "is used in a prompt" in m], rep.text()


def test_failure_rules_with_engine_retries():
    text = b.failure_rules("create_promise", "I could not record the promise.", engine_retries=1,
                           then="close with the brief's outcome for a service failure.")
    assert "nothing was recorded" in text and "Never describe a technical error" in text
    assert "already repeated a failed create_promise call 1 time(s)" in text
    assert 'say exactly: "I could not record the promise."' in text and "Then close with" in text


def test_failure_rules_without_engine_retries_asks_for_one_retry_with_a_holding_line():
    text = b.failure_rules("create_promise", "I could not record the promise.")
    assert 'say only "One moment, please." and, in the same message, call create_promise again with exactly the same values' in text
    assert 'If it fails again, say exactly: "I could not record the promise."' in text
    assert "{{" not in text


def test_outcome_after_a_transfer_pattern_validates(openapi):
    cid = {"customer_id": "{{customer_id}}"}
    others = ["promise", "paid", "no_promise", "not_verified", "not_reached"]
    close_work = ot.api_tool(openapi, "close_call", baked=cid, enums={"outcome": others}, post_hook=b.close_post_hook(snippets))
    close_xfer = ot.api_tool(openapi, "close_call", baked=cid, enums={"outcome": ["transferred"]},
                             post_hook=b.close_post_hook(snippets))
    work = b.conversation("negotiate", "Talk.", tools=[ot.api_tool(openapi, "transfer_to_agent", baked=cid), close_work],
                          transitions=[b.go("after_transfer", "transfer done", b.gt("transfer_to_agent_success_count", 0)),
                                       b.closed_transition("end")],
                          max_turns=40, max_turn_target="wrap_up")
    after = b.outcome_after("after_transfer", "transfer_to_agent", close_xfer, "end")
    wf = b.workflow("negotiate", [work, after, b.wrap_up_node("wrap_up", close_work, "end"), b.end_node("end")])
    assert "transferred" not in work["tools"][1]["function"]["parameters"]["properties"]["body"]["properties"]["outcome"]["enum"]
    assert after["tools"][0]["function"]["parameters"]["properties"]["body"]["properties"]["outcome"]["enum"] == ["transferred"]
    assert after["on_enter"]["type"] == "llm" and after["max_turn_transition"] == "end"
    rep = validate(wf, openapi)
    assert rep.errors == [], rep.text()
