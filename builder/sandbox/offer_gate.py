# offer_gate: the one place that decides whether a payment request may be put to the customer.
# Runs in a passthrough code node; the node after it says vars["offer_line"] word for word (on_enter tts), so every
# request the customer hears was counted here first. The builder prepends one line `PARAMS = json.loads(...)`.
# Rules for this file: only json, re and datetime; never raise; no double braces; read and write through vars.
#
# Why: practice agents asked for payment more often than the brief allowed (an extra "can you pay the full amount?"
# before each product's first attempt) because the count lived only in the model's head.
#
# State, in vars[ledger_var] as JSON, only ever written here (and product states by mark hooks):
#   counts  {product_id: requests put to the customer}   never decreases, never reset by any route
#   state   {product_id: "open" | "agreed" | "refused" | <set by a mark hook, e.g. "promise_requested", "noted">}
#   current product_id of the last request, or ""
# Inputs: vars[plans_var] (written by date_facts.py: order, plans, products), vars[reply_var] (extracted answer to
# the last request: accepted, refused or other; cleared here once read), vars[alt_flag_var] ("yes" when the customer
# said the full amount is too much).
# Outputs: vars["offer_state"] = "offer" (offer_line is ready) | "done" (no request is allowed now),
#          vars["offer_line"], vars["offer_product"], vars["offer_summary"].
#
# PARAMS keys:
#   ledger_var, plans_var, reply_var, alt_flag_var   variable names (defaults below)
#   line_template      e.g. "For your {product}, could you pay {amount} on {date}?" (placeholders: product, amount, date)
#   product_labels     {product type: label template from the product's fields, e.g. "credit card ending {card_last4}"}
#   product_index      optional, 1-based: this gate serves only the product at that position of get_account order
#                      (blocks.product_offer_loops). Seen in practice runs (fix14-ab PUB-12, 3 of 3 runs): with one
#                      gate for all products, a refusal of the personal loan made the gate ask again for the card the
#                      customer had already agreed, and the agent then asked for the loan itself: 3-4 requests where
#                      the brief allowed 2.
#   walk               true for blocks.offer_walk: the request node's answer may also be "too_much" (said about this
#                      product only), kept per product in the ledger; alt_flag_var is not used. Seen in a practice
#                      run (fix17-ab): one call-wide "full amount is too much" extraction turned yes on a plain no, and
#                      every product got its floor as attempt 1.
#   floor_counter_offer  true when the brief offers the floor once after "the full amount is too much" (Brief A rule
#                      26): the gate then says floor_line_template (product, amount, date) on the same date, once per
#                      product, without counting it as a persuasion attempt.
#   settled_var        optional: variable with comma-separated product_ids the customer already agreed or settled
#                      (blocks.settled_products_extraction()); an open product listed there is never asked again
import json


def _load(raw, default):
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw) if raw else None
        return out if isinstance(out, dict) else default
    except Exception:
        return default


def _fill(template, values):
    out = str(template)
    for k, v in values.items():
        out = out.replace("{" + str(k) + "}", str(v))
    return out


