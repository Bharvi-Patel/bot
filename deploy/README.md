# Store Setup, RAG Ingestion & AWS Deployment Guide

## 1. Per-Store Configuration (`BOT_STORE_CONFIG_DIR`)

Each store enabled for the assistant has a JSON file `<store_uuid>.json` in [`stores/`](../stores):

- [`stores/56695974416925887669406417382718.json`](../stores/56695974416925887669406417382718.json) — South Jackson Furniture
- [`stores/athensfurnituremart.json`](../stores/athensfurnituremart.json) — Athens Furniture Mart

Format validated by `bot/store_config.py`:
```json
{
  "store_name": "South Jackson Furniture",
  "allowed_domains": ["southjacksonfurniture.com"],
  "vertical": "furniture",
  "enabled": true,
  "shipping_example": "We currently ship only within the USA",
  "scrub_terms": ["brooklyn", "jamaica"],
  "catalog_rules": []
}
```
Supported verticals: `general`, `furniture`, `electronics`, `veterinary`, `tires_wheels`.

---

## 2. RAG Database Schema & Per-Store Ingestion

1. **Apply schema / migrate `store_uuid` column (one-time):**
   ```bash
   python rag/apply_schema.py
   # Or if migrating an existing single-store table:
   python rag/migrate_add_store_uuid.py 56695974416925887669406417382718
   ```

2. **Run RAG ingestion for one store or all enabled stores in `stores/`:**
   ```bash
   # Single store (FAQs, policy/guide pages, and all root product categories):
   python rag/ingest_store.py 56695974416925887669406417382718

   # All enabled stores in BOT_STORE_CONFIG_DIR (./stores):
   python rag/ingest_store.py --all --config-dir stores
   ```

---

## 3. Deploying on AWS (1 Worker, Private Address Next to API)

1. Provision the directory and virtualenv on the AWS host (same host or private subnet as `mw`/`api`):
   ```bash
   sudo mkdir -p /opt/b3-bot/events
   sudo rsync -a ./ /opt/b3-bot/
   python3 -m venv /opt/b3-bot/.venv
   /opt/b3-bot/.venv/bin/pip install -r /opt/b3-bot/requirements.txt
   ```

2. Configure `/opt/b3-bot/.env`:
   ```env
   BOT_SERVICE_KEY=<shared-secret-or-per-store-json-map>
   BOT_STORE_CONFIG_DIR=/opt/b3-bot/stores
   BOT_EVENTS_DIR=/opt/b3-bot/events
   BOT_API_BASE_URL=http://127.0.0.1:8000/api
   RAG_DB_URL=postgresql://...
   SJ_LLM_PROVIDER=groq
   SJ_LLM_MODEL=openai/gpt-oss-20b
   GROQ_API_KEY=<key>
   ```

3. Install and start the systemd service ([`deploy/bot.service`](bot.service)):
   ```bash
   sudo cp /opt/b3-bot/deploy/bot.service /etc/systemd/system/bot.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now bot.service
   curl -s http://127.0.0.1:8100/health
   ```

4. Configure `mw`'s `.env` to point at the private address:
   ```env
   BOT_SERVICE_URL=http://127.0.0.1:8100
   BOT_SERVICE_KEY=<same-secret>
   BOT_SERVICE_TIMEOUT=30
   BOT_CHAT_ENABLED=true
   ```
