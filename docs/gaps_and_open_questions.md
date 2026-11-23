# B3 Priority #9 (Chatbot + Ringg.AI): Gaps & Open Questions

*Last updated: 2026-11-19 · Repo commit `cc8514c` (`950 passed, 1 skipped`)*

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

### B. Multi-Store & Catalog Gaps (Bot + RAG)
- **Furniture-only filters & verticals:**
  - [x] Dynamic catalog attribute groups enabled across `bot/tool_schemas.py`, `bot/tools/list_attribute_values.py`, `bot/tools/search_products.py`, and `bot/ringg.py` (with `available_groups` discovery on miss), and `COMPONENT_NAME_PATTERNS` scoped to furniture stores only.
  - [x] Added `electronics`, `veterinary`, and `tires_wheels` rule packs to `bot/verticals.py` alongside `furniture` and `general`.
- **Leftover South Jackson hardcoding in shared helpers:**
  - [x] `rag/text_utils.py` boilerplate contact-footer stripping generalized (no longer hardcodes South Jackson's phone/email).
  - [x] `bot/ringg_prompt.py` shared `@store_name` prompt now uses a store-agnostic `StoreConfig` (`--vertical general|furniture`) without South Jackson's `"Rugs and Decor"` or US-only shipping rule.
- [x] **Mattress index gap (231 vs 569) & broken category paging:** Diagnosed live — the storefront API only has 231 active products in `mattresses-and-bedding` (`beautyrest`=0, `sierra sleep`=0, `nectar`=2; remaining ~338 from old MySQL dump are inactive/removed). The probe also caught that `price_range: {"from": 0}` still hit the API's cached-page-1 bug (`empty(0)` in PHP), now fixed in `bot/api_client.py` by using `sort_by: "az"`.
- **Unverified / partial API integrations:**
  - [ ] **Orders:** `customer_orders` is tested only against a synthetic fixture (`tests/fixtures/api/orders_index_from_controller_code.json`) — never verified against a real customer `/orders` response; no item quantities yet.
  - [x] **Blog pagination:** Confirmed `GET /blogs/active?page=N&limit=50` live and wired full multi-page pagination into `bot/api_client.py` and `rag/api_ingest.py` (`SJ_BLOG_URL` env override added).
  - [x] **Advanced pages (`is_advanced`) & non-variation attributes/colors/promotions:** Verified live on South Jackson (`0/18` pages are `is_advanced`; `attributes`/`colors`/`promotions` are `[]`; `sizes` duplicates `groupedAttributes`/`options`).
  - [ ] **Remaining API assumptions/limits:** Product URL confirmed & updated to `/p/{slug}` (`SJ_PRODUCT_URL`); blog URL still assumed `/blogs/{slug}` (`SJ_BLOG_URL`, blog route not found on storefront yet); option-priced products have `sku=None`; `max_width_in` is unsupported (`search_products.py`).
- **Packaging & hygiene:**
  - [x] `mysql-connector-python` moved out of runtime `requirements.txt` into `requirements-dev.txt`.
  - [x] `events/` added to `.gitignore` and untracked from git.

### C. Ringg (Phase 2) & Funnel / Dashboard Gaps
- **Funnel logging gaps (`bot/events.py`):**
  - [x] `summarize_tool` now records `filters`, `found`, `results`, and validated slugs for `recommend_products` (AI search), plus `found` and `results` for `get_shipping_options`.
  - [ ] Storefront searches, inquiries, leads/opportunities, actions, and transactions/revenue aren't wired into the funnel log yet (only in-bot searches, chat turns, and Ringg call/tool events are recorded).
  - [ ] Events only append to local daily `.jsonl` files (single-worker; no database/warehouse sink or dashboard read API yet).
- **Ringg operational gaps:**
  - [ ] No Ringg Call History reconciliation job to backfill missed webhooks (and in-memory `Seen` dedupe resets on process restart).
  - [ ] No outbound follow-up or chat-to-call handoff flows yet.
  - [ ] No reply-level guardrails on Ringg output (architectural limitation — only prompt + live tools apply).
  - [ ] No Ringg widget embed in `mw` passing `store_uuid`, `store_name`, `conversation_id`.

### D. Outside This Repo (`mw`, Storefront API, Ops)
- [x] **`mw` (Phase 1 Step 6):** Chat widget (`resources/js/index/components/ChatWidget.vue` mounted in `FrontendLayout.vue`) + backend proxy (`app/Http/Controllers/ChatController.php`, `POST /chat` and `POST /api/chat`) calling `/chat` with `X-Bot-Key`, `Store-UUID`, `X-Client-Id`, `X-Conversation-Id`, `X-Customer-Uuid`, `X-Customer-Token`.
- [x] **Real store setup & hosting:** Created `<store_uuid>.json` configs in `stores/` (`BOT_STORE_CONFIG_DIR`), added `rag/ingest_store.py` runner for per-store RAG ingests, and added `deploy/bot.service` + `deploy/README.md` for AWS single-worker private deployment.
- **API-team items (`readme.txt` §5 & §10):**
  - [x] `PATCH orders/{id}` missing auth check — added `Auth::guard('customer-api')->user()` and customer ownership check in `Front\OrderController::update`.
  - [x] `GET /stores` returning Turnstile secret — stripped `captcha_secret_key`, `google_apikey`, and `facebook_app_secret` in `Front\StoreController::index`.
  - [x] Product API exposing `base_price` — hidden/omitted `base_price` across `ProductResource`, `ProductService::buildProductQuery`, `Front\ProductController`, and `HomeService::getPopularProducts` (with server-side DB lookup in `PriceConfigService::applyPriceConfigToOrderDetails`).
  - [x] Confirming Redis per-store cache prefix in staging/production — updated `DynamicConnectionService::setConnection` to set `store_{uuid}_` prefix and reset Redis + `Cache::forgetDriver()` before any cache read (plus per-store directory scoping for `file` cache driver).
  - [x] Fixing unfiltered `/categories/{slug}/products` cache key — updated `Front\ProductController::getProductsByCategory` default cache key to `category:{slug}:default:page:{page}:per_page:{perPage}`.
  - [x] Adding a public shipping-options endpoint (optional) — added `GET /shipping-options` (`Front\TaxController::shippingOptions`) and `ApiClient.shipping_options()`.

---

## 2. Open Questions

### Product / Client Decisions
- **Ringg's role in the customer journey:**
  - [x] **Voice vs text chat:** Decided — Ringg for voice/call agents only (no order lookup); our own backend LLM for website text chat.
  - [ ] **Voice channel scope:** Inbound/webcall only, outbound lead follow-up, chat-to-call handoff, or all three?
- [ ] **Guest checkout & orders:** Do B3 stores allow guest checkout? (Decides whether guests are redirected to store contact or need a dedicated order+email API endpoint.)
- [ ] **Storing chat text:** Is storing customer questions allowed with consent/retention? *(Default in HTTP service is now OFF unless `BOT_LOG_CUSTOMER_TEXT=1`.)*
- [ ] **Dashboard location & event storage:** Where do the dashboard and its API live (existing API repo or separate app), and where should funnel events be stored long-term (Postgres/ClickHouse/S3 instead of local `.jsonl` files)?
- [ ] **AI provider & monthly budget:** Which LLM provider/model is approved for production chat (Mistral `ministral-8b` / OpenAI `gpt-4o-mini` / Gemini `gemini-2.5-flash` / Groq) and what is the monthly budget cap?

### API / `mw` Technical Questions
- [ ] **Real `/orders` response:** Capture one real test-customer `GET /orders` (and `GET /orders/{id}`) response to confirm exact field names and add item quantities.
- [ ] **Storefront routes:** Product URL confirmed & updated to `/p/{slug}`; blog storefront URL path still unconfirmed (currently `/blogs/{slug}`, blogs remain opt-in).
- [ ] **Bot-to-API authentication:** Agree on header name and mechanism for `BOT_API_KEY` (`bot/api_client.py`).
- [ ] **Public shipping-options endpoint:** Can the API team add a read-only public shipping/pickup endpoint, or stick with shipping policy text?
- [ ] **API cache (API team):** Fix the unfiltered category cache key (now worked around via `sort_by="az"` in `bot/api_client.py`) and verify the Redis per-store cache prefix in staging/production. *(Blog pagination via `?page=N&limit=M` is confirmed and fixed.)*
- [x] **Mattress count (231 vs 569):** Confirmed live via `dev_tools/probe_api_gaps.py` — API has 231 active products; missing ~338 from old dump are no longer active in the API.
- [ ] **Future pricing (Priorities #3 & #7):** Confirm response fields once `customer_groups_price` and monthly/weekly pricing land in the API.

### Deployment / Ops Questions
- [ ] **AWS hosting & scaling:** Decide where on AWS the bot service runs and who deploys it. *(Note: rate limits, concurrency gate, and Ringg webhook dedupe are in-memory today, so multi-worker scaling requires Redis.)*
- [ ] **Priority #1 traffic filtering:** Confirm foreign/bot traffic is blocked before reaching `mw` / the Ringg widget so it does not inflate funnel counts or Ringg session costs.
