"""Small builders for workflow JSON, so a build script never hand-writes the shapes the docs warn about.

Every helper returns plain dicts in the form of docs/workflow-format.md and docs/tools-and-code.md:
grouped conditions with a top-level "or", booleans tested in both spellings, passthrough nodes that end with an
`always` transition, node-level response_mapping for pre_actions, end_call nodes with a silent on_enter.

    from builder import blocks as b
    wf = b.workflow("setup", [
        b.code_node("setup", code, "talk"),
        b.conversation("talk", "Ask ...", tools=[...], transitions=[b.go("bye", "closed", b.gt("close_call_success_count", 0))],
                       max_turn_target="bye"),
        b.end_node("bye"),
    ], system_prompt="...")
"""

# Conditions (use inside go/when_all) ----------------------------------------------------------------------------

def eq(var, value):
    return [{"variable": var, "operator": "equals", "operand": value}]


def ne(var, value):
    return [{"variable": var, "operator": "not_equals", "operand": value}]


def gt(var, n):
    return [{"variable": var, "operator": "greater_than", "operand": n}]


def lt(var, n):
    return [{"variable": var, "operator": "less_than", "operand": n}]


def is_set(var):
    return [{"variable": var, "operator": "is_set"}]


def is_not_set(var):
    return [{"variable": var, "operator": "is_not_set"}]


def is_true(var):
    """A boolean from a tool result: arrives as "true" or "True"."""
    return eq(var, "true") + eq(var, "True")


def is_false(var):
    return eq(var, "false") + eq(var, "False")


def all_of(*parts):
    """AND of condition lists. Each part may itself hold alternatives (for example is_true), which stay OR-ed."""
    conds = []
    for part in parts:
        conds.append(part[0] if len(part) == 1 else {"logic": "or", "conditions": list(part)})
    return [{"logic": "and", "conditions": conds}]


# Transitions ----------------------------------------------------------------------------------------------------

def go(target, description, *alternatives):
    """Transition that fires when any of the alternatives holds (each from eq/gt/is_true/all_of/...)."""
    conds = [c for alt in alternatives for c in alt]
    if not conds:
        raise ValueError("go() needs at least one condition; use always() for an unconditional transition")
    return {"target_node": target, "description": description, "condition": {"logic": "or", "conditions": conds}}


def always(target, description="always"):
    return {"target_node": target, "description": description, "condition": {"operator": "always"}}


def intent(target, description, meaning=None, tool_call=False):
    t = {"target_node": target, "description": description,
         "condition": {"operator": "matches_intent", "description": meaning or description}}
    if tool_call:
        t["is_tool_call"] = True
    return t


# Entry behaviour ------------------------------------------------------------------------------------------------

def say(text):
    """A fixed line, spoken word for word. {{var}} works."""
    return {"type": "tts", "text": text}


def silent():
    return {"type": "silent"}


def llm(prompt=None):
    return {"type": "llm", "prompt": prompt} if prompt else {"type": "llm"}


# Extraction -----------------------------------------------------------------------------------------------------

def extract(name, type_, description, enum=None, min_confidence=0.7, pattern=None):
    e = {"name": name, "type": type_, "description": description, "min_confidence": min_confidence}
    if enum:
        e["enum_values"] = list(enum)
    if pattern:
        e["validation_pattern"] = pattern
    return e


def spoken_date(name="customer_spoken_date", min_confidence=0.7):
    """Extraction of the last exact day the customer said, as YYYY-MM-DD: the independent witness that the promise
    guard compares the tool call's customer_named_date with (promise_guard_code spoken_var=<name>)."""
    return extract(name, "string", "The last exact calendar day the customer themselves said they will pay, as "
                   "YYYY-MM-DD, counted from the call's current date. Only a day the customer said, never one the agent "
                   "proposed. If the customer corrects it, the corrected day.", min_confidence=min_confidence,
                   pattern=r"^\d{4}-\d{2}-\d{2}$")


# Nodes ----------------------------------------------------------------------------------------------------------

