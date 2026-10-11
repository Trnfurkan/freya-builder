# The team's mock of the bank

The final case's agent calls the team's own mock of the bank's services, on Cloudflare Workers. It is written from the
case's service contract and test customers, after the case opens, and runs until the results are out.

1. **Tool API file.** From the service contract, write `openapi.json` in the kit's shape (`tools/openapi.json`): one
   `POST /<operation>` per operation, request fields with exact names, types and enums, `servers[0].url` = the mock's
   address with its base path. The builder reads every URL, field and enum from it.
2. **Mock.** In `mock/worker.js`, fill `CONFIG` (base path, security questions, test customers, expected service
   failures) and change an operation in `OPS` only where the contract differs. Same answers as the tool API file:
   HTTP 200 with `ok` true/false (`error`, `what_to_do_next` when false), HTTP 500 for a service failure.
3. **Deploy.** Cloudflare dashboard > Workers & Pages > Create > Worker > Edit code > paste `worker.js` > Deploy.
   The address is `https://<name>.<subdomain>.workers.dev`.
4. **Check.** `python mock/check_mock.py https://<name>.<subdomain>.workers.dev/v1 --openapi <file> --customer <test id>`
   and `GET .../v1/records?customer_id=<id>`.
5. **Build.** `python -m builder.run <case folder> --openapi <file> --mock-url <address>/v1 --slot final --export`.
