# B3 Priority #9 (Chatbot + Ringg.AI): Gaps & Open Questions

*Last updated: 2026-11-26 · Branch `cleanup-and-voice-tools` (`1049 passed, 1 skipped`)*

> **Read first:** the `api` and `mw` items marked **[local]** are written and tested on the development machine but **not committed or deployed** in those repos yet. Until they are, they exist only on that machine.

---

## 1. Gaps in the Repo

### A. Bugs & Security / Privacy Gaps (Bot Code)
- [x] **Multi-store prompt bug on max-tool-calls fallback:** Fixed in `bot/router.py` — the final-answer retry now passes `system` (and `tools`), so non–South Jackson stores never fall back to the South Jackson `SYSTEM_PROMPT`.
- [x] **Customer message text written to disk (`logs/review.jsonl`):** Fixed in `bot/router.py`, `bot/events.py`, and `bot/tracing.py` — `log_for_review` no longer writes customer text during HTTP `/chat` turns unless `BOT_LOG_CUSTOMER_TEXT=1` is explicitly set, and `customer_token` is redacted in tracing.
- [x] **`get_order_status` offered in tool schemas to visitors:** Fixed in `bot/tool_schemas.py`, `bot/router.py`, `bot/tracing.py`, and all 4 LLM adapters (`llm_gemini.py`, `llm_groq.py`, `llm_ollama.py`, `llm_openai_compat.py`) — visitors only receive `VISITOR_TOOL_DECLARATIONS` (no `get_order_status`).
- [x] **Ringg tools bypass `BOT_STORE_CONFIG_DIR` (`enabled: false`):** Fixed in `bot/server.py` — `/ringg/tools/{tool}` now checks `stores.config_for(ctx)` and refuses disabled or unconfigured stores with `403`.
- **Single shared keys:**
  - [x] Per-agent `RINGG_TOOLS_KEY` support (`{"<agent_id>": "<secret>"}`) added in `bot/ringg.py` and `bot/server.py` (backwards-compatible with a single string).
  - [x] Per-store `BOT_SERVICE_KEY` support (`{"<store_uuid>": "<secret>"}`) added in `bot/store_config.py` and `bot/server.py` (backwards-compatible with a single string).
- [x] **Duplicate store settings file:** removed `stores/southjacksonfurniture.json` (identical to the UUID-named file; stores are looked up by Store-UUID only).
- [ ] **`stores/athensfurnituremart.json` is named by slug, not Store-UUID:** a real Athens request would be refused. Parked until Athens' Store-UUID is known (work is South Jackson only for now).
- [ ] **Latent `_MD_LINK` bug (kept as-is in the refactor):** `_is_bare_pointer` (`bot/nudges.py`) only strips markdown links, not bare URLs, because the old `guardrails.py` defined `_MD_LINK` twice. Fixing it changes when the "only gave a phone number" nudge and the "I don't have that information" prefix fire, so it needs a test and a decision.

### B. Multi-Store & Catalog Gaps (Bot + RAG)
- **Furniture-only filters & verticals:**
  - [x] Dynamic catalog attribute groups enabled across `bot/tool_schemas.py`, `bot/tools/list_attribute_values.py`, `bot/tools/search_products.py`, and `bot/ringg.py` (with `available_groups` discovery on miss), and `COMPONENT_NAME_PATTERNS` scoped to furniture stores only.
  - [x] Added `electronics`, `veterinary`, and `tires_wheels` rule packs to `bot/verticals.py` alongside `furniture` and `general`.
