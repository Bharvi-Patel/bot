"""Storefront API client: the one place the bot talks to the store API.

Every call sends the Store-UUID header (and the bot key, if one is configured). The bot never
touches a store database; the API decides which store's data it sees from the UUID.

Built against real responses saved in tests/fixtures/api/. Endpoints with no saved response yet
(product search, brands) are deliberately not implemented here.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

CDN_PLACEHOLDER = "cdn_folder"


class ApiError(Exception):
    """The API answered with an error, or could not be reached."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class ApiConfig:
    base_url: str
    store_uuid: str
    api_key: str | None = None
    api_key_header: str | None = None  # header name is undecided; the key is sent only if both are set
    cdn_folder: str | None = None  # replaces the literal "cdn_folder" in image base paths
    timeout: float = 10.0
    retries: int = 2

    @classmethod
    def from_env(cls) -> "ApiConfig":
        return cls(
            base_url=os.environ["BOT_API_BASE_URL"],
            store_uuid=os.environ["BOT_STORE_UUID"],
            api_key=os.getenv("BOT_API_KEY") or None,
            api_key_header=os.getenv("BOT_API_KEY_HEADER") or None,
            cdn_folder=os.getenv("BOT_CDN_FOLDER") or None,
            timeout=float(os.getenv("BOT_API_TIMEOUT", "10")),
            retries=int(os.getenv("BOT_API_RETRIES", "2")),
        )


# ---------- small helpers ----------

