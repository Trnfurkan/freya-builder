"""Smoke check of the deployed mock, with the requests the agent's tools will send.

    python mock/check_mock.py <mock base URL> --openapi <tool API file> --value customerNumber=<test value> [--value k=v]

Each operation of the tool API file is built with builder/openapi_tools.py (the same url, method, path and query
parameters as the workflow's tools) and sent once. Fields given with --value are used; the others get a placeholder.
A 404 or a network error means the route is wrong; any other status is the mock's own answer, printed to compare with
the bank's listed answers. It may write to the mock's memory, so never run it during the evaluation.
"""
import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from builder import openapi_tools as ot  # noqa: E402

PLACEHOLDER = {"string": "x", "number": 1, "integer": 1, "boolean": False}


def send(method, url, body):
    data = json.dumps(body).encode() if body and method not in ("GET", "DELETE") else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return None, str(e)[:300]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--openapi", required=True)
    ap.add_argument("--value", action="append", default=[], help="field=value used in every request that has it")
    a = ap.parse_args(argv)
    os.environ[ot.MOCK_URL_VAR] = a.base.rstrip("/")
    spec = ot.load(a.openapi)
    given = dict(v.split("=", 1) for v in a.value)
    bad = 0
    for name in ot.tool_names(spec):
        tool = ot.api_tool(spec, name)
        props = tool["function"]["parameters"]["properties"]
        method, url = props["method"]["default"], props["url"]["default"]
        body = {k: given.get(k, (v.get("enum") or [PLACEHOLDER.get(v.get("type"), "x")])[0])
                for k, v in props["body"]["properties"].items()}
        config = {"url": url, "method": method, "headers": {}, "body": body}
        if tool.get("pre_process_code"):
            exec(tool["pre_process_code"], {"vars": dict(), "config": config})
        status, text = send(config["method"], config["url"], config["body"])
        works = status not in (None, 404)
        bad += not works
        print(("OK  " if works else "BAD ") + f"{name} {config['method']} {config['url']} -> {status} {text}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