- **Leftover South Jackson hardcoding in shared helpers:**
  - [x] `rag/text_utils.py` boilerplate contact-footer stripping generalized (no longer hardcodes South Jackson's phone/email).
  - [x] `bot/ringg_prompt.py` shared `@store_name` prompt now uses a store-agnostic `StoreConfig` (`--vertical general|furniture`) without South Jackson's `"Rugs and Decor"` or US-only shipping rule.
- [x] **Mattress index gap (231 vs 569) & broken category paging:** Diagnosed live — the storefront API only has 231 active products in `mattresses-and-bedding` (`beautyrest`=0, `sierra sleep`=0, `nectar`=2; remaining ~338 from old MySQL dump are inactive/removed). The probe also caught that `price_range: {"from": 0}` still hit the API's cached-page-1 bug (`empty(0)` in PHP), now fixed in `bot/api_client.py` by using `sort_by: "az"`.
- **Unverified / partial API integrations:**
  - [x] **Orders verified against a real response (26 Nov):** `GET /orders` for a local test customer (`dev_tools/seed_test_orders.php` creates `bot-test@example.test` with 3 orders; `dev_tools/capture_orders.php` captures and redacts) has every field `customer_orders` reads, under the same names. Saved as `tests/fixtures/api/orders_index_real.json` / `orders_show_real.json` with a test. Live `get_order_status` for that customer: recent orders, one order by number, "no match" for another number, `login_required` when logged out — all correct.
  - [ ] **Item quantities:** the list endpoint does not return them, but `GET /orders/{uuid}` does (`order_details[].product_qty`). Showing quantities would need one detail call per order the customer asks about.
  - [x] **Blog pagination:** Confirmed `GET /blogs/active?page=N&limit=50` live and wired full multi-page pagination into `bot/api_client.py` and `rag/api_ingest.py` (`SJ_BLOG_URL` env override added).
  - [x] **Advanced pages (`is_advanced`) & non-variation attributes/colors/promotions:** Verified live on South Jackson (`0/18` pages are `is_advanced`; `attributes`/`colors`/`promotions` are `[]`; `sizes` duplicates `groupedAttributes`/`options`).
  - [ ] **Remaining API assumptions/limits:** Product URL confirmed & updated to `/p/{slug}` (`SJ_PRODUCT_URL`); blog URL still assumed `/blogs/{slug}` (`SJ_BLOG_URL`, blog route not found on storefront yet); option-priced products have `sku=None`; `max_width_in` is unsupported (`search_products.py`).
  - [ ] **Shipping tool does not use the new endpoint:** `GET /shipping-options` works (South Jackson: USA Local Shipping $299, In-Store Pickup free), but `get_shipping_options` still answers from policy text, so the bot gives no exact shipping prices.
- **South Jackson content (store admin, not code):**
  - [ ] **Returns, Shipping & Delivery and Care & Cleaning pages are switched off** (status not live), so they are not in the search index and the bot correctly says it has no return information. Publish them in the admin, then rerun `python rag/ingest_store.py 56695974416925887669406417382718`.
  - [ ] **"2880 – Armless Chair (1 LEFT)" is filed under Recliners** and its description calls it "a premium recliner", so chat and voice list it as a recliner. Fix its category/description in the admin if it is not one. (A name-based filter in the bot was rejected: 8 of 8 "mattress" results and 7 of 8 "bed" results do not have that word in their names.)
- **Packaging & hygiene:**
  - [x] `mysql-connector-python` moved out of runtime `requirements.txt` into `requirements-dev.txt`.
  - [x] `events/` added to `.gitignore` and untracked from git.
  - [x] One-off eval and check scripts moved from the repo root into `evals/` and `dev_tools/` (run as `python -m evals.eval_accuracy`, `python -m dev_tools.check_cheapest`).
  - [x] `bot/guardrails.py` (737 lines) split into `bot/gates.py`, `bot/nudges.py` and `bot/reply_check.py` (re-exported, no behaviour change); router nudges are one table.
  - [x] `tests/test_ringg.py` failed when run on its own (`.env` reloaded `BOT_STORE_CONFIG_DIR` after the test fixture cleared it); `tests/conftest.py` now loads `.env` first.

### C. Ringg (Phase 2) & Funnel / Dashboard Gaps
- **Funnel logging gaps (`bot/events.py`):**
  - [x] `summarize_tool` now records `filters`, `found`, `results`, and validated slugs for `recommend_products` (AI search), plus `found` and `results` for `get_shipping_options`.
  - [ ] Storefront searches, inquiries, leads/opportunities, actions, and transactions/revenue aren't wired into the funnel log yet (only in-bot searches, chat turns, and Ringg call/tool events are recorded).
  - [ ] Events only append to local daily `.jsonl` files (single-worker; no database/warehouse sink or dashboard read API yet).
- **Ringg agent B3 DEV (`f4e538d8-b803-40a1-aba7-c0d58f104603`):**
  - [x] **Ten on-call API tools attached** through Ringg's workspace MCP (`upsert_api_tool`): `X-Ringg-Tools-Key` static, `Store-UUID` from the call variable `custom_args.store_uuid` (never filled by the model), optional `X-Ringg-Call-Id`.
  - [x] **Prompt mentions tools as `@||tool||`** (Ringg only offers a tool the prompt mentions this way); `python -m bot.ringg_prompt` now writes them, `--plain-tool-names` for the old output.
  - [x] **Tested through Ringg (`test_api_tool`) via ngrok:** store info, product search, policy search, recommendations and shipping all return complete answers for South Jackson.
  - [x] **Ringg cuts tool replies at ~4,000 characters:** `/ringg/tools/*` replies are now fitted under 3,500 (`ringg.fit_for_voice`; policy search 10.5k → 3.3k characters).
  - [x] **First policy question after a restart timed out** (embedding model ~30 s to load vs Ringg's 30 s tool timeout): the bot now preloads it at start (`BOT_PRELOAD=0` turns it off) and keeps one shared Neon connection (policy search ~4.5 s → ~0.6 s).
  - [ ] **Live voice call not yet tested** (use B3 DEV's test call with `store_uuid` and `store_name` set).
  - [ ] **Tool addresses point at the ngrok development URL**; switch them and the webhook to the real bot address once it is hosted.
  - [ ] **Allowed website domains** are ringg.ai, southjacksonfurniture.com and athensfurnituremart.com: the voice widget does not load on `localhost`, and the live storefront domains must be confirmed before launch.
  - [ ] The old agent id that `mw` had hard-coded (`1cddd585-…`) does not exist in Ringg; `mw/.env` must set `RINGG_AGENT_ID` to B3 DEV.
- **Ringg operational gaps:**
  - [ ] No Ringg Call History reconciliation job to backfill missed webhooks (and in-memory `Seen` dedupe resets on process restart).
  - [ ] No outbound follow-up or chat-to-call handoff flows yet.
  - [ ] No reply-level guardrails on Ringg output (architectural limitation — only prompt + live tools apply).
  - [ ] **Voice store choice comes from the page:** the widget's `store_uuid` variable is visible in the page source, so a caller could point the agent at another enabled store (public catalog data only, no orders; mainly a cost/attribution issue).
  - [x] **[local]** Ringg voice widget wired into `mw`'s `ChatWidget.vue` (passes `store_uuid`, `store_name`, `conversation_id`); the Ringg script is pinned to `@desivocal/agents-cdn@1.0.21` instead of `@latest`.

### D. Outside This Repo (`mw`, Storefront API, Ops)
- [x] **[local] `mw` (Phase 1 Step 6):** Chat widget (`resources/js/index/components/ChatWidget.vue` mounted in `FrontendLayout.vue`) + backend proxy (`app/Http/Controllers/ChatController.php`, `POST /chat` and `POST /api/chat`) calling `/chat` with `X-Bot-Key`, `Store-UUID`, `X-Client-Id`, `X-Conversation-Id`, `X-Customer-Uuid`, `X-Customer-Token`. Tested end to end on localhost (mw → bot → API) for South Jackson.
- [x] **[local] `mw` fixes (26 Nov):** removed the hard-coded `BOT_SERVICE_KEY` and Ringg agent id fallbacks from `config/services.php`; the customer id is no longer taken from a browser header; `TRUSTED_PROXIES` setting added (must be set behind a load balancer, or every visitor shares one chat rate limit); chat links starting with `//` are no longer clickable.
- [ ] **`mw` before committing:** leave out the localhost change in `bootstrap/app.php` and the unrelated `codemirror` in `package.json`, and delete the stray untracked files (`json($data)`, `{`, `origin)`, `logAndFail($e)`, …).
- [ ] **Captcha check is silently skipped once the [local] API fix is deployed:** `mw`'s `AuthController::add` (register) verifies Turnstile only if `captcha_secret_key` comes back from `GET /stores`, and the API fix removes it from that response. Before deploying the API change, `mw` must read the secret from its own `.env` and refuse registration when a captcha provider is configured but the secret is missing.
- [ ] **Register and login do not work on `localhost`:** the store's Turnstile site key is bound to its live domain, so the captcha never loads and the buttons stay disabled. For local tests use `dev_tools/seed_test_orders.php` + `capture_orders.php` (no captcha), or set Cloudflare's test keys in the local admin.
- [ ] **The local South Jackson database holds real customers' personal data** (names, emails, orders). Never commit or share an export of it; the dev scripts only accept `@example.*` test addresses.
- [ ] **`RINGG_WEBCALL_KEY` is sent to every visitor's browser** (Inertia page props): confirm it is Ringg's public web-call key, never the workspace API key.
- [x] **Real store setup & hosting:** Created `<store_uuid>.json` configs in `stores/` (`BOT_STORE_CONFIG_DIR`), added `rag/ingest_store.py` runner for per-store RAG ingests, and added `deploy/bot.service` + `deploy/README.md` for AWS single-worker private deployment.
- **API-team items (`readme.txt` §5 & §10):**
  - [x] **[local]** `PATCH orders/{id}` missing auth check — added `Auth::guard('customer-api')->user()` and customer ownership check in `Front\OrderController::update`. **26 Nov:** a customer may now only cancel their own order while it is Pending (any other status change returns 403); no storefront or mobile-app code calls this route.
  - [x] **[local]** `GET /stores` returning Turnstile secret — stripped `captcha_secret_key`, `google_apikey`, and `facebook_app_secret` in `Front\StoreController::index`.
  - [x] **[local]** Product API exposing `base_price` — hidden/omitted `base_price` across `ProductResource`, `ProductService::buildProductQuery`, `Front\ProductController`, and `HomeService::getPopularProducts` (with server-side DB lookup in `PriceConfigService::applyPriceConfigToOrderDetails`).
  - [x] **[local]** Confirming Redis per-store cache prefix in staging/production — updated `DynamicConnectionService::setConnection` to set `store_{uuid}_` prefix and reset Redis + `Cache::forgetDriver()` before any cache read (plus per-store directory scoping for `file` cache driver).
  - [x] **[local]** Fixing unfiltered `/categories/{slug}/products` cache key — updated `Front\ProductController::getProductsByCategory` default cache key to `category:{slug}:default:page:{page}:per_page:{perPage}`.
  - [x] **[local]** Adding a public shipping-options endpoint — added `GET /shipping-options` (`Front\TaxController::shippingOptions`) and `ApiClient.shipping_options()`. **26 Nov:** fixed a 500 (`shipping_zones` has a `zipcodes` column that hid the loaded relation).
  - [x] **[local] Product search speed:** `POST /products/search` accepts `"facets": false` to skip the filter sidebar (bot path ~10 s → ~2 s locally), and with facets on reuses the id list instead of a third `COUNT` query. The bot sends the flag (committed); it has no effect until this API change is deployed.
  - [ ] **`GET /orders/{uuid}` returns `base_price` (the store's cost) to the customer** in `order_details[]`. The earlier fix hid `base_price` in product responses but not here.
  - [ ] **API repo before committing:** the 278-line `composer.lock` change looks unrelated; review it separately.
  - [ ] **Storefront search is still 5–6 s locally**, almost all in the category/attribute sidebar queries. Composite `(product_id, …)` indexes were tried and rolled back (MySQL did not use them). Profile on staging/production data, since the local MySQL is slow in general (1 s to count 108k rows, 128 MB buffer pool).
  - [ ] **Local tooling:** the default `php` on the dev machine is 7.4 and cannot parse the API's PHP 8 code; use Laragon's PHP 8.3.

---

## 2. Open Questions

### Product / Client Decisions
- **Ringg's role in the customer journey:**
  - [x] **Voice vs text chat:** Decided — Ringg for voice/call agents only (no order lookup); our own backend LLM for website text chat.
  - [ ] **Voice channel scope:** Inbound/webcall only, outbound lead follow-up, chat-to-call handoff, or all three?
- [ ] **Guest checkout & orders:** Do B3 stores allow guest checkout? (Decides whether guests are redirected to store contact or need a dedicated order+email API endpoint.)
- [ ] **Storing chat text:** Is storing customer questions allowed with consent/retention? *(Default in HTTP service is now OFF unless `BOT_LOG_CUSTOMER_TEXT=1`.)*
- [ ] **Dashboard location & event storage:** Where do the dashboard and its API live (existing API repo or separate app), and where should funnel events be stored long-term (Postgres/ClickHouse/S3 instead of local `.jsonl` files)?
- [ ] **AI provider & monthly budget — now the blocker for website chat:** the current Groq key allows **8,000 tokens per minute** for the whole service, and one model call costs ~3,650 tokens before any conversation (system prompt 2,416 + tool definitions 1,233). That is about one chat answer per minute across all visitors and stores (measured turns took 56–120 s). Trimming the prompt cannot fix this; a paid tier or another provider can. Voice is far less affected (Ringg's own model talks; it only calls our tools). Candidates: Mistral `ministral-8b` / OpenAI `gpt-4o-mini` / Gemini `gemini-2.5-flash` / Groq paid tier.
- [ ] **Neon autosuspend:** the RAG database on Neon suspends after ~5 idle minutes; the next policy question then takes ~1.5–3 s while it wakes and the bot reconnects (well inside Ringg's 30 s limit). Keeping it awake costs Neon compute time — a cost decision, not a code change.

### API / `mw` Technical Questions
- [ ] **Real `/orders` response:** Capture one real test-customer `GET /orders` (and `GET /orders/{id}`) response to confirm exact field names and add item quantities.
- [ ] **Storefront routes:** Product URL confirmed & updated to `/p/{slug}`; blog storefront URL path still unconfirmed (currently `/blogs/{slug}`, blogs remain opt-in).
- [ ] **Bot-to-API authentication:** the bot already sends `BOT_API_KEY` in the header named by `BOT_API_KEY_HEADER` (`bot/api_client.py`); agree on the header name and value with the API team, and have the API check it.
- [x] **Public shipping-options endpoint:** added as `GET /shipping-options` [local] (see 1.D); the bot's shipping tool does not use it yet (see 1.B).
- [x] **API cache (API team):** unfiltered category cache key and Redis per-store prefix fixed [local] (see 1.D); the bot's `sort_by="az"` workaround can stay until that is deployed.
- [x] **Mattress count (231 vs 569):** Confirmed live via `dev_tools/probe_api_gaps.py` — API has 231 active products; missing ~338 from old dump are no longer active in the API.
- [ ] **Future pricing (Priorities #3 & #7):** Confirm response fields once `customer_groups_price` and monthly/weekly pricing land in the API.

### Deployment / Ops Questions
- [ ] **AWS hosting & scaling:** Decide where on AWS the bot service runs and who deploys it. *(Note: rate limits, concurrency gate, and Ringg webhook dedupe are in-memory today, so multi-worker scaling requires Redis.)* Once hosted: switch B3 DEV's ten tool URLs and webhook from ngrok to the real address, and set `TRUSTED_PROXIES` in `mw`.
- [ ] **Commit and deploy the [local] `api` and `mw` changes** (see 1.D), excluding the local-only and unrelated files listed there.
- [ ] **Priority #1 traffic filtering:** Confirm foreign/bot traffic is blocked before reaching `mw` / the Ringg widget so it does not inflate funnel counts or Ringg session costs.
