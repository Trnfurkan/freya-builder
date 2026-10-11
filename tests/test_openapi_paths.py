"""Tool API files whose operations have their own paths, an operationId, GET and path or query parameters: a bank's
integration document need not follow the kit's POST /<name> shape, and the team's mock must use the bank's paths."""
import json

from builder import openapi_tools as ot
from builder import snippets
from builder.validate import validate
from builder import blocks as b

SPEC = {
    "servers": [{"url": "https://bank.example/api/"}],
    "paths": {
        "/customers/{customerNumber}/cards": {"get": {
            "operationId": "listCards", "summary": "The customer's cards.",
            "parameters": [{"name": "customerNumber", "in": "path", "required": True, "schema": {"type": "string"}},
                           {"name": "lang", "in": "query", "schema": {"type": "string", "enum": ["tr", "en"]}}],
            "responses": {"200": {"content": {"application/json": {"schema": {"type": "object", "properties": {
                "cards": {"type": "array"}}}}}}}}},
        "/statements/email": {"post": {
            "operationId": "sendStatementEmail", "summary": "E-mail the statement.",
            "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {
                "customerNumber": {"type": "string"}, "cardId": {"type": "string"}},
                "required": ["customerNumber", "cardId"]}}}},
            "responses": {"200": {"description": "ok"}}}},
    },
}


def test_names_come_from_operation_ids():
    assert ot.tool_names(SPEC) == ["listCards", "sendStatementEmail"]
    assert ot.operation(SPEC, "listCards")[0] == "GET"


def test_kit_shape_is_unchanged(openapi):
    assert "verify_identity" in ot.tool_names(openapi)
    t = ot.api_tool(openapi, "get_account", baked={"customer_id": "{{customer_id}}"})
    assert t["function"]["parameters"]["properties"]["url"]["default"] == ot.base_url(openapi) + "/get_account"
    assert "pre_process_code" not in t


def test_path_and_query_parameters_go_into_the_url():
    t = ot.api_tool(SPEC, "listCards", baked={"customerNumber": "{{customerNumber}}"})
    props = t["function"]["parameters"]["properties"]
    assert props["url"]["default"] == "https://bank.example/api/customers/{customerNumber}/cards?lang={lang}"
    assert props["method"]["default"] == "GET"
    assert props["body"]["properties"]["customerNumber"]["default"] == "{{customerNumber}}"
    assert props["body"]["properties"]["lang"]["enum"] == ["tr", "en"]
    code = t["pre_process_code"]
    assert "{{" not in code and "}}" not in code
    config = {"url": props["url"]["default"], "method": "GET", "headers": {},
              "body": {"customerNumber": "12345678", "lang": "tr"}}
    exec(code, {"vars": {}, "config": config})
    assert config["url"] == "https://bank.example/api/customers/12345678/cards?lang=tr" and config["body"] == {}


def test_url_values_are_encoded():
    t = ot.api_tool(SPEC, "listCards", baked={"customerNumber": "{{customerNumber}}"})
    config = {"url": t["function"]["parameters"]["properties"]["url"]["default"], "method": "GET", "headers": {},
              "body": {"customerNumber": "12 34/ş", "lang": "tr"}}
    exec(t["pre_process_code"], {"vars": {}, "config": config})
    assert config["url"].startswith("https://bank.example/api/customers/12%2034%2F%C5%9F/cards")


def test_the_callers_hook_runs_first_and_post_bodies_stay():
    first = "try:\n    config['body']['cardId'] = str(config['body'].get('cardId', '')).strip()\nexcept Exception:\n    pass\n"
    t = ot.api_tool(SPEC, "sendStatementEmail", baked={"customerNumber": "{{customerNumber}}"}, pre_hook=first)
    assert t["function"]["parameters"]["properties"]["url"]["default"] == "https://bank.example/api/statements/email"
    assert t.get("pre_process_code") == first
    out = snippets.run_pre_hook(t["pre_process_code"], {"customerNumber": "1", "cardId": " c1 "})
    assert out == {"customerNumber": "1", "cardId": "c1"}


def test_validator_accepts_operation_paths():
    get = ot.api_tool(SPEC, "listCards", baked={"customerNumber": "{{customerNumber}}"})
    send = ot.api_tool(SPEC, "sendStatementEmail", baked={"customerNumber": "{{customerNumber}}"})
    wf = b.workflow("talk", [
        b.conversation("talk", "Help.", tools=[get, send], transitions=[b.go("end", "done", b.eq("x", "y"))],
                       max_turns=5, max_turn_target="end"),
        b.end_node("end")])
    rep = validate(wf, SPEC)
    assert not [i for i in rep.errors if "url" in i[2] or "not in the tool API" in i[2]], rep.text()


NESTED = {
    "servers": [{"url": "https://bank.example"}],
    "paths": {"/svc/v1/things.list": {"post": {
        "operationId": "things.list",
        "requestBody": {"content": {"application/json": {"schema": {"type": "object", "required": ["data"],
            "properties": {
                "data": {"type": "object", "required": ["ownerNo"], "properties": {
                    "ownerNo": {"type": "string"}, "flow": {"type": "string", "enum": ["A", "B"]}}},
                "call": {"type": "object", "properties": {"callId": {"type": "string"}}}}}}}},
        "responses": {"200": {"description": "ok"}}}}},
}


def test_nested_body_fields_become_leaves_and_go_back_into_objects():
    props, required = ot.body_schema(NESTED, "things.list")
    assert set(props) == {"data__ownerNo", "data__flow", "call__callId"} and required == ["data__ownerNo"]
    t = ot.api_tool(NESTED, "things.list", baked={"data__ownerNo": "{{customerNumber}}", "data__flow": "A",
                                                   "call__callId": "{{call_id}}"})
    leaves = t["function"]["parameters"]["properties"]["body"]["properties"]
    assert leaves["data__ownerNo"]["default"] == "{{customerNumber}}" and leaves["data__flow"]["enum"] == ["A", "B"]
    assert t["function"]["parameters"]["properties"]["url"]["default"] == "https://bank.example/svc/v1/things.list"
    code = t["pre_process_code"]
    assert "{{" not in code
    out = snippets.run_pre_hook(code, {"data__ownerNo": "12345678", "data__flow": "A", "call__callId": "c1"})
    assert out == {"data": {"ownerNo": "12345678", "flow": "A"}, "call": {"callId": "c1"}}


def test_nested_body_runs_after_the_callers_hook():
    first = "try:\n    config['body']['data__ownerNo'] = str(config['body']['data__ownerNo']).strip()\nexcept Exception:\n    pass\n"
    t = ot.api_tool(NESTED, "things.list", pre_hook=first)
    assert t["pre_process_code"].startswith(first)
    out = snippets.run_pre_hook(t["pre_process_code"], {"data__ownerNo": " 1 ", "data__flow": "B"})
    assert out == {"data": {"ownerNo": "1", "flow": "B"}}


def test_validator_accepts_nested_leaves():
    t = ot.api_tool(NESTED, "things.list", baked={"data__ownerNo": "{{customerNumber}}"})
    wf = b.workflow("talk", [b.conversation("talk", "Help.", tools=[t], transitions=[b.go("end", "done", b.eq("x", "y"))],
                                            max_turns=5, max_turn_target="end"), b.end_node("end")])
    rep = validate(wf, NESTED)
    assert not [i for i in rep.errors if "things.list" in i[2] or "body" in i[2] or "url" in i[2]], rep.text()