def conversation(name, task, *, tools=(), transitions=(), on_enter=None, first_entry_only=False, extraction=(),
                 max_turns=30, max_turn_target=None, loop_prompt=None, routing_prompt=None, role=None):
    n = {"name": name, "node_type": "conversation", "task_message": task, "transitions": list(transitions)}
    if tools:
        n["tools"] = list(tools)
    if on_enter:
        n["on_enter"] = on_enter
    if first_entry_only:
        n["on_enter_first_entry_only"] = True
    if extraction:
        n["extraction_schema"] = list(extraction)
    if loop_prompt:
        n["loop_prompt"] = loop_prompt
    if routing_prompt:
        n["routing_prompt"] = routing_prompt
    if role:
        n["role_message"] = role
    if max_turn_target:
        n["max_node_turns"] = max_turns
        n["max_turn_transition"] = max_turn_target
    return n


def _passthrough(name, node_type, pre_actions, next_node, transitions, mapping=None):
    trans = list(transitions or [])
    if not trans or trans[-1].get("condition", {}).get("operator") != "always":
        trans.append(always(next_node))
    n = {"name": name, "node_type": node_type, "passthrough": True, "passthrough_default_transition": next_node,
         "on_enter": silent(), "disable_inactivity_monitor": True, "pre_actions": list(pre_actions), "transitions": trans}
    if mapping:
        n["response_mapping"] = list(mapping)
    return n


def code_exec(code, timeout_ms=10000):
    return {"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                                             "parameters": {"type": "object", "properties": {
                                                 "code": {"type": "string", "default": code},
                                                 "timeout_ms": {"type": "number", "default": timeout_ms}}}}}


def code_node(name, code, next_node, transitions=None, timeout_ms=10000):
    """Runs Python on entry, then moves on. Its counters are named after the node: <name>_success_count."""
    return _passthrough(name, "code", [code_exec(code, timeout_ms)], next_node, transitions)


def api_node(name, tool, next_node, mapping=(), transitions=None):
    """Runs one api_call on entry (cannot be skipped by the model). `tool` from openapi_tools.api_tool.
    The mapping goes on the node: a mapping inside a pre_actions entry is ignored by the platform."""
    tool = dict(tool)
    inner = tool.pop("response_mapping", None)
    return _passthrough(name, "api_request", [tool], next_node, transitions, list(mapping) + list(inner or []))


def end_node(name, spoken=None, function_name=None):
    """Hangs up. With `spoken`, says that line first; otherwise silent (say goodbye in the node before).
    `function_name` becomes the call's endedReason."""
    params = {"message_mode": {"type": "string", "default": "custom" if spoken else "none"}}
    if spoken:
        params["spoken_message"] = {"type": "string", "default": spoken}
    return {"name": name, "node_type": "end_call", "on_enter": silent(), "disable_inactivity_monitor": True,
            "transitions": [],
            "terminal": {"type": "function", "function": {"name": function_name or name, "handler": "__function__:end_call",
                                                          "parameters": {"type": "object", "properties": params}}}}


def global_node(name, condition, task, *, on_enter=None, target="__previous__", excluded=(), tool_call=False, tools=(),
                passthrough=None):
    """An interrupt that can fire from any node not in `excluded`, then returns to `target`.
    With a fixed line (on_enter=say(...)) it is a passthrough by default: say the line, go back at once
    (the docs' recommended redirect). Otherwise it waits for one customer turn before going back."""
    if passthrough is None:
        passthrough = bool(on_enter and on_enter.get("type") == "tts")
    n = {"name": name, "node_type": "conversation", "description": task, "condition": condition, "task_message": task,
         "excluded_nodes": list(excluded), "transitions": [always(target)]}
    if passthrough:
        n["passthrough"] = True
        n["passthrough_default_transition"] = target
    if on_enter:
        n["on_enter"] = on_enter
    if tool_call:
        n["is_tool_call"] = True
    if tools:
        n["tools"] = list(tools)
    return n


# Closing --------------------------------------------------------------------------------------------------------
# Seen in a practice run: an agent kept saying "Already closed. Goodbye." until its turn cap because the transition
# after the closing tool did not fire, and a node that hit its turn cap hung up without the closing tool at all.
# So: leave on ANY sign that the closing tool answered, and never let a turn cap skip the closing tool.

