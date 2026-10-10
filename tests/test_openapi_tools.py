import pytest

from builder import openapi_tools as ot


def test_names_and_url(openapi):
    assert ot.tool_names(openapi) == ["verify_identity", "get_account", "create_promise", "add_note",
                                      "schedule_callback", "transfer_to_agent", "close_call"]
    assert ot.base_url(openapi) == "https://freya-bank-mock.freyavoice.workers.dev/v1"


def test_verify_identity_tool(openapi):
    t = ot.api_tool(openapi, "verify_identity", baked={"customer_id": "{{customer_id}}"},
                    description="CALL it every time the customer answers a security question.")
    p = t["function"]["parameters"]["properties"]
    assert p["url"]["default"] == "https://freya-bank-mock.freyavoice.workers.dev/v1/verify_identity"
    assert p["method"]["default"] == "POST"
    body = p["body"]
    assert body["properties"]["customer_id"] == {"type": "string", "default": "{{customer_id}}"}
    assert body["properties"]["field"]["enum"] == ["date_of_birth", "phone_last4"]
    assert "One of: date_of_birth, phone_last4." in body["properties"]["field"]["description"]
    assert body["properties"]["answer"]["default"] == "{{answer}}"
    assert body["required"] == ["field", "answer"]
    assert t["config"] == {"timeoutMs": 12000, "maxRetries": 0}


def test_close_call_keeps_all_outcomes_and_can_narrow(openapi):
    t = ot.api_tool(openapi, "close_call", baked={"customer_id": "{{customer_id}}"})
    leaf = t["function"]["parameters"]["properties"]["body"]["properties"]["outcome"]
    assert leaf["enum"] == ["promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"]
    narrow = ot.api_tool(openapi, "close_call", enums={"outcome": ["not_reached"]})
    assert narrow["function"]["parameters"]["properties"]["body"]["properties"]["outcome"]["enum"] == ["not_reached"]
    with pytest.raises(ValueError):
        ot.api_tool(openapi, "close_call", enums={"outcome": ["made_up"]})


def test_date_fields_get_format_hint(openapi):
    t = ot.api_tool(openapi, "create_promise", baked={"customer_id": "{{customer_id}}"})
    assert "YYYY-MM-DD" in t["function"]["parameters"]["properties"]["body"]["properties"]["date"]["description"]


def test_unknown_tool_or_field_is_refused(openapi):
    with pytest.raises(KeyError):
        ot.api_tool(openapi, "send_sms")
    with pytest.raises(KeyError):
        ot.api_tool(openapi, "create_promise", baked={"discount": 10})


def test_hooks_and_mapping_attached(openapi):
    t = ot.api_tool(openapi, "get_account", baked={"customer_id": "{{customer_id}}"}, post_hook="x = 1",
                    response_mapping=[{"path": "data.products", "variable": "products"}])
    assert t["post_process_code"] == "x = 1"
    assert t["response_mapping"] == [{"path": "data.products", "variable": "products"}]