def to_number(value: Any) -> float | None:
    """The listing sends 439.99, the detail sends "439.99". Both become a float."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_price(value: Any) -> float | None:
    """A price the bot may quote. The API returns -0.01 for some products (7 of 16 in a real "bed"
    search), so zero or negative means "no price available", never a number to say aloud."""
    n = to_number(value)
    return n if n is not None and n > 0 else None


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _first_language(rows: Any) -> dict[str, Any]:
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return {}


# ---------- client ----------

class ApiClient:
    def __init__(self, config: ApiConfig, opener: Callable[..., Any] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.config = config
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep

    # -- transport --

    def _headers(self) -> dict[str, str]:
        headers = {"Store-UUID": self.config.store_uuid, "Accept": "application/json"}
        if self.config.api_key and self.config.api_key_header:
            headers[self.config.api_key_header] = self.config.api_key
        return headers

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None,
                 body: dict[str, Any] | None = None) -> Any:
        url = self.config.base_url.rstrip("/") + "/" + path.lstrip("/")
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = self._headers()
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        last: ApiError | None = None
        for attempt in range(self.config.retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                with self._open(req, timeout=self.config.timeout) as resp:
                    raw = resp.read()
                return self._parse(raw, status=getattr(resp, "status", 200))
            except urllib.error.HTTPError as exc:
                err = self._error_from_body(exc.read(), exc.code)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                err = ApiError(f"could not reach the store API: {exc}", retryable=True)
            except ApiError as exc:
                err = exc
            last = err
            if not err.retryable or attempt == self.config.retries:
                raise err
            self._sleep(0.5 * (2 ** attempt))
        raise last  # pragma: no cover

    @staticmethod
    def _error_from_body(raw: bytes, status: int) -> ApiError:
        message = f"HTTP {status}"
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict) and payload.get("message"):
                message = str(payload["message"])
        except (ValueError, TypeError):
            pass
        return ApiError(message, status=status, retryable=status >= 500 or status == 429)

    @staticmethod
    def _parse(raw: bytes, status: int) -> Any:
        try:
            payload = json.loads(raw)
        except ValueError:
            raise ApiError("the store API returned something that is not JSON", status=status)
        # Some endpoints wrap in {success, message, ...}; others (category tree) return a bare list.
        # A wrapped answer can say success:false even on HTTP 200.
        if isinstance(payload, dict) and payload.get("success") is False:
            code = payload.get("response_code") or payload.get("code") or status
            raise ApiError(str(payload.get("message") or "request failed"), status=int(code))
        return payload

    # -- images --

    def image_url(self, base: str | None, file_name: str | None) -> str | None:
        """Join an API image base path and a file name. The API's base paths contain a literal
        "cdn_folder"; it is replaced with the store's real folder. If no folder is configured and the
        placeholder is still there, return None rather than a broken link."""
        if not base or not file_name:
            return None
        if self.config.cdn_folder:
            base = base.replace(f"/{CDN_PLACEHOLDER}/", f"/{self.config.cdn_folder}/")
        if f"/{CDN_PLACEHOLDER}/" in base and not self.config.cdn_folder:
            return None
        return base.rstrip("/") + "/" + file_name.lstrip("/")

    # -- endpoints --

    def store_info(self) -> dict[str, Any]:
        """GET /stores. Picks only the fields the bot may state. The raw response also carries
        settings and keys, so it is never passed on whole."""
        payload = self._request("GET", "/stores")
        store = payload.get("stores") if isinstance(payload, dict) else None
        if not isinstance(store, dict):
            raise ApiError("unexpected /stores response shape")
        city = store.get("store_city") if isinstance(store.get("store_city"), dict) else {}
        state = store.get("store_state") if isinstance(store.get("store_state"), dict) else {}
        hours = store.get("opening_hours") if isinstance(store.get("opening_hours"), dict) else {}
        return {
            "name": store.get("name"),
            "phone": store.get("phone"),
            "email": store.get("email"),
            "address": store.get("address"),
            "address2": store.get("address2"),
            "city": city.get("cityname"),
            "state": state.get("name"),
            "zipcode": store.get("zipcode"),
            "timezone": store.get("timezone"),
            "opening_hours": {
                day: {"open": h.get("open"), "close": h.get("close"), "is_open": bool(h.get("is_open"))}
                for day, h in hours.items() if isinstance(h, dict)
            },
        }

    def store_pages(self) -> list[dict[str, Any]]:
        """The page list from GET /stores: slug, title and the raw status flag. The page text is not in this
        response. What status 0 means (hidden or unpublished) is not confirmed, so nothing is filtered here."""
        payload = self._request("GET", "/stores")
        pages = payload.get("pages") if isinstance(payload, dict) else None
        out = []
        for p in pages or []:
            if not isinstance(p, dict):
                continue
            out.append({
                "slug": p.get("slug"),
                "title": _first_language(p.get("page_languages")).get("title"),
                "status": p.get("status"),
            })
        return out

    def brands(self) -> list[dict[str, Any]]:
        """GET /brands: vendors[] -> brands[] -> brand_languages[]. Flattened to one row per brand.
        The response has no product counts, so the bot cannot say how many products a brand has from this call."""
        payload = self._request("GET", "/brands")
        data = payload.get("data") if isinstance(payload, dict) else None
        vendors = data.get("vendors") if isinstance(data, dict) else None
        if not isinstance(vendors, list):
            raise ApiError("unexpected /brands response shape")
        seen, out = set(), []
        for v in vendors:
            if not isinstance(v, dict):
                continue
            for b in v.get("brands") or []:
                if not isinstance(b, dict) or b.get("slug") in seen:
                    continue
                seen.add(b.get("slug"))
                out.append({
                    "slug": b.get("slug"),
                    "name": _first_language(b.get("brand_languages")).get("name"),
                    "vendor": v.get("name"),
                    "status": b.get("status"),
                })
        return out

    def search_products(self, query: str, page: int = 1) -> dict[str, Any]:
        """POST /products/search?page=N with {"q": query}. Only the "q" filter is confirmed so far;
        other filters (brand, category, price, sort) are not implemented until their request format is known."""
        params = {"page": page} if page and page > 1 else None
        payload = self._request("POST", "/products/search", params=params, body={"q": query})
        data = payload.get("data") if isinstance(payload, dict) else None
        paged = data.get("product") if isinstance(data, dict) else None
        if not isinstance(paged, dict):
            raise ApiError("unexpected products/search response shape")
        base = data.get("imagePath")
        facets = data.get("filter") if isinstance(data.get("filter"), dict) else {}
        return {
            "page": paged.get("current_page"),
            "last_page": paged.get("last_page"),
            "per_page": paged.get("per_page"),
            "total": paged.get("total"),
            "products": [self._listing_item(p, base) for p in paged.get("data") or [] if isinstance(p, dict)],
            "filters": {
                "categories": [{"uuid": c.get("uuid"), "title": c.get("title"), "count": c.get("product_count")}
                               for c in facets.get("categories") or [] if isinstance(c, dict)],
                "brands": [b.get("name") for b in facets.get("brands") or [] if isinstance(b, dict)],
                "price_range": facets.get("price_range"),
                "attributes": [{"group": g.get("group_name"), "values": [a.get("name") for a in g.get("attributes") or []]}
                               for g in facets.get("attributes") or [] if isinstance(g, dict)],
            },
        }

    def categories(self) -> list[dict[str, Any]]:
        """GET /categories/tree (a bare list). Returns a trimmed nested tree."""
        payload = self._request("GET", "/categories/tree")
        if not isinstance(payload, list):
            raise ApiError("unexpected /categories/tree response shape")
        return [self._category_node(n) for n in payload if isinstance(n, dict)]

    def _category_node(self, node: dict[str, Any]) -> dict[str, Any]:
        return {
            "category_id": node.get("category_id"),
            "slug": node.get("slug"),
            "name": node.get("name"),
            "children": [self._category_node(c) for c in node.get("children") or [] if isinstance(c, dict)],
        }

    def category_products(self, slug: str, page: int = 1) -> dict[str, Any]:
        """POST /categories/{slug}/products?page=N. 16 per page."""
        params = {"page": page} if page and page > 1 else None
        payload = self._request("POST", f"/categories/{urllib.parse.quote(slug, safe="")}/products", params=params, body={})
        data = payload.get("data") if isinstance(payload, dict) else None
        paged = data.get("product") if isinstance(data, dict) else None
        if not isinstance(paged, dict):
            raise ApiError("unexpected category products response shape")
        base = data.get("imagePath")
        return {
            "page": paged.get("current_page"),
            "last_page": paged.get("last_page"),
            "per_page": paged.get("per_page"),
            "total": paged.get("total"),
            "breadcrumbs": [{"name": b.get("name"), "slug": b.get("slug")} for b in data.get("breadcrumbs") or []],
            "products": [self._listing_item(p, base) for p in paged.get("data") or [] if isinstance(p, dict)],
        }

    def _listing_item(self, p: dict[str, Any], image_base: str | None) -> dict[str, Any]:
        lang = _first_language(p.get("product_languages"))
        brand = p.get("brand") if isinstance(p.get("brand"), dict) else {}
        return {
            "slug": p.get("slug"),
            "sku": p.get("sku"),
            "name": lang.get("name"),
            "brand": _first_language(brand.get("brand_languages")).get("name"),
            "price": to_price(p.get("sale_price")),  # base_price is deliberately not passed on
            "image_url": self.image_url(image_base, p.get("main_image")),
            "tags": lang.get("tags") or "",
        }

    def product_detail(self, slug: str) -> dict[str, Any]:
        """GET /product/{slug}. Descriptions only exist here, not in listings."""
        payload = self._request("GET", f"/product/{urllib.parse.quote(slug, safe="")}")
        data = payload.get("data") if isinstance(payload, dict) else None
        p = data.get("product") if isinstance(data, dict) else None
        if not isinstance(p, dict):
            raise ApiError("unexpected product response shape")
        lang = _first_language(p.get("product_languages"))
        brand = p.get("brand") if isinstance(p.get("brand"), dict) else {}

        seen, categories = set(), []  # the API repeats the same category several times
        for pc in data.get("product_categories") or []:
            cat = pc.get("category") if isinstance(pc, dict) else None
            if not isinstance(cat, dict) or cat.get("slug") in seen:
                continue
            seen.add(cat.get("slug"))
            categories.append({"slug": cat.get("slug"), "name": _first_language(cat.get("category_languages")).get("title")})

        base = data.get("imagePath")
        images = [self.image_url(base, i.get("image_url")) for i in p.get("product_images") or [] if isinstance(i, dict)]
        return {
            "slug": p.get("slug"),
            "sku": p.get("sku"),
            "name": lang.get("name"),
            "brand": _first_language(brand.get("brand_languages")).get("name"),
            "price": to_price(p.get("sale_price")),  # base_price is deliberately not passed on
            # The three text fields are raw HTML from the catalog; the tool cleans them (bot.text.clean_text).
            "short_description": lang.get("short_description") or None,
            "description": lang.get("full_description") or None,
            "special_features": lang.get("special_features") or None,
            "main_image": self.image_url(base, p.get("main_image")),
            "images": [u for u in images if u],
            "dimensions": {k: to_number(p.get(k)) for k in ("height", "width", "length", "depth", "weight")},
            "pieces": p.get("pieces"),
            "lifestyle": p.get("lifestyle"),
            "is_variant": p.get("parent_id") is not None,
            "categories": categories,
            # attributes, colors, sizes and variations are passed through raw: no saved response has any yet,
            # so their shape is unknown. Nothing should show them to the model until one is seen (they may
            # carry base_price or other internal fields).
            "attributes": p.get("attributes") or [],
            "colors": data.get("colors") or [],
            "sizes": data.get("sizes") or [],
            "variations": p.get("variations") or [],
        }


_DEFAULT: ApiClient | None = None


def get_client() -> ApiClient:
    """One client per process, built from BOT_API_BASE_URL and BOT_STORE_UUID (see ApiConfig.from_env)."""
    global _DEFAULT
    if _DEFAULT is None:
        try:
            _DEFAULT = ApiClient(ApiConfig.from_env())
        except KeyError as exc:
            raise ApiError(f"missing setting {exc.args[0]}; set it in the environment") from exc
    return _DEFAULT