CLOSED_FLAG = "call_closed"


def closed_transition(target, tool="close_call", flag=CLOSED_FLAG):
    """Leave for the end node once the closing tool answered: success counter, any parsed response, a second
    failure, or the flag its post-hook sets (close_post_hook)."""
    return go(target, f"{tool} answered: end the call", gt(f"{tool}_success_count", 0),
              is_set(f"{tool}_response.data.ok"), gt(f"{tool}_failure_count", 1), eq(flag, "yes"))


def close_post_hook(snippets, flag=CLOSED_FLAG):
    """post_process_code for the closing tool: no further speech after success, one retry after a failure."""
    return snippets.post_result_code(
        on_success="The call is now closed. Reply with only one word: Goodbye.",
        on_failure="Closing failed. Call the closing tool once more with the same values, then reply with only: Goodbye.",
        set_on_success={flag: "yes"})


def wrap_up_node(name, close_tool, end_target, *, task=None, tool="close_call", max_turns=4):
    """Where every turn cap leads: thanks, says goodbye and calls the closing tool, then ends.
    Point each conversation node's max_turn_target here instead of at the end node."""
    task = task or ("The call has run too long and must end now. Thank the customer, say goodbye, and in the same "
                    f"message call {tool} with the outcome and reason that apply to what happened so far, following "
                    "the closing rules. Do not start anything new.")
    return conversation(name, task, tools=[close_tool], on_enter=llm(task),
                        transitions=[closed_transition(end_target, tool)], max_turns=max_turns, max_turn_target=end_target)


# Closing only after every product is settled ----------------------------------------------------------------------
# Seen in a practice run (a brief that needs a note for every product without a promise): the agent sent close_call in
# the same message as two add_note calls; the bank closed first and refused the notes. A prompt rule did not stop it.
# So the working node has no closing tool: mark_product_code(settled_flag=...) on the promise and note tools raises the
# flag when every product has a promise requested or a note, and only then does the call move to a closing node.
# NOT RECOMMENDED (fix13-ab, two practice calls): parallel add_note calls lost each other's ledger writes, the flag
# never rose, and with no closing tool in the working node the call never closed. Kept for tests and later work; the
# skill tells the builder not to use it, nor settled_products_extraction (it marked a refused loan as agreed).

SETTLED_FLAG = "all_settled"


def settled_transition(target, flag=SETTLED_FLAG):
    """From the working node to the closing node, once every product has a promise requested or a note."""
    return go(target, "every product has a promise requested or a note: close the call", eq(flag, "yes"))


def closing_node(name, close_tool, end_target, *, extra_tools=(), task=None, max_turns=6):
    """Node entered by ready_to_close (recommended, no extra tools) or settled_transition: the only place with the
    closing tool besides wrap_up and outcome_after. extra_tools only with settled_transition (not recommended)."""
    if task is None and not extra_tools:
        task = CLOSING_TASK
    task = task or ("Every product now has a promise or a note. If a promise or note result just now was not ok, call "
                    "that tool again alone first and wait for its result. Otherwise, in one message: say any sentence "
                    "a recorded promise still needs, thank the customer, say goodbye, and call close_call with the "
                    "outcome and reason from the closing rules. close_call is the last tool call, alone.")
    return conversation(name, task, tools=list(extra_tools) + [close_tool], on_enter=llm(task),
                        transitions=[closed_transition(end_target)], max_turns=max_turns, max_turn_target=end_target)


# Closing through a "ready to close" transition (no shared variables) ---------------------------------------------
# Seen in practice runs (fix14-ab, 4 of 30 calls): close_call went out in the same message as add_note; the bank closed
# first and the note was refused. The settled flag above failed live (parallel pre-hooks lose writes), so here nothing
# depends on variables: the working node has no closing tool at all. The model takes a tool-call transition when it is
# done; a note sent in that same message is already on its way, and close_call exists only in the next node.

READY_TO_CLOSE = ("Every product has its recorded promise or its note and every one of those results has come back ok "
                  "(or nothing is left to do by the closing rules): move on to close the call.")


