// The team's mock of the bank's services, for Cloudflare Workers (one file, no build step, no bindings).
// Deploy: Cloudflare dashboard > Workers & Pages > Create > Worker > Edit code > paste this file > Deploy.
//
// Fill CONFIG from the case's documents only:
//   routes    the bank's endpoints, exactly as its integration document gives them: method, path ({name} = a path
//             parameter), and the name used for it in fixtures and handlers.
//   fixtures  the answers the bank lists for its test customers: which input they answer (match: every key must equal
//             the request's value; path, query and JSON body fields are merged into one input), the HTTP status and
//             the body, as listed. A fixture with `responses: [{status, body}, ...]` answers in that order, one per call
//             (for example a service failure, then a success); the last one repeats.
//   fallback  the answer when no fixture matches (the document's own answer for an unknown customer, if it gives one).
// The most specific fixture wins (the one with the most match keys). HANDLERS may compute an answer for an operation
// whose result depends on earlier calls (a change, then a read of it); a handler's answer replaces the fixture's.
//
// State lives in this worker's memory: per instance, lost on restart. Enough for one call.
// GET /__records lists the calls the mock received (newest last), for our own checks.

const CONFIG = {
  basePath: "",
  routes: [
    // { method: "GET", path: "/customers/{customerNumber}/cards", op: "cards" },
  ],
  fixtures: [
    // { op: "cards", match: { customerNumber: "00000000" }, status: 200, body: { cards: [] } },
  ],
  fallback: { status: 404, body: { error: "not found" } },
};

const HANDLERS = {
  // op(input, state, fixtureAnswer) -> {status, body} or null to keep the fixture's answer
};

const state = { calls: [], counters: {}, data: {} };

function reply(status, body) {
  return new Response(body === undefined ? "" : JSON.stringify(body),
                      { status, headers: { "content-type": "application/json; charset=utf-8" } });
}

function matchPath(template, path) {
  const names = [];
  const re = new RegExp("^" + template.replace(/[.*+?^$()|[\]\\]/g, "\\$&")
    .replace(/\{([^}/]+)\}/g, (_, n) => { names.push(n); return "([^/]+)"; }) + "/?$");
  const m = path.match(re);
  if (!m) return null;
  const out = {};
  names.forEach((n, i) => { out[n] = decodeURIComponent(m[i + 1]); });
  return out;
}

function same(a, b) {
  return String(a) === String(b);
}

function pick(op, input) {
  let best = null;
  for (const f of CONFIG.fixtures) {
    if (f.op !== op) continue;
    const keys = Object.keys(f.match || {});
    if (!keys.every((k) => k in input && same(input[k], f.match[k]))) continue;
    if (!best || keys.length > Object.keys(best.match || {}).length) best = f;
  }
  return best;
}

function answer(f, op) {
  if (!f.responses) return { status: f.status || 200, body: f.body };
  const key = op + JSON.stringify(f.match || {});
  const n = state.counters[key] || 0;
  state.counters[key] = n + 1;
  const r = f.responses[Math.min(n, f.responses.length - 1)];
  return { status: r.status || 200, body: r.body };
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    let path = url.pathname;
    if (request.method === "GET" && path === CONFIG.basePath + "/__records") return reply(200, { calls: state.calls });
    if (CONFIG.basePath && path.startsWith(CONFIG.basePath)) path = path.slice(CONFIG.basePath.length) || "/";
    let route = null, params = null;
    for (const r of CONFIG.routes) {
      if (r.method.toUpperCase() !== request.method) continue;
      params = matchPath(r.path, path);
      if (params) { route = r; break; }
    }
    if (!route) {   // kept in the records: a tool calling a wrong path or method shows up here
      state.calls.push({ at: new Date().toISOString(), op: null, method: request.method, path: url.pathname + url.search,
                         status: CONFIG.fallback.status });
      if (state.calls.length > 500) state.calls.shift();
      return reply(CONFIG.fallback.status, CONFIG.fallback.body);
    }
    let body = {};
    if (!["GET", "HEAD"].includes(request.method)) {
      const text = await request.text();
      if (text) {
        try { body = JSON.parse(text); } catch (e) { body = { _raw: text }; }
      }
    }
    const input = Object.assign({}, Object.fromEntries(url.searchParams), typeof body === "object" && body ? body : {}, params);
    const f = pick(route.op, input);
    let out = f ? answer(f, route.op) : { status: CONFIG.fallback.status, body: CONFIG.fallback.body };
    if (HANDLERS[route.op]) {
      try {
        out = HANDLERS[route.op](input, state.data, out) || out;
      } catch (e) {
        out = { status: 500, body: { error: "mock handler failed" } };
      }
    }
    state.calls.push({ at: new Date().toISOString(), op: route.op, input, status: out.status });
    if (state.calls.length > 500) state.calls.shift();
    return reply(out.status, out.body);
  },
};
