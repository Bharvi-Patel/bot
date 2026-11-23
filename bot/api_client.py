"""Storefront API client: the one place the bot talks to the store API.

Every call sends the Store-UUID header (and the bot key, if one is configured). The bot never
touches a store database; the API decides which store's data it sees from the UUID.

Built against real responses saved in tests/fixtures/api/. Listing filters sent so far (brand_ids, price_range,
per_page) use formats confirmed against the real API. attribute_ids is sent too, but ONE id at a time: several ids mean "any of", not "all of".
Sorting is deliberately not sent yet: price_low_high lists the -0.01 placeholder products first.
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

from bot.store_config import valid_store_id

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

    def __post_init__(self) -> None:
        # urlopen also opens file:// and other schemes, so a mistyped setting must fail loudly here.
        if not self.base_url.lower().startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")

    @classmethod
    def from_env(cls, store_uuid: str | None = None) -> "ApiConfig":
        """Shared settings come from the environment. The store comes from the caller; only when none is given
        does BOT_STORE_UUID apply (local development and single-store setups)."""
        return cls(
            base_url=os.environ["BOT_API_BASE_URL"],
            store_uuid=store_uuid or os.environ["BOT_STORE_UUID"],
            api_key=os.getenv("BOT_API_KEY") or None,
            api_key_header=os.getenv("BOT_API_KEY_HEADER") or None,
            cdn_folder=os.getenv("BOT_CDN_FOLDER") or None,
            timeout=float(os.getenv("BOT_API_TIMEOUT", "10")),
            retries=int(os.getenv("BOT_API_RETRIES", "2")),
        )


_FILTER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


_SORT_VALUES = frozenset({"az", "za", "price_low_high", "price_high_low"})


def _filter_body(brand_ids: list[str] | None, price_from: float | None, price_to: float | None,
                 per_page: int | None, attribute_ids: list[str] | None = None,
                 sort_by: str | None = None) -> dict[str, Any]:
    """The filter part of a listing request, in the formats the real API accepted:
    brand_ids as a list of brand uuids, price_range as {"from": n, "to": n} (the {"min","max"} form is ignored),
    per_page as a number, sort_by as one of az/za/price_low_high/price_high_low. Anything else raises ValueError."""
    body: dict[str, Any] = {}
    if brand_ids:
        if not isinstance(brand_ids, (list, tuple)) or not all(isinstance(b, str) and _FILTER_ID_RE.match(b) for b in brand_ids):
            raise ValueError("brand_ids must be a list of brand uuids")
        body["brand_ids"] = list(brand_ids)
    if attribute_ids:
        if not isinstance(attribute_ids, (list, tuple)) or not all(isinstance(a, str) and _FILTER_ID_RE.match(a) for a in attribute_ids):
            raise ValueError("attribute_ids must be a list of attribute uuids")
        body["attribute_ids"] = list(attribute_ids)   # the API matches products with ANY of these, so send one
    price: dict[str, float] = {}
    for key, value in (("from", price_from), ("to", price_to)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
            raise ValueError(f"price_{key} must be a number that is not negative")
        price[key] = value
    if price:
        body["price_range"] = price
    if per_page is not None:
        if isinstance(per_page, bool) or not isinstance(per_page, int) or not 1 <= per_page <= 40:
            raise ValueError("per_page must be a whole number from 1 to 40")  # 40 is the largest size tried on the real API
        body["per_page"] = per_page
    if sort_by is not None:
        if not isinstance(sort_by, str) or sort_by not in _SORT_VALUES:
            raise ValueError(f"sort_by must be one of: {', '.join(sorted(_SORT_VALUES))}")
        body["sort_by"] = sort_by
    return body


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
                 body: dict[str, Any] | None = None, extra_headers: dict[str, str] | None = None) -> Any:
        url = self.config.base_url.rstrip("/") + "/" + path.lstrip("/")
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {**self._headers(), **(extra_headers or {})}
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

    def shipping_options(self) -> list[dict[str, Any]]:
        """GET /shipping-options: public read-only list of active shipping zones and their enabled shipping methods."""
        payload = self._request("GET", "/shipping-options")
        zones = payload.get("zones") if isinstance(payload, dict) else None
        if not isinstance(zones, list):
            data = payload.get("data") if isinstance(payload, dict) else None
            zones = data.get("zones") if isinstance(data, dict) else None
        if not isinstance(zones, list):
            raise ApiError("unexpected /shipping-options response shape")
        out = []
        for z in zones:
            if not isinstance(z, dict):
                continue
            methods = []
            for m in z.get("methods") or []:
                if not isinstance(m, dict):
                    continue
                methods.append({
                    "name": m.get("name"),
                    "slug": m.get("slug"),
                    "label": m.get("label") or m.get("name"),
                    "cost_type": m.get("cost_type"),
                    "price": to_number(m.get("price")),
                    "percentage": to_number(m.get("percentage")),
                    "min_cost": to_number(m.get("min_cost")),
                    "max_cost": to_number(m.get("max_cost")),
                    "description": strip_html(m.get("description")) or None,
                    "is_pickup": bool(m.get("is_pickup")),
                    "call_for_shipping": bool(m.get("call_for_shipping")),
                })
            country = z.get("country") if isinstance(z.get("country"), dict) else {}
            states = [s.get("name") for s in (z.get("states") or []) if isinstance(s, dict) and s.get("name")]
            zipcodes = [str(zc) for zc in (z.get("zipcodes") or []) if zc]
            out.append({
                "name": z.get("name"),
                "country": country.get("name"),
                "states": states,
                "zipcodes": zipcodes,
                "is_country_wide": bool(z.get("is_country_wide")),
                "methods": methods,
            })
        return out

    def faqs(self) -> list[dict[str, Any]]:
        """GET /faqs/global: faqs[] -> faq_languages[] (question, answer as HTML). One row per FAQ in the store's
        first listed language. The product links inside each FAQ are dropped: the answers are store-wide text.
        The response has no status or deleted flag, so whether the endpoint already hides disabled FAQs is not
        confirmed; nothing is filtered here."""
        payload = self._request("GET", "/faqs/global")
        faqs = payload.get("faqs") if isinstance(payload, dict) else None
        if not isinstance(faqs, list):
            raise ApiError("unexpected /faqs/global response shape")
        out = []
        for f in faqs:
            if not isinstance(f, dict):
                continue
            lang = _first_language(f.get("faq_languages"))
            question, answer = lang.get("question"), lang.get("answer")
            if not f.get("slug") or not question or not answer:
                continue
            out.append({"slug": f["slug"], "question": strip_html(question), "answer_html": answer,
                        "sort_order": f.get("sort_order")})
        return out

    def page(self, slug: str) -> dict[str, Any]:
        """GET /page/{slug}: the page row itself, not wrapped. The text is page_languages[0].long_description (HTML) in the
        store's first listed language. A page-builder page (is_advanced) keeps its content elsewhere; no response of that
        kind has been seen, so it is returned as advanced=True with no html rather than guessed at."""
        quoted = urllib.parse.quote(slug, safe="")
        payload = self._request("GET", f"/page/{quoted}")
        page = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
        if not isinstance(page, dict) or not isinstance(page.get("page_languages"), list):
            raise ApiError("unexpected /page response shape")
        lang = _first_language(page["page_languages"])
        return {"slug": page.get("slug") or slug, "title": strip_html(lang.get("title")) or None,
                "html": lang.get("long_description") or "", "status": page.get("status"),
                "deleted": bool(page.get("deleted_at")), "updated_at": page.get("updated_at"),
                "advanced": bool(lang.get("is_advanced"))}

    def customer_orders(self, token: str, limit: int = 25) -> list[dict[str, Any]]:
        """GET /orders as the logged-in customer: the token is sent as the standard Bearer credential and the API returns only
        that customer's own orders (it filters on the login). The token is used for this one call and is never stored on the
        client. Only these fields are kept: order number, status name, date, total and item names. The raw rows also hold the
        customer's name, phone, email and payment details, which are dropped here. The list endpoint has no item quantities.
        Field names come from the API's controller code (Laravel writes relations in snake_case, so order_status and order_details);
        no real response has been saved yet, so check them against one."""
        if not isinstance(token, str) or not token:
            raise ApiError("a customer login is required", status=401)
        payload = self._request("GET", "/orders", params={"page": 1, "limit": int(limit)},
                                extra_headers={"Authorization": f"Bearer {token}"})
        rows = payload.get("orders") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ApiError("unexpected /orders response shape")
        out = []
        for o in rows:
            if not isinstance(o, dict) or not o.get("order_number"):
                continue
            status = next((o[k] for k in ("order_status", "orderStatus") if isinstance(o.get(k), dict)), {})
            details = o.get("order_details") or o.get("orderDetails") or []
            items = [{"name": strip_html(d.get("product_name")), "sku": d.get("product_sku")}
                     for d in details if isinstance(d, dict) and d.get("product_name")]
            out.append({"order_number": str(o["order_number"]), "order_status": status.get("name"),
                        "created_at": o.get("created_at"), "grand_total": to_number(o.get("grand_total")), "items": items})
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
                    "uuid": b.get("uuid"),   # the id brand_ids filters on (same id the search facets list)
                    "slug": b.get("slug"),
                    "name": _first_language(b.get("brand_languages")).get("name"),
                    "vendor": v.get("name"),
                    "status": b.get("status"),
                })
        return out

    def search_products(self, query: str, page: int = 1, *, brand_ids: list[str] | None = None,
                        price_from: float | None = None, price_to: float | None = None,
                        per_page: int | None = None, attribute_ids: list[str] | None = None) -> dict[str, Any]:
        """POST /products/search?page=N with {"q": query} plus the filters confirmed against the real API
        (tests/fixtures/api and the captures of 16-17 Nov): brand_ids, attribute_ids, price_range {"from","to"}
        and per_page.
        The API requires q to be at least 2 characters (HTTP 422 otherwise), so that is checked here.
        Sorting is NOT sent yet: price_low_high lists the -0.01 placeholder products first. Several attribute ids
        mean "any of", so callers send one."""
        query = (query or "").strip()
        if len(query) < 2:
            raise ApiError("search text must be at least 2 characters", status=422)
        params = {"page": page} if page and page > 1 else None
        body = {"q": query, **_filter_body(brand_ids, price_from, price_to, per_page, attribute_ids)}
        payload = self._request("POST", "/products/search", params=params, body=body)
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
                # the same groups with each value's uuid, which attribute_ids filters on
                "attribute_options": [{"group": g.get("group_name"),
                                       "values": [{"id": a.get("uuid"), "name": a.get("name")}
                                                  for a in g.get("attributes") or [] if isinstance(a, dict) and a.get("uuid")]}
                                      for g in facets.get("attributes") or [] if isinstance(g, dict)],
            },
        }

    def attribute_options(self, query: str) -> list[dict[str, Any]]:
        """Attribute groups and values (with uuids) found among the products that match `query`.
        There is no store-wide list in the API: /product-filter-data returned no attribute groups for the Beds
        categories (it only lists attributes marked as filters), so the search response's facets are used.
        They show only what exists among the matches, e.g. no Bed Size group at all for a brand whose beds carry none."""
        return self.search_products(query)["filters"]["attribute_options"]

    def blogs(self, page: int = 1, limit: int | None = None) -> dict[str, Any]:
        """GET /blogs/active: the store's live blog posts (title, slug, dates; the post text is read with blog()).
        Confirmed against the live API (19 Nov): paginates with ?page=N&limit=M (default limit 10)."""
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("page must be a whole number of 1 or more")
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100):
            raise ValueError("limit must be a whole number from 1 to 100")
        params: dict[str, Any] = {}
        if page > 1:
            params["page"] = page
        if limit is not None:
            params["limit"] = limit
        payload = self._request("GET", "/blogs/active", params=params or None)
        rows = payload.get("blogs") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ApiError("unexpected /blogs/active response shape")
        out = []
        for b in rows:
            if not isinstance(b, dict) or not b.get("slug"):
                continue
            lang = _first_language(b.get("blog_languages"))
            cat = b.get("blog_category") if isinstance(b.get("blog_category"), dict) else {}
            out.append({"uuid": b.get("uuid"), "slug": b.get("slug"), "status": b.get("status"), "deleted_at": b.get("deleted_at"),
                        "updated_at": b.get("updated_at"), "title": lang.get("title"), "category": cat.get("slug")})
        total = payload.get("totalRecord")
        return {"blogs": out, "total": total, "page": payload.get("currentPage", page), "limit": payload.get("limit", limit),
                "partial": isinstance(total, int) and total > len(rows)}

    def blog(self, slug: str) -> dict[str, Any]:
        """GET /blogs/{slug}: one post. `html` is the raw post body."""
        payload = self._request("GET", f"/blogs/{urllib.parse.quote(slug, safe='')}")
        b = payload.get("blog") if isinstance(payload, dict) else None
        lang = _first_language(b.get("blog_languages")) if isinstance(b, dict) else {}
        if not isinstance(b, dict) or not b.get("slug") or not lang:
            raise ApiError("unexpected /blogs/{slug} response shape")
        cat = b.get("blog_category") if isinstance(b.get("blog_category"), dict) else {}
        return {"uuid": b.get("uuid"), "slug": b.get("slug"), "status": b.get("status"), "language_id": lang.get("language_id"),
                "title": lang.get("title"), "html": lang.get("description") or "", "tags": b.get("tags"), "category": cat.get("slug")}

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

    def category_products(self, slug: str, page: int = 1, *, brand_ids: list[str] | None = None,
                          price_from: float | None = None, price_to: float | None = None,
                          per_page: int | None = None, attribute_ids: list[str] | None = None,
                          sort_by: str | None = None) -> dict[str, Any]:
        """POST /categories/{slug}/products?page=N, 16 per page unless per_page is given.

        PAGING WORKAROUND: the API caches an unfiltered category list for 24 hours under a key with no page number,
        so every page of it comes back as page 1 (and price_range {"from": 0} also hits that bug because PHP treats
        0 as empty — confirmed live on 19 Nov). Sending sort_by="az" bypasses the broken cache key, returns distinct
        pages across the full total (including -0.01 option-priced products), and matches alphabetical default order.
        Remove this once the API's cache key includes the page."""
        if sort_by is None and not (price_from or price_to):
            sort_by = "az"
        params = {"page": page} if page and page > 1 else None
        quoted = urllib.parse.quote(slug, safe="")
        payload = self._request("POST", f"/categories/{quoted}/products", params=params,
                                body=_filter_body(brand_ids, price_from, price_to, per_page, attribute_ids, sort_by))
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

    @staticmethod
    def _shown_option(p: dict[str, Any]) -> dict[str, Any] | None:
        variations = p.get("variations")
        if p.get("product_type") != 1 or not isinstance(variations, list) or not variations or not isinstance(variations[0], dict):
            return None
        v = variations[0]
        labels = [_first_language(a.get("attribute_languages")).get("title") or a.get("slug")
                  for a in v.get("attributes") or [] if isinstance(a, dict)]
        price = to_price(v.get("sale_price"))
        if price is None:
            return None
        return {"option": " / ".join(str(x) for x in labels if x) or None, "price": price}

    def _listing_item(self, p: dict[str, Any], image_base: str | None) -> dict[str, Any]:
        lang = _first_language(p.get("product_languages"))
        brand = p.get("brand") if isinstance(p.get("brand"), dict) else {}
        return {
            "slug": p.get("slug"),
            "sku": p.get("sku"),
            "name": lang.get("name"),
            "brand": _first_language(brand.get("brand_languages")).get("name"),
            "price": to_price(p.get("sale_price")),  # base_price is deliberately not passed on
            # product_type 1 is a product whose price depends on an option such as size: in the saved real data every
            # one has sale_price -0.01 on the product itself, with the real prices on its options (see product_detail).
            "price_varies": p.get("product_type") == 1,
            # An option-priced product comes with ONE variation (one size and its price). In the real captures it is
            # not chosen by the attribute filter (a Queen filter showed a Twin) and not known to be the cheapest, so it
            # is only "this size costs this", never a "from" price and never the price of the size that was asked for.
            "shown_option": self._shown_option(p),
            "image_url": self.image_url(image_base, p.get("main_image")),
            "tags": lang.get("tags") or "",
        }

    def product_detail(self, slug: str) -> dict[str, Any]:
        """GET /product/{slug}. Descriptions only exist here, not in listings."""
        quoted = urllib.parse.quote(slug, safe="")
        payload = self._request("GET", f"/product/{quoted}")
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

        # Options (sizes, colors...) of a product whose price depends on them. groupedAttributes lists each option
        # value as {"id": "<attribute uuid>:<variation uuid>", "title": ...} under its group, which says which
        # variation each value belongs to. Only sku, slug, sale price and those values are kept: the raw variations
        # also carry base_price and other internal fields.
        values: dict[str, dict[str, str]] = {}
        for g in data.get("groupedAttributes") or []:
            for a in (g.get("attributes") or []) if isinstance(g, dict) else []:
                if isinstance(a, dict) and ":" in str(a.get("id", "")) and a.get("title"):
                    values.setdefault(str(a["id"]).split(":")[-1], {})[str(g.get("group"))] = a["title"]
        options = [
            {"sku": v.get("sku"), "slug": v.get("slug"), "price": to_price(v.get("sale_price")), "attributes": values.get(str(v.get("uuid")), {})}
            for v in p.get("variations") or [] if isinstance(v, dict)
        ]
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
            "price_varies": p.get("product_type") == 1,
            "options": options,
            "categories": categories,
            # attributes, colors, sizes, variations and promotions are deliberately NOT returned: no saved
            # response has any yet, so their shape is unknown and they may carry base_price or other internal
            # fields. Add them back, whitelisted, once a real response with data has been seen.
        }


def get_client(ctx: Any = None) -> ApiClient:
    """The API client for the store this chat belongs to (ctx.store_uuid).

    A new client is built on every call and nothing is cached or shared between stores, so one store's data
    can never be served to another. If the context has no store, BOT_STORE_UUID is used (local development
    and single-store setups). On a multi-store server leave BOT_STORE_UUID unset, so a missing store fails
    instead of quietly answering for the wrong one."""
    store_uuid = getattr(ctx, "store_uuid", None) or os.getenv("BOT_STORE_UUID")
    if not store_uuid:
        raise ApiError("no store selected: ctx.store_uuid is empty and BOT_STORE_UUID is not set")
    if not valid_store_id(store_uuid):  # it goes into a request header, so no spaces or line breaks
        raise ApiError("the store id has characters that are not allowed")
    try:
        return ApiClient(ApiConfig.from_env(store_uuid))
    except KeyError as exc:
        raise ApiError(f"missing setting {exc.args[0]}; set it in the environment") from exc
