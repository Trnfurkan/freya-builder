"""Check a workflow JSON before it goes to Freya.

    python -m builder.validate out/brief-a/workflow.json tools/openapi.json [--call-vars customer_id,first_name,...]

Errors are things the docs say break a workflow or get silently dropped by the platform.
Warnings are risks worth a look. Exit code 1 when there is an error.
Sources: docs/workflow-format.md, docs/tools-and-code.md (see their checklists).
"""
import argparse
import json
import re
import sys

from builder import openapi_tools

NODE_TYPES = {"conversation", "api_request", "code", "end_call", "transfer_call", "integration",
              "knowledge_base", "reschedule_call", "invoke_agent", "exit"}
ACTION_TYPES = NODE_TYPES - {"conversation", "end_call"}
OPERATORS = {"equals", "not_equals", "greater_than", "less_than", "contains", "is_set", "is_not_set", "always", "matches_intent"}
EXTRACTOR_PLACEHOLDERS = ("{{extractor.speech}}", "{{extractor.conversation.last-10}}", "{{extractor.type_preamble}}", "{{extractor.variables_list}}")
PLACEHOLDER = re.compile(r"\{\{\s*([^}:]+?)(?::json)?\s*\}\}")
AUTO_SUFFIXES = ("_success_count", "_failure_count", "_status_code", ".current_turns", ".total_turns")


class Report:
    def __init__(self):
        self.items = []

    def error(self, where, msg):
        self.items.append(("ERROR", where, msg))

    def warn(self, where, msg):
        self.items.append(("WARN", where, msg))

    @property
    def errors(self):
        return [i for i in self.items if i[0] == "ERROR"]

    def text(self):
        return "\n".join(f"{lvl:5} {where}: {msg}" for lvl, where, msg in self.items) or "OK: no findings"


def _nulls(obj, path, rep):
    if obj is None:
        rep.warn(path, "null value (docs: the schema rejects null; leave the field out or use \"\")")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _nulls(v, f"{path}.{k}", rep)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _nulls(v, f"{path}[{i}]", rep)


def _check_code(code, where, rep):
    if not isinstance(code, str) or not code.strip():
        return
    if "{{" in code or "}}" in code:
        rep.error(where, "code contains a double brace; read values from vars instead of {{...}}")
    try:
        compile(code, where, "exec")
    except SyntaxError as e:
        rep.error(where, f"Python syntax error: {e}")


def _conditions(cond, where, rep, top=True):
    if not isinstance(cond, dict):
        rep.error(where, "condition must be an object")
        return []
    out = []
    if "conditions" in cond:
        if top and cond.get("logic") != "or":
            rep.error(where, "the top-level condition group must use \"logic\": \"or\" (nest AND groups inside it)")
        for c in cond["conditions"]:
            out += _conditions(c, where, rep, top=False)
        return out
    op = cond.get("operator")
    if op not in OPERATORS:
        rep.error(where, f"unknown operator {op!r}")
    if op in ("equals", "not_equals", "greater_than", "less_than", "contains", "is_set", "is_not_set") and not cond.get("variable"):
        rep.error(where, f"operator {op} needs a variable")
    if op == "matches_intent" and not cond.get("description"):
        rep.error(where, "matches_intent needs a description")
    out.append(cond)
    return out


def _bool_pairs(conds, where, rep):
    seen = {(c.get("variable"), str(c.get("operand"))) for c in conds if c.get("operator") == "equals"}
    for var, val in seen:
        if val in ("true", "True", "false", "False"):
            other = val.lower() if val[0].isupper() else val.capitalize()
            if (var, other) not in seen:
                rep.warn(where, f"{var} == {val!r} without {other!r}: booleans arrive as either spelling")


