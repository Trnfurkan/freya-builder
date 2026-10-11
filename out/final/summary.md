# Summary: slot final (Freya Bank credit card statement debt)

- workflow_id: f854506f-3bf1-421b-a1b8-c0c4aa174aa0
- agent_id: 3bf29ea8-7244-4eef-b59a-7d98dc1c5985
- Saved version: v6 (`out/final/v6_build_workflow.py` = `build_workflow.py`), platform version 6, chat-tested (table below).
- Rules: `out/final/rules.md` (74 numbered rules, the source that decided each one, sequences, open points).
- No practice customers exist for this brief; tested only with `builder.chat_test`-style chats (`out/final/dbg_chat.py`,
  same chat API, plus the call transcript) against the team mock, test customers' variables, now 2026-10-12T10:30+03:00.

## Design
- Engine-run bank calls (api_request nodes, all fields baked; the model never fills a bank field): cards_list ->
  cards_balance -> contact_email_get (once, before any offer) -> statement_week_get / decide / change, statement_pdf_send.
- Code nodes build every fixed sentence from the service answers (debt reading with the three TL cases, foreign
  currencies, no-debt sentence, due date / "bugündür", no-statement-debt wording, offer variants, week state, options,
  confirmation with the joint-statement sentence before the question, change result, e-mail question). Amounts, dates
  and last four digits are spoken in words with the kit's tr_speech functions; currency names in full.
- The model only: picks the card (tool-call transitions pick_card_k), answers short questions, re-asks unclear answers.
  Routing runs on extracted enums and code-written state; every exit is an end node with the exact sentence.
- Global nodes: agent, payment, dispute, other language, someone else's card (intent); out-of-scope question, three
  failed tries, silence, complaint after three calming replies (model-decided tool-call transitions).

## Tests (v2-v6, chat against the team mock; v6 re-checked EX1, EX3+e-mail, EX5, first-message agent request)
| Case | Customer | Result |
|---|---|---|
| EX1 single card, below-minimum TL, Euro, thanks -> main menu | 95004361 | ok |
| EX2 / EX5 week change, "olur herhalde" re-asked, evet -> change, preview dates | 96158684 | ok |
| EX3 "all my cards" twice, then Yıldız Premium; other card loop | 97526380 | ok |
| EX4 last month's statement -> only latest -> yes -> sent | 92460108 | ok |
| EX6 no e-mail: offer only week; first 12 / 6 digits refused | 96125950 | ok |
| Same name + same last four -> last six -> full number -> right card | 92030487 | ok |
| Joint statement (main card) week change with linked-card sentence before the question | 92220795 | ok |
| No eligible card (404) / card list 500 | 93821776 / 97073972 | ok / ok |
| Week preview 500 -> week error sentence | 94339861 | ok |
| Spouse's card -> security sentence, end | 93075404 | ok (v4) |
| Out-of-scope (limit) -> question -> "no" -> back | 98390128 | ok, but the debt reading is said again |
| Angry 3 calming replies -> agent | 97116902 | ok |
| Branch hours -> out-of-scope question (v4 sent it to payment; fixed v5) | 95004361 | ok (v5) |
| "I want to pay" after reading -> payment transfer | 98390128 | ok (v5) |
| Three unclear answers at card choice -> failure line via turn cap | 97526380 | ok (v5, after 6 turns) |
| Agent request as the very first message -> agent sentence | 92460108 | ok (v6) |
| EX5 week named with the request, "olur herhalde" re-asked, evet -> changed | 96158684 | ok (v6) |
| No e-mail customer asks for e-mail | 94907600 | ok |

## Open risks
- A card named in the caller's very first message still gets the selection question once (an LLM opening line that
  takes the pick transition printed the tool call as text in v3; reverted in v4).
- After an out-of-scope "no", the node is re-entered and its fixed line (for read_debt: the whole reading) is repeated.
- Silence and "Bilgilerinizi kontrol ediyorum." cannot be shown in text tests; silence is prompt-driven.
- First message (before any conversation node) is checked only for agent, payment and other language (cards_check
  intent transitions, v6); a dispute or someone else's card in the very first message is handled on the next turn.
- The model did not take the three-tries transition by itself; the turn caps (4, card choice 6) end with the failure line.
- Statement-week yearly right cannot be checked before offering (no service); a refused change gives the week-error sentence.
