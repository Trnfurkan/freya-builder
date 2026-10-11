# The team's mock of the bank

The final case's agent calls the team's own mock of the bank's services, on Cloudflare Workers. It is written from the
case's documents (the integration document's endpoints, the listed answers for the test customers), after the case
opens, and runs until the results are out.

1. **Tool API file.** From the integration document, write `mock/openapi.json`: the bank's own paths and methods, an
   `operationId` per operation (the tool's name), path and query `parameters`, request bodies with the exact field
   names, types and enums, the response shapes, and `servers[0].url` = the mock's address. The builder reads every URL,
   method, field and enum from it (`builder/openapi_tools.py`).
2. **Mock.** In `mock/worker.js`, fill `CONFIG.routes` (method, path, op) from the same document and
   `CONFIG.fixtures` from the test customers' listed answers (match, HTTP status, body). Add a `HANDLERS` entry only
   where an answer depends on an earlier call in the same call.
3. **Deploy.** Cloudflare dashboard > Workers & Pages > Create > Worker > Edit code > paste `worker.js` > Deploy.
   The address is `https://<name>.<subdomain>.workers.dev`.
4. **Check.** `python mock/check_mock.py <address> --openapi mock/openapi.json --value <field>=<test value>` sends the
   requests the agent's tools will send; `GET <address>/__records` lists what the mock received.
5. **Build.** `python builder/build.py <case folder> <address>` (see `builder/build.py`).