def _check_api_tool(tool, where, spec, rep, extraction_names):
    fn = tool.get("function", {})
    name = fn.get("name", "")
    props = fn.get("parameters", {}).get("properties", {})
    if name not in openapi_tools.tool_names(spec):
        rep.error(where, f"api_call tool {name!r} is not in the tool API ({', '.join(openapi_tools.tool_names(spec))})")
        return
    url = props.get("url", {})
    if "const" in url and "default" not in url:
        rep.error(where, "url uses const; only default is sent")
    expected = openapi_tools.base_url(spec) + "/" + name
    if url.get("default") != expected:
        rep.error(where, f"url {url.get('default')!r}, the tool API says {expected!r}")
    api_props, api_required = openapi_tools.body_schema(spec, name)
    body = props.get("body", {})
    if "properties" not in body:
        rep.error(where, "body fields must be leaves under body.properties")
        return
    leaves = body["properties"]
    hook = str(tool.get("pre_process_code") or "")
    for field in set(leaves) - set(api_props):
        if f"# strips: {field}\n" in hook + "\n":
            continue   # a model-filled helper field that the pre-hook reads and removes before sending
        rep.error(where, f"body field {field!r} is not in the tool API")
    if "def source_check(" in hook and "# strips: " not in hook:
        rep.warn(where, "promise guard without named_date_key: a date inside the allowed range is not checked against "
                        "the day the customer said (a practice agent recorded the deadline for a holiday that came before it)")
    for field in set(api_required) - set(leaves):
        rep.error(where, f"required body field {field!r} is missing")
    for field, leaf in leaves.items():
        if field not in api_props:
            continue
        if "const" in leaf and "default" not in leaf:
            rep.error(where, f"{field} uses const; only default is sent")
        allowed = api_props[field].get("enum")
        if allowed and leaf.get("enum") and set(leaf["enum"]) - set(allowed):
            rep.error(where, f"{field} enum {sorted(set(leaf['enum']) - set(allowed))} not allowed by the tool API")
        d = leaf.get("default")
        if allowed and isinstance(d, str) and not PLACEHOLDER.fullmatch(d) and d not in allowed:
            rep.error(where, f"{field} default {d!r} not allowed by the tool API")
        if isinstance(d, str) and PLACEHOLDER.fullmatch(d):
            var = PLACEHOLDER.fullmatch(d).group(1).strip()
            if var in extraction_names:
                rep.warn(where, f"{field} default {{{{{var}}}}} has the name of an extracted variable: once set it is baked and the model never fills it")
        if allowed and not leaf.get("enum") and isinstance(d, str) and PLACEHOLDER.fullmatch(d):
            rep.warn(where, f"{field} is model-filled without an enum; the tool API allows only {allowed}")


def _check_tool(tool, where, spec, rep, extraction_names):
    fn = tool.get("function", {})
    handler = fn.get("handler", "")
    for hook in ("pre_process_code", "post_process_code"):
        if tool.get(hook):
            if handler != "__function__:api_call":
                rep.error(where, f"{hook} on handler {handler}; the platform removes hooks from non-api_call tools")
            _check_code(tool[hook], f"{where}.{hook}", rep)
    if handler == "__function__:api_call" and spec is not None:
        _check_api_tool(tool, where, spec, rep, extraction_names)
    if handler == "__function__:code_exec":
        code = fn.get("parameters", {}).get("properties", {}).get("code", {}).get("default")
        _check_code(code, f"{where}.code", rep)
    for m in tool.get("response_mapping") or []:
        if not str(m.get("path", "")).startswith("data."):
            rep.warn(where, f"response_mapping path {m.get('path')!r} does not start with 'data.'")


def _written_vars(code):
    names = set(re.findall(r"vars\[\s*['\"](\w+)['\"]\s*\]", code or ""))
    prefixes = set(re.findall(r"vars\[\s*['\"](\w+)['\"]\s*\+", code or ""))
    return names, prefixes


GATE_MARK = "# offer_gate:"
MARK_MARK = "# mark_product:"
POSITION_MARK = "# mark_position:"
WALK_FLAG = '\\"walk\\": true'
FACT_VARS = ("product_facts", "date_facts", "date_lookup", "offer_plan", "date_actions", "promise_rules")


