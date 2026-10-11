"""The account must load before the call goes on (raw-a2 PUB-07: get_account never answered, the agent made up the
product id and the amount and the bank refused the promise)."""
from builder import blocks as b
from builder import openapi_tools as ot
from builder import snippets
from builder.facts import facts
from builder.validate import validate
from tests.conftest import no_attempts

GUARD = snippets.promise_guard_code({"check_dates": True, "check_amount": True})
CID = {"customer_id": "{{customer_id}}"}
MAP = [{"path": "data.products", "variable": "products"}]


def test_facts_with_no_products_count_zero(params_a):
    v = facts(no_attempts(params_a), "2026-10-14T11:00", [])
    assert v["product_count"] == "0"


def test_guard_blocks_a_promise_when_the_account_did_not_load():
    v = {"product_count": "0"}
    out = snippets.run_pre_hook(GUARD, {"product_id": "CC_12345", "amount": 1250, "date": "2026-10-27"}, v)
    assert out["product_id"] == ""
    assert v["promise_guard"].startswith("NOT RECORDED") and "did not load" in v["promise_guard"]


def test_guard_without_facts_still_lets_the_bank_judge():
    v = {}
    out = snippets.run_pre_hook(GUARD, {"product_id": "p1", "amount": 10, "date": "2026-10-27"}, v)
    assert out["product_id"] == "p1" and v["promise_guard"] == ""


def test_account_load_goes_on_only_with_a_product_and_tries_once_more(openapi, params_a):
    code = snippets.date_facts_code(no_attempts(params_a))
    nodes = b.account_load("load", ot.api_tool(openapi, "get_account", baked=CID), "facts", code, "work", "failed",
                           mapping=MAP)
    by = {n["name"]: n for n in nodes}
    assert list(by) == ["load", "facts", "load_retry", "facts_retry"]
    assert by["load"]["passthrough_default_transition"] == "facts" and by["load"]["response_mapping"] == MAP
    for facts_name, fallback in (("facts", "load_retry"), ("facts_retry", "failed")):
        first, last = by[facts_name]["transitions"]
        assert first["target_node"] == "work"
        assert first["condition"]["conditions"][0] == {"logic": "and", "conditions": [
            {"variable": "product_count", "operator": "is_set"},
            {"variable": "product_count", "operator": "not_equals", "operand": "0"}]}
        assert last["target_node"] == fallback and last["condition"]["operator"] == "always"
    assert by["load_retry"]["passthrough_default_transition"] == "facts_retry"


def _wf(openapi, params_a, nodes):
    close = ot.api_tool(openapi, "close_call", baked=CID, pre_hook=b.close_pre_hook(),
                        post_hook=b.close_post_hook(snippets))
    work = b.conversation("work", "Talk.", tools=[close], transitions=[b.closed_transition("end")], max_turns=10,
                          max_turn_target="end")
    failed = b.conversation("failed", "Say goodbye and close.", tools=[close], transitions=[b.closed_transition("end")],
                            max_turns=4, max_turn_target="end")
    return b.workflow("load", [*nodes, work, failed, b.end_node("end")])


def _warned(rep):
    return [i for i in rep.items if i[0] == "WARN" and "product_count" in i[2]]


def test_validator_warns_on_an_unchecked_account_load(openapi, params_a):
    code = snippets.date_facts_code(no_attempts(params_a))
    tool = ot.api_tool(openapi, "get_account", baked=CID)
    plain = [b.api_node("load", tool, "facts", MAP), b.code_node("facts", code, "work")]
    assert _warned(validate(_wf(openapi, params_a, plain)))
    checked = b.account_load("load", tool, "facts", code, "work", "failed", mapping=MAP)
    rep = validate(_wf(openapi, params_a, checked))
    assert not _warned(rep) and not rep.errors