def decide(vars, p):
    lv = p.get("ledger_var") or "offer_ledger"
    ledger = _load(vars.get(lv), dict(counts=dict(), state=dict(), current=""))
    counts = ledger.setdefault("counts", dict())
    state = ledger.setdefault("state", dict())
    plans_all = _load(vars.get(p.get("plans_var") or "offer_plans"), dict(order=[], plans=dict(), products=dict()))
    order = plans_all.get("order") or []
    plans = plans_all.get("plans") or dict()
    products = plans_all.get("products") or dict()

    # Products the customer already agreed or settled in the working node (an extraction, settled_var): never asked
    # again. Seen in a practice run: the customer agreed to pay the card tomorrow, the promise was not recorded yet,
    # and the gate asked for the card twice more.
    if p.get("settled_var"):
        for pid in str(vars.get(p["settled_var"]) or "").replace(";", ",").replace(" ", ",").split(","):
            pid = pid.strip()
            if pid in order and state.get(pid, "open") == "open":
                state[pid] = "agreed"

    rv = p.get("reply_var") or "offer_reply"
    reply = str(vars.get(rv) or "").strip().lower()
    vars[rv] = ""                                   # a stale answer must never decide the next request
    cur = ledger.get("current") or ""
    if cur and reply == "accepted":
        state[cur] = "agreed"
        ledger.setdefault("agreed", dict())[cur] = ledger.get("step") or dict()   # the request's own amount and date
    elif cur and reply == "own_terms":
        # The customer named their own day or amount for this product: agreed on their terms. The walk goes on to the
        # next product; the terms are read back and checked by the promise guard when they are recorded. Seen in a
        # practice run (fix18-ab): "I will pay the card tomorrow" went to the working node, which then asked for the
        # loans itself.
        state[cur] = "agreed"
        ledger.setdefault("agreed", dict())[cur] = dict(own_terms=True)
    elif cur and reply == "too_much":
        # The customer said this product's full amount is too much: remembered for this product only.
        ledger.setdefault("too_much", dict())[cur] = True
        step = ledger.get("step") or dict()
        offered = ledger.setdefault("floor_offered", dict())
        if p.get("floor_counter_offer") and step.get("floor") and not offered.get(cur):
            # The brief's counter-offer (Brief A rule 26: "offer the floor, once. Say that the rest stays overdue"):
            # the floor on the same date, once per product, not a persuasion attempt. Seen in a practice run
            # (fix21-a PUB-06): taken as the next attempt, the floor went out on the deadline without the note.
            offered[cur] = True
            prod = products.get(cur) or dict()
            label_t = (p.get("product_labels") or dict()).get(prod.get("type", ""), "") or str(prod.get("type", cur)).replace("_", " ")
            line_t = p.get("floor_line_template") or ("For your {product}, could you pay {amount} on {date}? "
                                                      "The rest of the past-due amount stays overdue.")
            vars["offer_line"] = _fill(line_t, dict(product=_fill(label_t, prod), amount=step["floor"],
                                                    date=step.get("date_label", "")))
            vars["offer_product"] = cur
            vars["offer_state"] = "offer"
            ledger["step"] = dict(amount=step["floor"], date=step.get("date", ""), date_label=step.get("date_label", ""))
            vars[lv] = json.dumps(ledger)
            return
        if int(counts.get(cur, 0)) >= len(plans.get(cur) or []):
            state[cur] = "refused"
    elif cur and reply == "refused" and int(counts.get(cur, 0)) >= len(plans.get(cur) or []):
        state[cur] = "refused"

    pick = ""
    candidates = order
    idx = int(p.get("product_index") or 0)
    if idx:   # a gate for one product position: only that product's next request, never another product's
        candidates = order[idx - 1:idx]
    for pid in candidates:
        if state.get(pid, "open") != "open":
            continue
        if int(counts.get(pid, 0)) < len(plans.get(pid) or []):
            pick = pid
            break
    if pick:
        n = int(counts.get(pick, 0))
        step = plans[pick][n]
        counts[pick] = n + 1                        # counted now: the next node says this line word for word
        amount = step.get("amount", "")
        if p.get("walk"):   # per product, from the request node's own answers, never from another product
            too_much = bool((ledger.get("too_much") or dict()).get(pick))
        else:
            too_much = str(vars.get(p.get("alt_flag_var") or "full_too_much") or "").lower() == "yes"
        line_t = p.get("line_template") or "For your {product}, could you pay {amount} on {date}?"
        if step.get("alt_amount") and too_much:
            amount = step.get("alt_amount")
            if p.get("floor_counter_offer"):     # the floor with the brief's note, and it counts as the floor offer
                line_t = p.get("floor_line_template") or ("For your {product}, could you pay {amount} on {date}? "
                                                          "The rest of the past-due amount stays overdue.")
                ledger.setdefault("floor_offered", dict())[pick] = True
        prod = products.get(pick) or dict()
        label_t = (p.get("product_labels") or dict()).get(prod.get("type", ""), "") or str(prod.get("type", pick)).replace("_", " ")
        vars["offer_line"] = _fill(line_t, dict(product=_fill(label_t, prod), amount=amount, date=step.get("date_label", "")))
        vars["offer_product"] = pick
        vars["offer_state"] = "offer"
        ledger["current"] = pick
        ledger["step"] = dict(amount=amount, date=step.get("date", ""), date_label=step.get("date_label", ""),
                              floor=step.get("alt_amount") or "")
    else:
        used = [pid for pid in order if int(counts.get(pid, 0)) >= len(plans.get(pid) or []) and state.get(pid, "open") in ("open", "refused")]
        vars["offer_line"] = ""
        vars["offer_product"] = ""
        vars["offer_state"] = "done"
        terms = ledger.get("agreed") or dict()

        def _name(pid):
            prod = products.get(pid) or dict()
            lt = (p.get("product_labels") or dict()).get(prod.get("type", ""), "") or str(prod.get("type", pid)).replace("_", " ")
            return _fill(lt, prod) + " (" + pid + ")"

        agreed = [_name(pid) + (": " + str(terms[pid].get("amount", "")) + " on " + str(terms[pid].get("date", ""))
                                if terms.get(pid) and terms[pid].get("date") else ": on the terms the customer said in this call")
                  for pid in order if state.get(pid) == "agreed"]
        used = [_name(pid) for pid in used]
        vars["offer_summary"] = ("No payment request is allowed now. Agreed: " + (", ".join(agreed) if agreed else "none")
                                 + ". All persuasion attempts are used for: " + (", ".join(used) if used else "none")
                                 + ". Make no further request for any product; go on with the read-back, the records "
                                   "and the notes.")
        ledger["current"] = ""
    vars[lv] = json.dumps(ledger)


try:
    decide(vars, PARAMS)
except Exception as _e:
    vars["offer_state"] = "done"                    # on any doubt, ask for nothing
    vars["offer_line"] = ""
    vars["offer_summary"] = "No payment request is allowed now (offer gate error: " + str(_e) + ")."