def _codes(node):
    """Every code string a node carries: code_exec defaults and tool hooks."""
    out = []
    for t in (node.get("pre_actions") or []) + (node.get("tools") or []):
        out.append(t.get("function", {}).get("parameters", {}).get("properties", {}).get("code", {}).get("default", "") or "")
        out.append(t.get("pre_process_code", "") or "")
        out.append(t.get("post_process_code", "") or "")
    return [c for c in out if c]


def policy_in_workflow(wf):
    """The policy the date facts code node carries (its PARAMS line), or None."""
    import ast
    nodes = list((wf.get("nodes") or {}).values()) + list((wf.get("global_nodes") or {}).values())
    for n in nodes:
        for c in _codes(n):
            if "attempt_offers" not in c and "deadline_business_days" not in c:
                continue
            first = c.split("\n")[1] if c.startswith("import json\nPARAMS = json.loads(") else ""
            if not first.startswith("PARAMS = json.loads("):
                continue
            try:
                return json.loads(ast.literal_eval(first[len("PARAMS = json.loads("):-1]))
            except Exception:
                continue
    return None


CLOSED_SET = '\\"set_on_success\\": \\u007b\\"call_closed\\": \\"yes\\"'


def _is_closer(tool):
    """The closing tool: its post-hook (blocks.close_post_hook) sets call_closed on success. Read from the hook's
    PARAMS, not from any text: post_result.py's own comments mention call_closed (fix20-a: every tool with a result
    hook was taken for a closing tool)."""
    return CLOSED_SET in str(tool.get("post_process_code") or "")


def _settled_flag(tool):
    """The flag a mark_product pre-hook raises when every product is settled, or ''."""
    m = re.search(r'\\"settled_flag\\": \\"(\w+)\\"', str(tool.get("pre_process_code") or ""))
    return m.group(1) if m and "# mark_product:" in str(tool.get("pre_process_code") or "") else ""


def check_settled_closing(wf, rep):
    """A node whose tools raise the settled flag must not hold the closing tool (it could go out with a note in one
    message) and must have a transition on that flag (blocks.settled_transition) into a node that does."""
    nodes = wf.get("nodes") or {}
    every_flag = {f for n in nodes.values() for f in (_settled_flag(t) for t in n.get("tools") or []) if f}
    closing = {t.get("target_node") for n in nodes.values() for t in n.get("transitions") or []
               if any(f'"variable": "{f}"' in json.dumps(t.get("condition")) for f in every_flag)}
    for k, n in nodes.items():
        tools = n.get("tools") or []
        flags = {f for f in (_settled_flag(t) for t in tools) if f}
        if not flags or k in closing:    # the closing node may repeat a failed note (blocks.closing_node)
            continue
        where = "nodes." + k
        closers = [t["function"]["name"] for t in tools
                   if _is_closer(t) and t.get("function")]
        if closers:
            rep.error(where, f"has the closing tool {closers[0]!r} next to tools that settle products: it can go out in "
                             "the same message as a note; close only in blocks.closing_node after settled_transition")
        conds = json.dumps([t.get("condition") for t in n.get("transitions") or []])
        for f in flags:
            if f'"variable": "{f}"' not in conds:
                rep.error(where, f"no transition on {f!r}: add blocks.settled_transition(<closing node>, {f!r})")


def _is_note_tool(tool):
    hook = str(tool.get("pre_process_code") or "")
    name = str((tool.get("function") or {}).get("name", ""))
    return ("# mark_product:" in hook and '\\"state\\": \\"noted\\"' in hook) or "note" in name.lower()


def check_close_marks_sent(wf, rep):
    """Every closing tool marks that it went out (blocks.close_pre_hook), so blocks.closed_transition can end the call
    before a late answer: in practice runs (raw-a PUB-06, PUB-10) close_call was sent twice."""
    for k, n in (wf.get("nodes") or {}).items():
        for t in n.get("tools") or []:
            if _is_closer(t) and "close_sent" not in str(t.get("pre_process_code") or ""):
                rep.error("nodes." + k, f"closing tool {t['function']['name']!r} has no blocks.close_pre_hook(): the call "
                                        "may not end before its answer comes back, and close_call goes out twice")


