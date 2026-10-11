"""Build workflow tool functions from the bank's tool API file (tools/openapi.json or a final brief's own file).

Every URL, body field, type and enum comes from the file, so nothing is typed by hand.
Shapes follow docs/tools-and-code.md ("Tool function shape", "api_call body field rules").
"""
import json
import os
import re

MOCK_URL_VAR = "FREYA_MOCK_URL"


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def base_url(spec):
    """The bank's address: FREYA_MOCK_URL when set (the team's own mock, builder.run --mock-url), else the file's."""
    return (os.environ.get(MOCK_URL_VAR) or spec["servers"][0]["url"]).rstrip("/")


METHODS = ("get", "post", "put", "patch", "delete")


def _ops(spec):
    """(name, path, method, operation) for every operation. The name is the operationId; without one, the path without
    its slashes and braces (the kit's shape: POST /verify_identity is verify_identity)."""
    out = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if method.lower() in METHODS and isinstance(op, dict):
                name = op.get("operationId") or re.sub(r"[^A-Za-z0-9_]+", "_", path.strip("/")).strip("_")
                out.append((name, path, method.lower(), op))
    return out


def tool_names(spec):
    return [n for n, _, _, _ in _ops(spec)]


def _find(spec, name):
    for n, path, method, op in _ops(spec):
        if n == name:
            return path, method, op
    raise KeyError(f"{name} is not in the tool API. Tools: {', '.join(tool_names(spec))}")


def operation(spec, name):
    _, method, op = _find(spec, name)
    return method.upper(), op


def url_params(spec, name):
    """{name: schema} of the operation's path and query parameters (OpenAPI "parameters", in: path or query)."""
    path, _, op = _find(spec, name)
    out = dict()
    for prm in op.get("parameters") or spec["paths"][path].get("parameters") or []:
        if isinstance(prm, dict) and prm.get("in") in ("path", "query") and prm.get("name"):
            schema = dict(prm.get("schema") or {"type": "string"})
            if prm.get("description"):
                schema["description"] = prm["description"]
            schema["_in"], schema["_required"] = prm["in"], bool(prm.get("required") or prm["in"] == "path")
            out[prm["name"]] = schema
    return out


def tool_url(spec, name):
    """The tool's url: base address + the operation's own path. Path and query parameters stay as {name} (single
    braces, never resolved by the engine); URL_PARAMS_HOOK fills them from the body before the request goes out."""
    path, _, _ = _find(spec, name)
    query = [k for k, v in url_params(spec, name).items() if v["_in"] == "query"]
    return base_url(spec) + path + ("?" + "&".join(k + "={" + k + "}" for k in query) if query else "")


URL_PARAMS_HOOK = """
# url parameters (openapi_tools.URL_PARAMS_HOOK): move each from the body into the url, encoded; no body for GET/DELETE
try:
    _up = json.loads(%s)
    _url = str(config.get("url") or "")
    _body = config.get("body") if isinstance(config.get("body"), dict) else dict()
    for _k in _up:
        _raw = str(_body.pop(_k, ""))
        _enc = "".join(chr(_c) if (chr(_c).isalnum() and _c < 128) or chr(_c) in "-_.~" else "%%%%%%02X" %% _c
                       for _c in _raw.encode("utf-8"))
        _url = _url.replace("{" + _k + "}", _enc)
    config["url"] = _url
    if str(config.get("method") or "").upper() in ("GET", "DELETE"):
        config["body"] = dict()
except Exception:
    pass
"""


NEST_HOOK = """
# nested body (openapi_tools.NEST_HOOK): leaves named parent__child go back into their objects
try:
    _nb = config.get("body") if isinstance(config.get("body"), dict) else dict()
    for _key in [k for k in list(_nb) if "__" in k]:
        _val = _nb.pop(_key)
        _parts = _key.split("__")
        _cur = _nb
        for _p in _parts[:-1]:
            if not isinstance(_cur.get(_p), dict):
                _cur[_p] = dict()
            _cur = _cur[_p]
        _cur[_parts[-1]] = _val
    config["body"] = _nb
except Exception:
    pass
"""


def url_params_hook(names):
    return "import json\n" + URL_PARAMS_HOOK % json.dumps(json.dumps(list(names)))


NEST = "__"


def _flatten(props, required, prefix=""):
    """Nested object fields become leaves named parent__child: the engine fills leaves under body.properties only.
    NEST_HOOK puts them back into their objects before the request goes out."""
    out, req = dict(), []
    for k, v in props.items():
        key = prefix + k
        if isinstance(v, dict) and v.get("type", "object") == "object" and isinstance(v.get("properties"), dict) \
                and v["properties"]:
            sub, sub_req = _flatten(v["properties"], v.get("required") or [], key + NEST)
            out.update(sub)
            if k in required:
                req += sub_req
        else:
            out[key] = v
            if k in required:
                req.append(key)
    return out, req


def body_schema(spec, name):
    _, op = operation(spec, name)
    schema = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
    props, required = _flatten(schema.get("properties", {}), list(schema.get("required", [])))
    for k, v in url_params(spec, name).items():   # path and query parameters are filled like body fields
        props[k] = {f: x for f, x in v.items() if not f.startswith("_")}
        if v["_required"] and k not in required:
            required.append(k)
    return props, required


def response_fields(spec, name):
    _, op = operation(spec, name)
    ok = op.get("responses", {}).get("200", {})
    return ok.get("content", {}).get("application/json", {}).get("schema", {}).get("properties", {})


def api_tool(spec, name, *, baked=None, descriptions=None, description="", timeout_ms=20000, max_retries=0,
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
            "description": description or operation(spec, name)[1].get("summary", ""),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "default": tool_url(spec, name)},
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
    params = list(url_params(spec, name))
    if params:   # after the caller's own hook, which may still read or fix these fields
        tool["pre_process_code"] = (pre_hook + "\n" if pre_hook else "") + url_params_hook(params)
    if any(NEST in k for k in props):   # last: every other hook sees the flat leaves
        tool["pre_process_code"] = (tool.get("pre_process_code", "") + "\n" + NEST_HOOK).lstrip("\n")
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
