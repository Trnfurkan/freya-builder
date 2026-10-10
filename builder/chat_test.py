"""Talk to the published workflow turn by turn (docs/api.md, "Talk to your workflow turn by turn").

    python -m builder.chat_test <workflow_id> --vars vars.json "Yes, speaking." "14 March 1988" ...

Each argument after the options is one customer turn. Prints what the agent said after each turn.
This makes real API calls; if the call variables hold a real customer_id, the agent's tool calls reach the bank.
Seen in the docs, not guaranteed: a long turn can return 504, and a first code node may be skipped in chat.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import freya  # noqa: E402


def chat(workflow_id, variables, turns):
    call_id, log = None, []
    try:
        for text in turns:
            body = {"workflowId": workflow_id, "message": text, "customerVariables": variables}
            if call_id:
                body["callId"] = call_id
            r = freya.api("POST", "/api/v2/chat/message", body)
            call_id = r.get("callId") or call_id
            log.append((text, r.get("response", "")))
            print(f"CUSTOMER: {text}\nAGENT:    {r.get('response', '')}\n", flush=True)
    finally:
        if call_id:
            try:
                freya.api("POST", "/api/v2/chat/end", {"callId": call_id})
            except Exception as e:
                print(f"(could not end the chat: {e})")
    return call_id, log


def main(argv=None):
    from builder.practice import use_system_certificates
    use_system_certificates()
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow_id")
    ap.add_argument("--vars", required=True, help="JSON file with the call variables")
    ap.add_argument("turns", nargs="+")
    a = ap.parse_args(argv)
    with open(a.vars, encoding="utf-8") as f:
        variables = json.load(f)
    call_id, _ = chat(a.workflow_id, variables, a.turns)
    print(f"call id: {call_id}  (transcript with tool calls: freya.get_call(call_id)['messages'])")


if __name__ == "__main__":
    main()