def check_close_with_notes(wf, rep):
    """A conversation node must not hold a note tool and the closing tool together: in practice runs close_call went
    out in the same message as add_note and the bank closed first (4 of 30 calls). Use blocks.ready_to_close into a
    blocks.closing_node instead."""
    for k, n in (wf.get("nodes") or {}).items():
        tools = n.get("tools") or []
        closers = [t for t in tools if _is_closer(t)]
        if closers and any(_is_note_tool(t) for t in tools):
            rep.error("nodes." + k, "has a note tool and the closing tool together: close_call can go out in the same "
                                    "message as a note. Take the closing tool out, add blocks.ready_to_close(<closing>) "
                                    "and blocks.closing_node(<closing>, <close tool>, <end>)")


def check_offer_gate(wf, rep, ledger_var="offer_ledger"):
    """Payment requests must pass the offer gate (builder/blocks.offer_loop). Every path into a node that says
    {{offer_line}} must come from a gate code node; nothing else may say the line, return into it with __previous__,
    or write the request ledger."""
    nodes, globals_ = wf.get("nodes") or {}, wf.get("global_nodes") or {}
    every = [(k, n, False) for k, n in nodes.items()] + [(k, n, True) for k, n in globals_.items()]
    line = "{{offer_line}}"
    say_nodes = {k for k, n, _ in every if line in str((n.get("on_enter") or {}).get("text", ""))}
    gates = {k for k, n, _ in every if any(GATE_MARK in c for c in _codes(n))}
    pol = policy_in_workflow(wf) or {}
    limited = any(isinstance(b, dict) and int(b.get("attempts") or 0) > 0 for b in pol.get("bands") or [])
    if limited and (not gates or not say_nodes):
        rep.error("workflow", "the policy allows a limited number of persuasion attempts but the workflow has no "
                              "offer gate: build the requests with builder.blocks.offer_loop and to_gate")
    if gates and pol and pol.get("plans_in_facts", True):
        rep.error("workflow", "the offer gate makes the payment requests but date_params.json leaves plans_in_facts on: "
                              "product_facts then shows each attempt's amount and date and the working node asks them "
                              "itself (seen in a practice run); set \"plans_in_facts\": false")
    if not say_nodes and not gates:
        return
    if not gates:
        rep.error("workflow", "a node says {{offer_line}} but no offer gate code node exists")
    for k, n, is_global in every:
        where = ("global_nodes." if is_global else "nodes.") + k
        texts = [n.get(f, "") for f in ("task_message", "loop_prompt", "role_message", "routing_prompt")]
        if k not in say_nodes:
            texts.append(str((n.get("on_enter") or {}).get("text", "")))
        if any(line in (t or "") for t in texts):
            rep.error(where, "uses {{offer_line}} outside the request node: a request could be made without the gate")
        targets = [t.get("target_node") for t in n.get("transitions") or []]
        targets += [n.get("passthrough_default_transition"), n.get("max_turn_transition")]
        for s_node in say_nodes & set(targets):
            if k not in gates:
                rep.error(where, f"leads into request node {s_node!r} without passing the offer gate")
        if is_global and "__previous__" in targets:
            missing = say_nodes - set(n.get("excluded_nodes") or [])
            if missing:
                rep.error(where, f"returns with __previous__ and may re-enter {sorted(missing)}, repeating a request "
                                 "without counting it; add them to excluded_nodes")
        for c in _codes(n):
            if ledger_var in c and GATE_MARK not in c and MARK_MARK not in c and POSITION_MARK not in c:
                rep.error(where, f"code writes or reads {ledger_var!r} outside the offer gate and mark hooks")
    if wf.get("initial_node") in say_nodes:
        rep.error("workflow", "the call starts in a request node, before the offer gate")
    check_walk(wf, rep, every, gates, say_nodes)