def ready_to_close(target, when=READY_TO_CLOSE):
    """Tool-call transition from the working node to the closing node (closing_node with no extra tools)."""
    return {"target_node": target, "description": when, "is_tool_call": True,
            "condition": {"operator": "matches_intent", "description": when}}


CLOSING_TASK = ("All work is done. In one message: say any sentence the rules still require that you have not said yet "
                "(for example the sentence after a recorded promise), thank the customer, say goodbye, and call "
                "close_call alone with the outcome and reason from the closing rules. Call no other tool.")


def settled_products_extraction(name="settled_products", min_confidence=0.5):
    """Extraction for the offer gate's settled_var: products the customer already agreed or settled in the working
    node, so the gate never asks for them again."""
    return extract(name, "string", "Comma-separated product_ids (p1, p2, ... from the product facts) of every product "
                   "for which the customer has already agreed an amount and a date, said it is already paid, or "
                   "disputed it. Empty if none.", min_confidence=min_confidence)


# Outcomes that need an earlier tool ------------------------------------------------------------------------------
# Seen in practice runs: the agent called the closing tool with "transferred" before calling the transfer tool; the call
# was closed and the bank refused the transfer. A prompt rule did not stop it (2 of 3 runs). So the outcome is taken
# out of the closing tool where the work happens, and offered only in a node reached after the tool succeeded.

def outcome_after(name, tool, close_tool, end_target, *, task=None, max_turns=4):
    """Node entered after `tool` succeeded, whose closing tool (built with enums={outcome: [...]}) allows only the
    outcomes that need that tool. Pair it with go(name, ..., gt(f"{tool}_success_count", 0)) in the working node,
    and leave those outcomes out of the working node's closing tool."""
    task = task or (f"{tool} has succeeded. Tell the customer what happens next in one short sentence, thank them, say "
                    "goodbye and in the same message call close_call with the outcome and reason that match it.")
    return conversation(name, task, tools=[close_tool], on_enter=llm(task),
                        transitions=[closed_transition(end_target)], max_turns=max_turns, max_turn_target=end_target)


# Payment requests through one gate ------------------------------------------------------------------------------
# Seen in a practice run: inside one conversation node the agent added an unplanned "can you pay the full amount?"
# before each product's first attempt, so a product allowed one request got two. A count kept in the model's head can
# not be enforced. So requests are made only by a node that says vars["offer_line"] word for word, and that node can
# only be entered from the offer gate code node, which counts the request when it prepares the line.

OFFER_LINE = "{{offer_line}}"


def offer_loop(gate_name, say_name, back_node, gate_code, *, reply_var="offer_reply", max_turns=3):
    """[gate node, request node]. The working node reaches the gate with to_gate(gate_name); the gate either prepares
    the next allowed request or, when none is allowed, goes back with vars["offer_state"] == "done"."""
    gate = code_node(gate_name, gate_code, say_name,
                     transitions=[go(back_node, "no payment request is allowed now", eq("offer_state", "done"))])
    task = ("You have just made the payment request above. Do not make any other request or offer, do not name any other "
            "amount or date, and call no tool. If the customer asks a question, answer it in one short sentence and then "
            "repeat the request word for word.")
    request = conversation(
        say_name, task, on_enter=say(OFFER_LINE),
        extraction=[extract(reply_var, "string",
                            "The customer's answer to the payment request just made: accepted (agrees to exactly that "
                            "amount and date), refused (says no without proposing anything), other (proposes their own "
                            "date or amount, says the amount is too much, asks to stop, mentions illness, job loss, a "
                            "death, a dispute, a payment already made, a new plan or a human, or anything else).",
                            enum=["accepted", "refused", "other"], min_confidence=0.6)],
        transitions=[go(back_node, "the customer accepted the request", eq(reply_var, "accepted")),
                     go(gate_name, "the customer refused: the gate decides the next request, if any", eq(reply_var, "refused")),
                     go(back_node, "the customer said something else", eq(reply_var, "other"))],
        max_turns=max_turns, max_turn_target=back_node)
    return [gate, request]


