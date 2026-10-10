"""Build workflow tool functions from the bank's tool API file (tools/openapi.json or a final brief's own file).

Every URL, body field, type and enum comes from the file, so nothing is typed by hand.
Shapes follow docs/tools-and-code.md ("Tool function shape", "api_call body field rules").
"""
import json


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def base_url(spec):
    return spec["servers"][0]["url"].rstrip("/")


def tool_names(spec):
    return [p.strip("/") for p in spec["paths"]]


def operation(spec, name):
    path = "/" + name
    if path not in spec["paths"]:
        raise KeyError(f"{name} is not in the tool API. Tools: {', '.join(tool_names(spec))}")
    methods = spec["paths"][path]
    method = next(iter(methods))
    return method.upper(), methods[method]


def body_schema(spec, name):
    _, op = operation(spec, name)
    schema = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
    return schema.get("properties", {}), schema.get("required", [])


def response_fields(spec, name):
    _, op = operation(spec, name)
    ok = op.get("responses", {}).get("200", {})
    return ok.get("content", {}).get("application/json", {}).get("schema", {}).get("properties", {})


def api_tool(spec, name, *, baked=None, descriptions=None, description="", timeout_ms=12000, max_retries=0,
             pre_hook="", post_hook="", response_mapping=None, enums=None, helper_fields=None):
    """One api_call tool.

    baked:        {field: value}, sent as given and hidden from the model, e.g. {"customer_id": "{{customer_id}}"}.
                  Every other body field is filled by the model ("default": "{{<field>}}").
    descriptions: {field: text} shown to the model for model-filled fields (added to the file's own description).
    enums:        {field: [values]} to narrow a field's allowed values; must be a subset of the file's enum.
    helper_fields: {field: description} model-filled fields that are NOT in the tool API. The pre-hook must read and
                  remove each one before the request goes out (its code says "# strips: <field>"), e.g. the day the
                  customer named, for snippets.promise_guard_code(named_date_key=...).
    """
    method, _ = operation(spec, name)
    props, required = body_schema(spec, name)
    baked, descriptions, enums = baked or {}, descriptions or {}, enums or {}
    unknown = set(baked) | set(descriptions) | set(enums)
    unknown -= set(props)
    if unknown:
        raise KeyError(f"{name}: fields not in the tool API: {sorted(unknown)}")
    body = {}
    for field, schema in props.items():
        leaf = {"type": schema.get("type", "string")}
        allowed = schema.get("enum")
        if field in enums:
            extra = set(enums[field]) - set(allowed or enums[field])
            if extra:
                raise ValueError(f"{name}.{field}: {sorted(extra)} not allowed by the tool API")
            allowed = list(enums[field])
        if allowed:
            leaf["enum"] = list(allowed)
        text = " ".join(x for x in (schema.get("description", ""), descriptions.get(field, "")) if x)
        if allowed and "One of:" not in text:
            text = (text + " One of: " + ", ".join(map(str, allowed)) + ".").strip()
        if schema.get("format") == "date" and "YYYY-MM-DD" not in text:
            text = (text + " Format YYYY-MM-DD.").strip()
        if text and field not in baked:
            leaf["description"] = text
        leaf["default"] = baked.get(field, "{{" + field + "}}")
        body[field] = leaf
    for field, text in (helper_fields or {}).items():
        if field in props:
            raise KeyError(f"{name}: helper field {field!r} is already in the tool API")
        if f"# strips: {field}\n" not in pre_hook + "\n":
            raise ValueError(f"{name}: helper field {field!r} needs a pre_hook that removes it ('# strips: {field}')")
        body[field] = {"type": "string", "description": text, "default": "{{" + field + "}}"}
    tool = {
        "type": "function",
        "config": {"timeoutMs": timeout_ms, "maxRetries": max_retries},
        "function": {
            "name": name,
            "handler": "__function__:api_call",
            "description": description or spec["paths"]["/" + name][method.lower()].get("summary", ""),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "default": base_url(spec) + "/" + name},
                    "method": {"type": "string", "default": method},
                    "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                    "body": {"type": "object", "properties": body, "required": [f for f in required if f not in baked]},
                },
            },
        },
    }
    if pre_hook:
        tool["pre_process_code"] = pre_hook
    if post_hook:
        tool["post_process_code"] = post_hook
    if response_mapping:
        tool["response_mapping"] = list(response_mapping)
    return tool


def repeatable_on_failure(spec, name):
    """True when the tool API file says a failed call records nothing and may be repeated, so the engine may retry it
    (config.maxRetries) without recording anything twice. Read from the error responses' descriptions."""
    _, op = operation(spec, name)
    for code, resp in (op.get("responses") or {}).items():
        if str(code).startswith("5"):
            text = str(resp.get("description", "")).lower()
            if "may be repeated" in text or ("nothing was recorded" in text and "repeat" in text):
                return True
    return False
