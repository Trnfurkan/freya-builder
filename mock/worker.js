// The team's mock of the bank's services, for Cloudflare Workers (one file, no build step, no bindings).
// Deploy: Cloudflare dashboard > Workers & Pages > Create > Worker > Edit code > paste this file > Deploy.
//
// Shape (the same as the kit's tools/openapi.json, which builder/openapi_tools.py reads):
//   POST <basePath>/<operation>   JSON body in, JSON out, HTTP 200 with ok true/false, HTTP 500 = service failure.
//   GET  <basePath>/records?customer_id=...   what the mock recorded for a customer (for our own checks).
// Fill CONFIG from the case's service contract and test customers, and change an operation in OPS only where the
// contract says something else. Every value comes from the case's documents; nothing here is a final-case value.
//
// State lives in this worker's memory: it is per instance and is lost when the worker restarts. Enough for a call,
// which is short; if the contract needs records kept longer, bind a KV namespace and store `state` there.

const CONFIG = {
  basePath: "/v1",
  // Optional shared secret: when keyHeader is set, a request without the right value gets 401.
  keyHeader: "",
  key: "",
  identityFields: ["date_of_birth", "phone_last4"],   // the contract's security questions, in order
  maxWrongAnswers: 3,                                  // wrong answers in total that end the check
  customers: {
    // "c_example": {
    //   date_of_birth: "1988-03-14", phone_last4: "4471",
    //   products: [{ product_id: "p1", type: "credit_card", card_last4: "4821", past_due: 420.0, days_late: 6,
    //                minimum_payment: 120.0 }],
    // },
  },
  // Service failures the test customers expect: { "<customer_id>": { "<operation>": [1] } } fails the 1st call.
  failures: {},
};

const state = new Map();

function record(cid) {
  if (!state.has(cid)) {
    state.set(cid, { calls: {}, wrong: 0, passed: {}, promises: {}, notes: [], callbacks: [], transfers: [],
                     closed: null });
  }
  return state.get(cid);
}

function reply(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function refuse(error, next) {
  return { ok: false, error, what_to_do_next: next || ("The bank did not carry this out: " + error + ".") };
}

function isoDate(s) {
  return typeof s === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s) && !isNaN(Date.parse(s + "T00:00:00Z"));
}

function verified(r) {
  return CONFIG.identityFields.every((f) => r.passed[f]);
}

const OPS = {
  verify_identity(b, c, r) {
    if (!CONFIG.identityFields.includes(b.field)) return refuse("unknown field");
    if (r.wrong >= CONFIG.maxWrongAnswers) return refuse("the check has ended", "Do not ask again; follow the rule for a failed check.");
    let answer = String(b.answer || "").trim();
    let want = String(c[b.field] || "");
    if (b.field === "date_of_birth" && !isoDate(answer)) return refuse("unreadable date", "Send the date as YYYY-MM-DD.");
    if (b.field !== "date_of_birth") answer = answer.replace(/\D/g, "");
    if (answer === want) {
      r.passed[b.field] = true;
      return { ok: true, verified: true, all_verified: verified(r), wrong_answers_left: CONFIG.maxWrongAnswers - r.wrong };
    }
    if (b.field === "date_of_birth") {
      const [y, m, d] = answer.split("-");
      if (y + "-" + d + "-" + m === want) return { ok: true, verified: false, hint: "day_month_swapped", all_verified: false,
                                                   wrong_answers_left: CONFIG.maxWrongAnswers - r.wrong };
    }
    r.wrong += 1;
    return { ok: true, verified: false, all_verified: false, wrong_answers_left: Math.max(0, CONFIG.maxWrongAnswers - r.wrong) };
  },

  get_account(b, c, r) {
    if (!verified(r)) return refuse("identity not verified", "Pass every security question first.");
    return { ok: true, products: c.products || [] };
  },

  create_promise(b, c, r) {
    if (!verified(r)) return refuse("identity not verified");
    const p = (c.products || []).find((x) => x.product_id === b.product_id);
    if (!p) return refuse("unknown product_id", "Use a product_id from get_account.");
    if (r.promises[b.product_id]) return refuse("this product already has a promise", "It is recorded; do not repeat it.");
    if (!isoDate(b.date)) return refuse("date must be YYYY-MM-DD");
    const amount = Number(b.amount);
    if (!(amount > 0)) return refuse("amount must be a positive number");
    if (p.past_due !== undefined && amount > Number(p.past_due) + 1e-9) return refuse("amount above the past-due amount");
    const id = "pr_" + (Object.keys(r.promises).length + 1) + "_" + b.product_id;
    r.promises[b.product_id] = { promise_id: id, product_id: b.product_id, amount, date: b.date };
    return { ok: true, promise_id: id };
  },

  add_note(b, c, r) {
    r.notes.push({ product_id: b.product_id, reason: b.reason, text: b.text || "" });
    return { ok: true };
  },

  schedule_callback(b, c, r) {
    if (!isoDate(b.date) || !/^\d{2}:\d{2}$/.test(String(b.time || ""))) return refuse("date YYYY-MM-DD and time HH:MM");
    r.callbacks.push({ date: b.date, time: b.time });
    return { ok: true };
  },

  transfer_to_agent(b, c, r) {
    r.transfers.push({ queue: b.queue });
    return { ok: true };
  },

  close_call(b, c, r) {
    if (r.closed) return refuse("the call is already closed", "Do not call close_call again; end the call.");
    r.closed = { outcome: b.outcome, reason: b.reason || null };
    return { ok: true };
  },
};

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (CONFIG.keyHeader && request.headers.get(CONFIG.keyHeader) !== CONFIG.key) return reply({ ok: false, error: "unauthorized" }, 401);
    if (!url.pathname.startsWith(CONFIG.basePath + "/")) return reply({ ok: false, error: "not found" }, 404);
    const op = url.pathname.slice(CONFIG.basePath.length + 1).replace(/\/+$/, "");
    if (request.method === "GET" && op === "records") {
      const cid = url.searchParams.get("customer_id") || "";
      return reply({ ok: true, customer_id: cid, records: state.get(cid) || null });
    }
    if (request.method !== "POST" || !OPS[op]) return reply({ ok: false, error: "unknown operation " + op }, 404);
    let body;
    try {
      body = await request.json();
    } catch (e) {
      return reply(refuse("the body is not JSON"));
    }
    const cid = String(body.customer_id || "");
    const customer = CONFIG.customers[cid];
    if (!customer) return reply(refuse("unknown customer_id"));
    const r = record(cid);
    r.calls[op] = (r.calls[op] || 0) + 1;
    const fails = ((CONFIG.failures[cid] || {})[op]) || [];
    if (fails.includes(r.calls[op])) return reply({ error: "service failure" }, 500);
    try {
      return reply(OPS[op](body, customer, r));
    } catch (e) {
      return reply({ error: "service failure" }, 500);
    }
  },
};
