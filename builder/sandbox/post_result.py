# post_process_code for an api_call tool (docs/tools-and-code.md, "Tool results and the next turn").
# The builder prepends one line `PARAMS = json.loads(...)`.
# It drops response headers and adds one plain instruction, `what_to_do_next`, to result["data"],
# so the phone agent always has something to say or call after the tool result.
# Rules for this file: only json, re and datetime; never raise; no double braces.
#
# PARAMS keys:
#   on_success      instruction when the call worked
#   on_failure      instruction when the call failed (HTTP error, envelope not "success", or ok == false)
#   when            list of [data_field, value, instruction]; the first matching field wins over on_success/on_failure
#   flag_false      true (default): name every other false field in front of the instruction ("Result: verified is
#                   FALSE."), so a negative answer is never read as a success when no `when` rule covers it
#   guard_var       variable a pre-hook filled with the reason it blocked the request; that reason wins, then it is cleared
#   set_on_success  {variable: value} written to vars when the call worked (for routing, e.g. call_closed)
#   set_on_any      {variable: value} written to vars after any response
#   fail_count_var  variable that counts this tool's failed calls (a blocked guard request does not count)
#   fail_limit      on the failure that reaches this count, on_failure_final replaces on_failure
#   on_failure_final instruction for that last failure (for "try once more, then ..." rules)
import json


def _failed(res, data):
    try:
        if str(res.get("status", "success")).lower() != "success":
            return True
        if int(res.get("status_code", 200)) >= 400:
            return True
    except Exception:
        pass
    if isinstance(data, dict) and str(data.get("ok", "true")).lower() == "false":
        return True
    return not isinstance(data, dict)


def _apply(res, p, variables):
    if not isinstance(res, dict):
        return
    res.pop("header", None)
    data = res.get("data")
    failed = _failed(res, data)
    if not isinstance(data, dict):
        data = dict(ok=False, error=str(data)[:300] if data is not None else "no response body")
        res["data"] = data
    note = p.get("on_failure") if failed else p.get("on_success")
    guard = p.get("guard_var")
    blocked = bool(guard and isinstance(variables, dict) and variables.get(guard))
    counter = p.get("fail_count_var")
    if failed and counter and not blocked and isinstance(variables, dict):
        try:
            n = int(variables.get(counter) or 0) + 1
        except Exception:
            n = 1
        variables[counter] = n
        if p.get("on_failure_final") and n >= int(p.get("fail_limit") or 2):
            note = p.get("on_failure_final")
        elif note:
            note = "Failed attempt " + str(n) + ". " + note
    for rule in p.get("when") or []:
        try:
            field, value, text = rule[0], rule[1], rule[2]
        except Exception:
            continue
        if field in data and str(data.get(field)).lower() == str(value).lower():
            note = text
            break
    if p.get("flag_false", True):
        falses = [k for k, v in data.items() if k != "ok" and str(v).lower() == "false"]
        if falses:
            note = "Result: " + ", ".join(k + " is FALSE" for k in falses) + ". " + (note or "")
    if blocked:
        note = str(variables.get(guard)) + " This is not a service failure and does not count as a failed attempt. " \
            + "Tell the customer what can be recorded instead, following your rules, and read it back before trying again."
        variables[guard] = ""
    if isinstance(variables, dict):
        for k, v in (p.get("set_on_any") or dict()).items():
            variables[k] = v
        if not failed:
            for k, v in (p.get("set_on_success") or dict()).items():
                variables[k] = v
    if note:
        data["what_to_do_next"] = note


try:
    _vars = vars
except NameError:
    _vars = None
try:
    _apply(result, PARAMS, _vars)
except Exception:
    pass
