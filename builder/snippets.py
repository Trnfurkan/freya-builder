"""Turn the files in builder/sandbox/ into code strings for a workflow.

Each snippet gets its parameters as one line `PARAMS = json.loads("...")` in front of it.
Braces inside that line are written as \\u007b and \\u007d, so the code never contains a
double brace (docs/tools-and-code.md, "Rules for snippets").
"""
import json
import pathlib

SANDBOX = pathlib.Path(__file__).resolve().parent / "sandbox"


def build(name, params):
    """Code string for sandbox/<name>.py with `params` baked in."""
    literal = json.dumps(json.dumps(params, ensure_ascii=True))
    literal = literal.replace("{", "\\u007b").replace("}", "\\u007d")
    code = "import json\nPARAMS = json.loads(" + literal + ")\n" + (SANDBOX / f"{name}.py").read_text(encoding="utf-8")
    assert "{{" not in code and "}}" not in code, f"snippet {name} contains a double brace"
    compile(code, name, "exec")
    return code


def date_facts_code(params):
    """Code for a `code` node: writes date_facts, product_facts, deadline_<band>, deadline_<product_id>, ..."""
    return build("date_facts", params)


def normalize_answer_code(params):
    """pre_process_code for the identity tool: dates to YYYY-MM-DD, spoken digits to digits."""
    return build("normalize_answer", params)


def post_result_code(on_success="", on_failure="", when=None, *, flag_false=True, guard_var="", set_on_success=None,
                     set_on_any=None, fail_count_var="", fail_limit=2, on_failure_final=""):
    """post_process_code: drops headers, adds `what_to_do_next` to the tool result (keys in sandbox/post_result.py)."""
    return build("post_result", {"on_success": on_success, "on_failure": on_failure, "when": when or [],
                                 "flag_false": flag_false, "guard_var": guard_var,
                                 "set_on_success": set_on_success or {}, "set_on_any": set_on_any or {},
                                 "fail_count_var": fail_count_var, "fail_limit": fail_limit,
                                 "on_failure_final": on_failure_final})


def promise_guard_code(params):
    """pre_process_code for the promise tool: blocks a date or amount the account's rules do not allow."""
    return build("promise_guard", params)


# Local runners: execute a snippet the way the platform does, for tests and for checking facts.

def run_code_node(code, variables):
    variables = dict(variables)
    exec(code, {"vars": variables})
    return variables


def run_pre_hook(code, body, variables=None):
    """Runs a pre-hook. `variables` (if given) is changed in place, as the platform's vars would be."""
    config = {"url": "", "method": "POST", "headers": {}, "body": dict(body)}
    exec(code, {"vars": variables if variables is not None else {}, "config": config})
    return config["body"]


def run_post_hook(code, result, variables=None):
    """Runs a post-hook. `variables` (if given) is changed in place."""
    result = json.loads(json.dumps(result))
    exec(code, {"vars": variables if variables is not None else {}, "result": result})
    return result
