"""Run the per-store RAG ingestion and sync pipeline for one store UUID or every enabled store in BOT_STORE_CONFIG_DIR.

Usage:
    python rag/ingest_store.py <store_uuid> [--include-blogs] [--skip-products] [--slug <category_slug> ...]
    python rag/ingest_store.py --all [--config-dir stores] [--include-blogs] [--skip-products]
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.api_client import ApiClient, ApiConfig
from bot.store_config import JsonDirSource, valid_store_id


def discover_enabled_stores(config_dir: pathlib.Path) -> list[str]:
    if not config_dir.is_dir():
        raise SystemExit(f"config directory does not exist: {config_dir}")
    source = JsonDirSource(config_dir)
    stores: list[str] = []
    for path in sorted(config_dir.glob("*.json")):
        store_uuid = path.stem
        if not valid_store_id(store_uuid):
            continue
        cfg = source.get(store_uuid)
        if cfg and cfg.enabled:
            stores.append(store_uuid)
    return stores


def root_category_slugs(store_uuid: str) -> list[str]:
    client = ApiClient(ApiConfig.from_env(store_uuid=store_uuid))
    tree = client.category_tree()
    return [c["slug"] for c in tree if isinstance(c, dict) and c.get("slug")]


def run_step(cmd: list[str]) -> None:
    print(f"-> {' '.join(cmd)}", flush=True)
    res = subprocess.run(cmd, cwd=ROOT)
    if res.returncode != 0:
        raise SystemExit(f"command failed with exit code {res.returncode}: {' '.join(cmd)}")


def ingest_one_store(store_uuid: str, include_blogs: bool = False, skip_products: bool = False,
                     slugs: list[str] | None = None, dry_run: bool = False) -> None:
    if not valid_store_id(store_uuid):
        raise SystemExit(f"invalid store_uuid: {store_uuid!r}")

    py = sys.executable
    ingest_types = "faq,page,blog" if include_blogs else "faq,page"
    sync_types = "policy,guide,faq,blog" if include_blogs else "policy,guide,faq"

    run_step([py, "rag/api_ingest.py", store_uuid, "--types", ingest_types])
    if not dry_run:
        run_step([py, "rag/sync.py", store_uuid, "--types", sync_types])

    if not skip_products:
        target_slugs = list(slugs or []) or root_category_slugs(store_uuid)
        if not target_slugs:
            print(f"[{store_uuid}] no root categories returned by API; skipping product ingest", flush=True)
            return
        prod_cmd = [py, "rag/api_products.py", store_uuid]
        for s in target_slugs:
            prod_cmd.extend(["--slug", s])
        run_step(prod_cmd)
        if not dry_run:
            run_step([py, "rag/sync_products.py", store_uuid])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest and sync RAG data per store from the storefront API.")
    parser.add_argument("store_uuid", nargs="?", help="Store UUID to ingest (omit when using --all)")
    parser.add_argument("--all", action="store_true", help="Ingest all enabled stores in BOT_STORE_CONFIG_DIR")
    parser.add_argument("--config-dir", default=os.getenv("BOT_STORE_CONFIG_DIR", str(ROOT / "stores")),
                        help="Path to store config directory (default: BOT_STORE_CONFIG_DIR or ./stores)")
    parser.add_argument("--include-blogs", action="store_true", help="Also ingest and sync active blog posts")
    parser.add_argument("--skip-products", action="store_true", help="Only ingest/sync FAQs and pages (skip products)")
    parser.add_argument("--slug", action="append", default=[], help="Specific category slug(s) for product ingest")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and build JSONL files without writing to Postgres")
    args = parser.parse_args(argv)

    if args.all:
        stores = discover_enabled_stores(pathlib.Path(args.config_dir))
        if not stores:
            raise SystemExit(f"no enabled store configs found in {args.config_dir}")
    elif args.store_uuid:
        stores = [args.store_uuid]
    else:
        parser.error("provide either a <store_uuid> or --all")

    for sid in stores:
        print(f"=== Ingesting store {sid} ===", flush=True)
        ingest_one_store(
            sid,
            include_blogs=args.include_blogs,
            skip_products=args.skip_products,
            slugs=args.slug,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