def product_offer_loops(max_products, back_node, gate_code_for, *, gate_prefix="offer_gate_",
                        say_prefix="offer_request_", reply_var="offer_reply"):
    """One gate and one request node per product position (1..max_products, the brief's most products per customer).
    gate_code_for(k) returns the gate code for position k, e.g.
        lambda k: snippets.offer_gate_code({..., "product_index": k}).
    Returns (nodes, transitions): add the nodes to the workflow and the transitions (product_to_gate) to the working
    node. Each gate asks only for its own product, so a refusal of product 2 can never produce a request for product 1."""
    nodes, transitions = [], []
    for k in range(1, int(max_products) + 1):
        nodes += offer_loop(f"{gate_prefix}{k}", f"{say_prefix}{k}", back_node, gate_code_for(k), reply_var=reply_var)
        transitions.append(product_to_gate(f"{gate_prefix}{k}", k))
    return nodes, transitions


def product_to_gate(gate_name, k):
    """Tool-call transition into the gate for product number k (get_account order, as numbered in product_facts)."""
    return to_gate(gate_name, when=f"The customer refused to pay product number {k} (Product {k} in the product facts) "
                                   "with no date and no amount, and a persuasion attempt for that product may follow.")


def to_gate(gate_name, when="The customer refused to pay (no date, no amount) and a persuasion attempt may follow."):
    """Tool-call transition from the working node to the offer gate: the agent never words a request itself."""
    return {"target_node": gate_name, "description": when, "is_tool_call": True,
            "condition": {"operator": "matches_intent", "description": when}}


# Tool failures --------------------------------------------------------------------------------------------------
# Seen in practice runs: when the bank answered a write with HTTP 500, the platform logged "function_call_error",
# the tool's post-hook did not run, and the model only saw "API request failed: ...". With no instruction it apologised,
# said goodbye and closed, in every run, although the brief allowed one more try. So the failure rules must be in the
# tool description and the prompt (always visible). The engine's maxRetries did not repeat a call after an HTTP 500 in a
# later practice build, so the model does the retry (engine_retries=0, the default); that build then passed.

def failure_rules(tool, final_line, *, engine_retries=0, then="", holding_line="One moment, please."):
    """Text for the tool description and the prompt: what a failed call means and what to say.
    final_line: the brief's sentence for when all allowed attempts failed. then: what the brief says to do next.
    holding_line: a short natural line said while trying again (in the call's language).
    Only an answer from the bank with ok true proves a record; the tool API has no call to read records back, so an
    error or a missing answer is never reported as recorded. The number of calls that reach the bank is limited in code
    (promise_guard max_attempts), separately from the payment requests the customer hears (offer gate)."""
    proof = (f"Only a {tool} result with ok true means it is recorded; after an error or no answer it is not "
             "confirmed, so never say or imply that it is recorded.")
    if engine_retries:
        attempts = (f"The system already repeated a failed {tool} call {engine_retries} time(s) by itself, so an error "
                    "you see means every allowed attempt failed.")
        act = f'say exactly: "{final_line}"' + (f" Then {then}" if then else "")
    else:
        attempts = (f'The first time {tool} fails in this call, say only "{holding_line}" and, in the same message, call '
                    f"{tool} again with exactly the same values. Do not ask the customer anything again.")
        act = f'If it fails again, say exactly: "{final_line}"' + (f" Then {then}" if then else "")
    return (f"If {tool} returns an error (for example 'API request failed', 'service unavailable' or ok false), "
            "nothing was recorded. Never describe a technical error, a system or a retry to the customer, never apologise "
            f"at length, and never end the call because of a failure on its own. {proof} {attempts} {act}")


# Workflow -------------------------------------------------------------------------------------------------------

def workflow(initial, nodes, *, system_prompt="", global_nodes=(), test_variables=None, lifecycle_tools=()):
    by_name = {}
    for n in nodes:
        if n["name"] in by_name:
            raise ValueError(f"duplicate node {n['name']}")
        by_name[n["name"]] = n
    wf = {"initial_node": initial, "system_prompt": system_prompt, "nodes": by_name,
          "global_nodes": {g["name"]: g for g in global_nodes}}
    if test_variables:
        wf["test_variables"] = dict(test_variables)
    if lifecycle_tools:
        wf["tools"] = list(lifecycle_tools)
    return wf