def check_speakable(wf, rep):
    """Every conversation node must give the model something to say: an on_enter llm needs its prompt, a tts its
    text. In a practice run (fix17-ab) the closing node produced "The model returned no speakable content"."""
    for k, n in (wf.get("nodes") or {}).items():
        if n.get("node_type") != "conversation":
            continue
        oe = n.get("on_enter") or {}
        if oe.get("type") == "llm" and not str(oe.get("prompt") or "").strip():
            rep.error("nodes." + k, "on_enter llm without a prompt: the model may return nothing speakable")
        if oe.get("type") == "tts" and not str(oe.get("text") or "").strip():
            rep.error("nodes." + k, "on_enter tts without text")


def _reply_targets(node):
    """{reply value: target} for transitions that test one reply value (offer_walk / offer_loop request nodes)."""
    out = dict()
    for t in node.get("transitions") or []:
        for c in (t.get("condition") or {}).get("conditions") or []:
            if c.get("operator") == "equals" and isinstance(c.get("operand"), str):
                out.setdefault(c["operand"], t.get("target_node"))
    return out


def check_walk(wf, rep, every, gates, say_nodes):
    """The controlled offer flow (blocks.offer_walk): every answer to a request that is a yes or a no goes back into a
    gate, so the gate counts and moves on; the working node never sees the plan; and what cannot be proven from the
    graph (free text of the model in a node that leads into the flow) is reported as a warning."""
    nodes = {k: n for k, n, _ in every}
    walk_gates = {g for g in gates if any(WALK_FLAG in c for c in _codes(nodes.get(g, {})))}
    for s_node in say_nodes:
        feeders = [k for k, n, _ in every if s_node in [t.get("target_node") for t in n.get("transitions") or []]
                   + [n.get("passthrough_default_transition")]]
        if not any(f in walk_gates for f in feeders):
            continue
        targets = _reply_targets(nodes.get(s_node, {}))
        for answer in ("accepted", "refused"):
            if targets.get(answer) not in gates:
                rep.error("nodes." + s_node, f"in the walk, a {answer!r} answer must go back into the offer gate "
                                             f"(goes to {targets.get(answer)!r}): only the gate counts and moves on")
    if not gates:
        return
    for k, n, _ in every:
        texts = " ".join(str(n.get(f, "")) for f in ("task_message", "loop_prompt", "role_message", "routing_prompt"))
        texts += " " + str((n.get("on_enter") or {}).get("prompt", "")) + " " + str((n.get("on_enter") or {}).get("text", ""))
        if "{{offer_plan" in texts:
            rep.error("nodes." + k, "shows the persuasion plan to the model ({{offer_plan...}}): the offer gate words "
                                    "the requests; a model that sees the plan asks it itself")
    feeding = sorted(k for k, n, _ in every if n.get("node_type") == "conversation" and k not in say_nodes
                     and any(t.get("target_node") in gates for t in n.get("transitions") or []))
    marks = {k for k, n, _ in every if any(POSITION_MARK in c for c in _codes(n))}
    offering = set(gates) | set(say_nodes) | set(feeding) | marks
    for g in sorted(walk_gates):
        for t in nodes[g].get("transitions") or []:
            if '"offer_state"' not in json.dumps(t.get("condition")):
                continue
            start = t.get("target_node")
            seen, todo = set(), [start]
            while todo:
                k = todo.pop()
                if k in seen or k not in nodes:
                    continue
                seen.add(k)
                n = nodes[k]
                todo += [x.get("target_node") for x in n.get("transitions") or []]
                todo += [n.get("passthrough_default_transition"), n.get("max_turn_transition")]
            hit = sorted(seen & offering)
            if hit:
                rep.error("nodes." + g, f"after the walk is done (to {start!r}) the call can reach {hit}, which can "
                                        "make a new payment request: send done to blocks.records_node, which has no "
                                        "way back")
            for k in sorted(seen):
                n = nodes.get(k) or {}
                oe = n.get("on_enter") or {}
                text = " ".join(str(x or "") for x in (n.get("task_message"), n.get("loop_prompt"), n.get("role_message"),
                                                       oe.get("prompt"), oe.get("text")))
                shown = [v for v in FACT_VARS if "{{" + v in text]
                if shown:
                    rep.error("nodes." + k, f"after the walk is done the model here sees {shown}: amounts, floors and "
                                            "deadlines are enough to make up a new request (fix18-ab: the records node "
                                            "asked for the car loan's floor on its deadline); give it only {{offer_summary}}")
    if walk_gates:
        shown = [v for v in FACT_VARS if "{{" + v in str(wf.get("system_prompt") or "")]
        if shown:
            rep.error("workflow", f"the system prompt shows {shown} to every node, including the records node after the "
                                  "walk: keep facts in the working node's task only")
    closers = {k for k, n, _ in every if any(_is_closer(x)
                                             for x in n.get("tools") or [])}
    walk_feeding = [k for k in feeding if any(t.get("target_node") in walk_gates for t in nodes[k].get("transitions") or [])]
    done_targets = {t.get("target_node") for g in walk_gates for t in nodes[g].get("transitions") or []
                    if '"offer_state"' in json.dumps(t.get("condition"))}
    for k in sorted(done_targets - set(gates)):
        n = nodes.get(k) or {}
        if n.get("node_type") != "conversation":
            continue
        text = str(n.get("task_message") or "") + str((n.get("on_enter") or {}).get("prompt") or "")
        missing = [v for v in ("offer_summary", "calendar_facts") if "{{" + v + "}}" not in text]
        if missing:
            rep.error("nodes." + k, f"the records node does not show {missing}: it needs the summary and the calendar "
                                    "(fix23-a: without dates it wrote today as 2025-05-14); keep blocks.RECORDS_TASK")
    for k in walk_feeding:
        for t in nodes[k].get("transitions") or []:
            if t.get("target_node") in done_targets and t.get("target_node") not in gates:
                rep.error("nodes." + k, f"goes straight to the records node {t.get('target_node')!r}: offer_summary is "
                                        "not written on that way (fix22-a: the records node made up a payment link); "
                                        "go through blocks.records_entry")
    for k in walk_feeding:
        n = nodes[k]
        if k in closers:
            rep.error("nodes." + k, "the working node of the walk holds the closing tool: it could close before the "
                                    "records and notes; close only from the records node")
        for t in n.get("transitions") or []:
            if t.get("is_tool_call") and t.get("target_node") in closers:
                rep.error("nodes." + k, f"the working node can go straight to {t.get('target_node')!r}, which closes, "
                                        "before the records and notes: use blocks.to_records")
    for k in feeding:
        rep.warn("nodes." + k, "not provable from the graph: this node's model writes free text, and a payment request "
                               "it words itself would not be counted (there is no documented output filter); keep "
                               "amounts and plans out of its prompt and let the offer gate walk the products")


