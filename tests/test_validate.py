import copy
import json

import pytest

from builder import openapi_tools as ot
from builder import snippets
from builder.validate import validate
from tests.conftest import KIT


def end_node(name):
    return {"name": name, "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
            "transitions": [], "terminal": {"type": "function", "function": {"name": name, "handler": "__function__:end_call",
                                                                              "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}}}


@pytest.fixture
def wf(openapi, params_a):
    always = {"operator": "always"}
    return {
        "initial_node": "setup",
        "system_prompt": "You are a bank's digital assistant.",
        "global_nodes": {},
        "nodes": {
            "setup": {"name": "setup", "node_type": "code", "passthrough": True, "passthrough_default_transition": "talk",
                      "on_enter": {"type": "silent"},
                      "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                                                                          "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": snippets.date_facts_code(params_a)}}}}}],
                      "transitions": [{"target_node": "talk", "description": "always", "condition": always}]},
            "talk": {"name": "talk", "node_type": "conversation", "task_message": "Facts: {{date_facts}}. Customer {{first_name}}.",
                     "tools": [ot.api_tool(openapi, "close_call", baked={"customer_id": "{{customer_id}}"},
                                           post_hook=snippets.post_result_code("Say goodbye.", "Try again."))],
                     "max_node_turns": 40, "max_turn_transition": "bye",
                     "transitions": [{"target_node": "bye", "description": "closed", "condition": {"logic": "or", "conditions": [
                         {"variable": "close_call_success_count", "operator": "greater_than", "operand": 0}]}}]},
            "bye": end_node("bye"),
        },
    }


def errors(rep):
    return [m for lvl, _, m in rep.items if lvl == "ERROR"]


def test_good_workflow_has_no_errors(wf, openapi):
    rep = validate(wf, openapi, ["customer_id", "first_name", "last_name", "title", "now"])
    assert errors(rep) == [], rep.text()
    assert not any("is used in a prompt" in m for _, _, m in rep.items), rep.text()


def test_kit_example_agent_has_no_errors(openapi):
    base = json.loads((KIT / "examples" / "base-agent.json").read_text(encoding="utf-8"))
    rep = validate(base, openapi)
    assert errors(rep) == [], rep.text()
    assert any("never used" in m for _, _, m in rep.items)   # it uses four of the seven tools


@pytest.mark.parametrize("mutate, expect", [
    (lambda w: w["nodes"]["setup"].update(tools=[{"function": {"name": "x", "handler": "__function__:code_exec"}}]), "non-conversation"),
    (lambda w: w["nodes"]["setup"]["transitions"].pop(), "always"),
    (lambda w: w["nodes"]["talk"]["transitions"][0].update(target_node="nowhere"), "does not exist"),
    (lambda w: w.update(initial_node="nope"), "initial_node"),
    (lambda w: w["nodes"]["talk"]["tools"][0].update(post_process_code="x = '{{a}}'"), "double brace"),
    (lambda w: w["nodes"]["talk"]["tools"][0].update(post_process_code="def ("), "syntax"),
    (lambda w: w["nodes"]["talk"]["tools"][0]["function"]["parameters"]["properties"]["url"].update(default="https://evil.example/close_call"), "tool API says"),
    (lambda w: w["nodes"]["talk"]["tools"][0]["function"]["parameters"]["properties"]["body"]["properties"].update(discount={"type": "number", "default": "{{discount}}"}), "not in the tool API"),
    (lambda w: w["nodes"]["talk"]["tools"][0]["function"]["parameters"]["properties"]["body"]["properties"]["outcome"].update(enum=["promise", "won"]), "not allowed"),
    (lambda w: w["nodes"]["talk"]["tools"][0]["function"].update(name="send_sms"), "not in the tool API"),
    (lambda w: w["nodes"]["talk"]["transitions"][0].update(condition={"logic": "and", "conditions": [{"operator": "always"}]}), "logic"),
    (lambda w: w.update(nodes={k: v for k, v in w["nodes"].items() if k != "bye"}), "does not exist"),
    (lambda w: w["nodes"]["talk"].update(extraction_schema=[{"name": "d", "type": "date", "min_confidence": 0.7, "extraction_prompt": "Find the date."}]), "extractor placeholders"),
    (lambda w: w.update(tools=[{"type": "function", "function": {"name": "close_call", "handler": "__function__:api_call"}}]), "alwaysRunsAt"),
])
def test_broken_workflows_are_caught(wf, openapi, mutate, expect):
    w = copy.deepcopy(wf)
    mutate(w)
    msgs = errors(validate(w, openapi))
    assert any(expect in m for m in msgs), msgs


def test_warnings(wf, openapi):
    w = copy.deepcopy(wf)
    w["nodes"]["talk"]["task_message"] += " {{dat_facts_typo}}"
    w["nodes"]["talk"]["transitions"][0]["condition"]["conditions"].append({"variable": "v", "operator": "equals", "operand": "true"})
    w["nodes"]["talk"]["max_turn_transition"] = None
    msgs = [m for _, _, m in validate(w, openapi).items]
    assert any("dat_facts_typo" in m for m in msgs)
    assert any("'True'" in m for m in msgs)
    assert any("null value" in m for m in msgs)
