"""Print what the date-facts code node will tell the phone agent, without Freya.

    python -m builder.facts out/<slot>/date_params.json --now 2026-10-14T11:00 [--products products.json]

Runs the exact code string that goes into the workflow, so what you read here is what the agent gets.
"""
import argparse
import json
import sys

from builder import snippets


def facts(params, now, products=None):
    variables = {params.get("now_var") or "now": now}
    if products is not None:
        variables[params.get("products_var") or "products"] = products
    return snippets.run_code_node(snippets.date_facts_code(params), variables)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("params")
    ap.add_argument("--now", required=True)
    ap.add_argument("--products", help="JSON file with the product list as the account tool returns it")
    a = ap.parse_args(argv)
    with open(a.params, encoding="utf-8") as f:
        params = json.load(f)
    products = None
    if a.products:
        with open(a.products, encoding="utf-8") as f:
            products = json.load(f)
    out = facts(params, a.now, products)
    print(out["date_facts"])
    print()
    print(out["product_facts"])
    return 0 if out.get("date_facts_state") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
