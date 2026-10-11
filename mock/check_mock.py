"""Smoke check of the deployed mock: every operation of the tool API file answers on the mock's address.

    python mock/check_mock.py <mock base URL, e.g. https://x.workers.dev/v1> --openapi <tool API file> --customer <id>

It sends each operation once with the customer id and placeholder values: a 200 with ok true or false, or a 500 the
mock was told to send, means the route works; 404 or a network error means it does not. It writes to the mock's
memory (a wrong answer, a callback), so run it with a test customer, never during the evaluation.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

PLACEHOLDER = {"string": "x", "number": 1, "integer": 1, "boolean": False}


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception as e:
        return None, str(e)[:200]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--openapi", required=True)
    ap.add_argument("--customer", required=True)
    a = ap.parse_args(argv)
    spec = json.load(open(a.openapi, encoding="utf-8"))
    bad = 0
    for path, methods in spec["paths"].items():
        op = next(iter(methods.values()))
        props = op["requestBody"]["content"]["application/json"]["schema"].get("properties", {})
        body = {k: (v.get("enum") or [PLACEHOLDER.get(v.get("type"), "x")])[0] for k, v in props.items()}
        body["customer_id"] = a.customer
        status, text = post(a.base.rstrip("/") + path, body)
        works = status in (200, 500)
        bad += not works
        print(("OK  " if works else "BAD ") + path, status, text)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