def validate(wf, spec=None, call_vars=None):
    rep = Report()
    if not isinstance(wf, dict):
        rep.error("workflow", "not an object")
        return rep
    if "workflow" in wf and "nodes" not in wf:
        rep.error("workflow", "this looks like an export or request body; pass the inner workflow object")
        return rep
    _nulls(wf, "workflow", rep)
    if "agent_config" in wf:
        rep.warn("workflow", "agent_config is present; the kit says to leave it out")
    if "none of your tools does" not in str(wf.get("system_prompt") or ""):
        rep.warn("workflow.system_prompt", "no blocks.NO_UNBACKED_ACTIONS: a practice agent (raw-a2 PUB-05) said it "
                                           "would note something on the account with no note tool; build with "
                                           "blocks.workflow, which adds it")
    nodes, globals_ = wf.get("nodes") or {}, wf.get("global_nodes") or {}
    if not nodes:
        rep.error("workflow", "no nodes")
        return rep
    if wf.get("initial_node") not in nodes:
        rep.error("workflow", f"initial_node {wf.get('initial_node')!r} is not a node")
    keys = set(nodes) | set(globals_)

    # Variables that something sets, for the placeholder check.
    known = set(call_vars or []) | set((wf.get("test_variables") or {}).keys())
    prefixes = set()
    extraction_names = set()
    model_fields = set()
    all_nodes = [(k, n, False) for k, n in nodes.items()] + [(k, n, True) for k, n in globals_.items()]
    for key, node, _ in all_nodes:
        for e in node.get("extraction_schema") or []:
            extraction_names.add(e.get("name"))
        for m in node.get("response_mapping") or []:
            known.add(m.get("variable"))
        for t in (node.get("tools") or []) + (node.get("pre_actions") or []):
            for m in t.get("response_mapping") or []:
                known.add(m.get("variable"))
            code = t.get("function", {}).get("parameters", {}).get("properties", {}).get("code", {}).get("default", "")
            for c in (code, t.get("pre_process_code", ""), t.get("post_process_code", "")):
                n, p = _written_vars(c)
                known |= n
                prefixes |= p
            body = t.get("function", {}).get("parameters", {}).get("properties", {}).get("body", {}).get("properties", {})
            for leaf in body.values():
                d = leaf.get("default")
                if isinstance(d, str) and PLACEHOLDER.fullmatch(d):
                    model_fields.add(PLACEHOLDER.fullmatch(d).group(1).strip())
    for t in wf.get("tools") or []:
        for c in (t.get("pre_process_code", ""), t.get("post_process_code", "")):
            n, p = _written_vars(c)
            known |= n
            prefixes |= p
    known |= extraction_names

    def var_known(v):
        if v in known or v in model_fields:
            return True
        if v.startswith(("system.", "call.", "date.", "time.", "extractor.")):
            return True
        if "_response." in v or "_args." in v or v.endswith(AUTO_SUFFIXES):
            return True
        return any(v.startswith(p) for p in prefixes)

    has_end = False
    for key, node, is_global in all_nodes:
        where = ("global_nodes." if is_global else "nodes.") + key
        nt = node.get("node_type")
        if nt not in NODE_TYPES:
            rep.error(where, f"unknown node_type {nt!r}")
        if node.get("name") and node["name"] != key:
            rep.warn(where, f"name {node['name']!r} differs from the key")
        if node.get("tools") and nt != "conversation":
            rep.error(where, "tools on a non-conversation node are removed by the platform")
        if node.get("pre_actions") and nt not in ACTION_TYPES:
            rep.error(where, "pre_actions on a conversation or end_call node are removed by the platform")
        for i, t in enumerate(node.get("tools") or []):
            if t.get("function", {}).get("handler") == "__function__:end_call":
                has_end = True
            _check_tool(t, f"{where}.tools[{i}]", spec, rep, extraction_names)
        for i, t in enumerate(node.get("pre_actions") or []):
            if t.get("response_mapping"):
                rep.error(f"{where}.pre_actions[{i}]", "response_mapping inside a pre_actions entry is ignored; put it on the node")
            _check_tool(t, f"{where}.pre_actions[{i}]", spec, rep, extraction_names)
        for e in node.get("extraction_schema") or []:
            if "min_confidence" not in e:
                rep.warn(where, f"extraction {e.get('name')!r} has no min_confidence (default 0.95 is strict)")
            p = e.get("extraction_prompt")
            if p and not all(x in p for x in EXTRACTOR_PLACEHOLDERS):
                rep.error(where, f"extraction_prompt of {e.get('name')!r} lacks the extractor placeholders; it would never see the user")
        trans = node.get("transitions") or []
        for i, t in enumerate(trans):
            tgt = t.get("target_node")
            if tgt not in keys and not (is_global and tgt == "__previous__"):
                rep.error(f"{where}.transitions[{i}]", f"target_node {tgt!r} does not exist")
            conds = _conditions(t.get("condition", {}), f"{where}.transitions[{i}]", rep)
            _bool_pairs(conds, f"{where}.transitions[{i}]", rep)
        if is_global and node.get("condition"):
            _conditions(node["condition"], where + ".condition", rep)
        if node.get("passthrough"):
            if not trans or trans[-1].get("condition", {}).get("operator") != "always":
                rep.error(where, "passthrough node must end with an `always` transition or it can deadlock")
            pdt = node.get("passthrough_default_transition")
            if not pdt or (pdt not in keys and pdt != "__previous__"):
                rep.error(where, f"passthrough_default_transition {pdt!r} is missing or not a node")
        mtt = node.get("max_turn_transition")
        if mtt and mtt not in keys:
            rep.error(where, f"max_turn_transition {mtt!r} is not a node")
        target = nodes.get(mtt) or globals_.get(mtt) or {}
        api_tools = [t for t in node.get("tools") or [] if t.get("function", {}).get("handler") == "__function__:api_call"]
        # A node with a single bank tool is a dedicated closing step (wrap-up); its own cap is the last resort.
        if mtt and nt == "conversation" and target.get("node_type") == "end_call" and len(api_tools) > 1:
            rep.warn(where, f"turn cap goes straight to end_call node {mtt!r}: the call ends without the closing tool; "
                            "point it at a wrap-up node (builder.blocks.wrap_up_node)")
        if node.get("max_node_turns") and not mtt and nt == "conversation":
            rep.warn(where, "max_node_turns without max_turn_transition changes nothing at the cap")
        if nt == "end_call":
            has_end = True
            if trans:
                rep.error(where, "end_call node must have no transitions")
            term = node.get("terminal", {})
            if term.get("function", {}).get("handler") != "__function__:end_call":
                rep.error(where, "end_call node needs terminal.function.handler __function__:end_call")
            if (node.get("on_enter") or {}).get("type") not in (None, "silent"):
                rep.warn(where, "farewell belongs in the terminal's spoken_message; set on_enter silent")
        texts = [node.get(f, "") for f in ("task_message", "loop_prompt", "role_message", "routing_prompt")]
        texts.append((node.get("on_enter") or {}).get("text", ""))
        for txt in texts:
            for v in PLACEHOLDER.findall(txt or ""):
                if not var_known(v.strip()):
                    rep.warn(where, f"{{{{{v.strip()}}}}} is used in a prompt but nothing visible sets it (call variable?)")
    for txt in [wf.get("system_prompt", "")]:
        for v in PLACEHOLDER.findall(txt or ""):
            if not var_known(v.strip()):
                rep.warn("system_prompt", f"{{{{{v.strip()}}}}} is used but nothing visible sets it (call variable?)")
    for i, t in enumerate(wf.get("tools") or []):
        if not (t.get("config") or {}).get("alwaysRunsAt"):
            rep.error(f"tools[{i}]", "workflow-level tool without config.alwaysRunsAt never runs")
        _check_tool(t, f"tools[{i}]", spec, rep, extraction_names)
    if not has_end:
        rep.error("workflow", "no end_call node and no end_call tool: the call can never end")

    # Reachability from the initial node (global nodes can fire anywhere).
    seen, todo = set(), [wf.get("initial_node")] + list(globals_)
    while todo:
        k = todo.pop()
        if k in seen or k not in keys:
            continue
        seen.add(k)
        n = nodes.get(k) or globals_.get(k)
        todo += [t.get("target_node") for t in n.get("transitions") or []]
        todo += [n.get("passthrough_default_transition"), n.get("max_turn_transition")]
    for k in set(nodes) - seen:
        rep.warn("nodes." + k, "not reachable from initial_node")

    check_offer_gate(wf, rep)
    check_settled_closing(wf, rep)
    check_close_with_notes(wf, rep)
    check_close_marks_sent(wf, rep)
    check_speakable(wf, rep)

    if spec is not None:
        used = set()
        for _, node, _ in all_nodes:
            for t in (node.get("tools") or []) + (node.get("pre_actions") or []):
                used.add(t.get("function", {}).get("name"))
        for t in wf.get("tools") or []:
            used.add(t.get("function", {}).get("name"))
        for name in openapi_tools.tool_names(spec):
            if name not in used:
                rep.warn("workflow", f"tool API call {name!r} is never used; check the brief does not need it")
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow")
    ap.add_argument("openapi", nargs="?")
    ap.add_argument("--call-vars", default="", help="comma-separated call variable names from the brief")
    a = ap.parse_args(argv)
    with open(a.workflow, encoding="utf-8") as f:
        wf = json.load(f)
    spec = openapi_tools.load(a.openapi) if a.openapi else None
    rep = validate(wf, spec, [v for v in a.call_vars.split(",") if v])
    print(rep.text())
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
