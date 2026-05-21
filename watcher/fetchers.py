"""Platform-specific HTTP fetchers.

Two platforms covered:
- dealeron: GET with base64-encoded SQL-like filter, paginates via `pn` + `pageNumber`
- dealercom: POST a (large) JSON body, paginates via preferences.pageStart

Both return a list of Listing dicts with a uniform shape:

    {
        "vin": str,
        "year": int | None,
        "make": str,
        "model": str,
        "trim": str,
        "mileage": int | None,
        "price": int | None,
        "ext_color": str | None,
        "int_color": str | None,
        "body_style": str | None,
        "cpo_tier": str | None,
        "vdp_url": str,
        "dealer_key": str,
        "dealer_name": str,
    }
"""
from __future__ import annotations

import base64
import logging
import re
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

# Polite UA for DealerOn (no bot protection, accepts identifying UAs).
DEALERON_UA = "Mozilla/5.0 (compatible; cpo-watcher/1.0)"
# Dealer.com sits behind Akamai Bot Manager. Empirically, a "compatible" UA gets 403'd
# while requests' default `python-requests/X.Y.Z` UA passes. We pass None below so
# requests uses its default — the least-bot-flagged option in testing.
DEALERCOM_UA: str | None = None

# CPO filter for DealerOn: (type = 'u') and (cpo = true)
DEALERON_CPO_FILTER_B64 = base64.b64encode(b"(type = 'u') and (cpo = true)").decode()

DEALERCOM_PAGE_SIZE = 100  # server caps at 100
DEALERON_PAGE_SIZE = 96    # server caps at 96


def _digits(s: str | None) -> int | None:
    if s is None:
        return None
    m = re.search(r"-?\d+", str(s).replace(",", ""))
    return int(m.group(0)) if m else None


# Plausible price band for a CPO Toyota. Outside this range we treat as unknown.
# TCUV program caps mileage at 85K so very-low prices are implausible — bumped to
# $8K to also catch junk values from DealerOn's TaggingPrice (e.g. doc fees,
# incentive amounts) that snuck past a lower floor.
PRICE_MIN = 8_000
PRICE_MAX = 300_000


def _sanitize_price(p: int | None) -> int | None:
    if p is None or p < PRICE_MIN or p > PRICE_MAX:
        return None
    return p


def fetch_dealeron(dealer: dict, session: requests.Session) -> list[dict]:
    base = f"https://{dealer['host']}/api/vhcliaa/vehicle-pages/cosmos/srp/vehicles/{dealer['client_id']}/{dealer['page_id']}"
    headers = {
        "User-Agent": DEALERON_UA,
        "Accept": "application/json, */*",
        "Referer": f"https://{dealer['host']}{dealer.get('srp_path', '/certified-pre-owned.html')}",
    }
    listings: list[dict] = []
    page_number = 1
    while True:
        params = {
            "host": dealer["host"],
            "baseFilter": DEALERON_CPO_FILTER_B64,
            "displayCardsShown": "NaN",
            "pn": str(DEALERON_PAGE_SIZE),
            "pageNumber": str(page_number),
        }
        r = session.get(base, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        body = r.json()
        cards = body.get("DisplayCards") or []
        for card in cards:
            vc = card.get("VehicleCard")
            if not vc:
                continue
            listings.append(_normalize_dealeron(vc, dealer))
        paging = (body.get("Paging") or {}).get("PaginationDataModel") or {}
        total_pages = paging.get("TotalPages") or 1
        if page_number >= total_pages:
            break
        page_number += 1
        time.sleep(2)
    return listings


def _normalize_dealeron(vc: dict, dealer: dict) -> dict:
    # Price priority: VehicleInternetPrice (int) is the source of truth. If 0 (dealer
    # hides asking price), fall back to TaggingPrice — but only if it's plausibly a
    # vehicle price ($5K+). TaggingPrice sometimes holds non-price values (doc fee,
    # incentive, etc) when the listing is "Call for price".
    price_int = _digits(vc.get("VehicleInternetPrice")) or 0
    if not price_int:
        price_int = _digits(vc.get("TaggingPrice"))
    return {
        "vin": vc.get("VehicleVin"),
        "year": vc.get("VehicleYear"),
        "make": vc.get("VehicleMake"),
        "model": vc.get("VehicleModel"),
        "trim": vc.get("VehicleTrim") or "",
        "mileage": _digits(vc.get("Mileage")),
        "price": _sanitize_price(price_int),
        "ext_color": vc.get("ExteriorColorLabel"),
        "int_color": vc.get("InteriorColorLabel"),
        "body_style": vc.get("VehicleBodyStyle"),
        "cpo_tier": vc.get("CpoTierTitle"),
        "vdp_url": vc.get("VehicleDetailUrl"),
        "dealer_key": dealer["key"],
        "dealer_name": dealer["name"],
    }


# The Dealer.com getInventory endpoint is picky about the body — stripping it down too
# far returns a 500. This body was captured from a live page and works against any
# Toyota-on-Dealer.com SRP. siteId + pageId + pageAlias come from dealer config.
def _dealercom_body(dealer: dict, page_start: int) -> dict:
    return {
        "siteId": dealer["site_id"],
        "locale": "en_US",
        "device": "DESKTOP",
        "pageAlias": "INVENTORY_LISTING_DEFAULT_AUTO_CERTIFIED_USED",
        "pageId": dealer["page_id"],
        "windowId": "cpo-watcher",
        "widgetName": "ws-inv-data",
        # Filter to Certified Pre-Owned only. Without this, some dealers' page configs
        # return mixed New/Used/CPO inventory and we'd email about new cars too.
        "inventoryParameters": {"compositeType": ["certified"]},
        "preferences": {
            "pageSize": str(DEALERCOM_PAGE_SIZE),
            "pageStart": str(page_start),
            "listing.config.id": "auto-used-certified",
            "listing.boost.order": "account,make,model,bodyStyle,trim,optionCodes,modelCode,fuelType",
            "removeEmptyFacets": "true",
            "removeEmptyConstraints": "true",
            "required.display.sets": "TITLE,IMAGE_ALT,IMAGE_TITLE,PRICE,FEATURED_ITEMS,CALLOUT,LISTING,HIGHLIGHTED_ATTRIBUTES,SUPPLEMENTAL_TITLE",
            "required.display.attributes": (
                "vin,year,make,model,trim,bodyStyle,exteriorColor,interiorColor,"
                "odometer,internetPrice,askingPrice,retailValue,salePrice,msrp,"
                "stockNumber,modelCode,engine,transmission,driveLine,fuelType,"
                "comments,certified,cpoTier,accountName,link"
            ),
            "showFranchiseVehiclesOnly": "true",
            "sorts": "year,normalBodyStyle,normalExteriorColor,odometer,internetPrice",
            "sortsTitles": "YEAR,BODYSTYLE,COLOR,MILEAGE,PRICE",
            "inventoryDateFormat": "MM_DD_YYYY_FORMAT",
        },
        "includePricing": True,
        "flags": {
            "vcda-js-environment": "live",
            "ws-itemlist-service-version": "v5",
            "ws-itemlist-model-version": "v1",
            "ws-inv-data-fetch-timeout": 30000,
            "ws-inv-data-fetch-retries": 2,
            "ws-inv-data-use-wis": True,
            "ws-inv-data-toggle-refactor": True,
        },
    }


def fetch_dealercom(dealer: dict, session: requests.Session) -> list[dict]:
    """Dealer.com's getInventory caps pageSize at 100 server-side and silently ignores
    pageStart/start in every location tested (body, query string, Referer, etc.).
    There's no working pagination via this endpoint — the browser frontend appears to
    maintain pagination state via a stateful widget bus we don't replicate. We fetch a
    single page of 100 (sorted year DESC by default) and warn if totalCount exceeds
    that. For "find new listings" use cases this is fine: the newest listings always
    land in the first page.
    """
    url = f"https://{dealer['host']}/api/widget/ws-inv-data/getInventory"
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Referer": f"https://{dealer['host']}{dealer.get('srp_path', '/certified-inventory/index.htm')}",
    }
    if DEALERCOM_UA is not None:
        headers["User-Agent"] = DEALERCOM_UA
    body = _dealercom_body(dealer, 0)
    r = session.post(url, json=body, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    items = data.get("inventory") or []
    listings = [_normalize_dealercom(it, dealer) for it in items]
    page_info = data.get("pageInfo") or {}
    total = int(page_info.get("totalCount") or len(listings))
    if total > len(listings):
        log.warning(
            "dealer=%s inventory exceeds single-page limit: totalCount=%d returned=%d (oldest %d not fetched)",
            dealer["key"], total, len(listings), total - len(listings),
        )
    return listings


def _normalize_dealercom(it: dict, dealer: dict) -> dict:
    # `attributes` is a list of {name, value, label}. Pull mileage from there if the
    # top-level field is missing.
    attrs = {a.get("name"): a.get("value") for a in (it.get("attributes") or [])}
    pricing = it.get("trackingPricing") or {}
    # `internetPrice` is the listing's actual asking price across all 3 Dealer.com sites
    # tested. `askingPrice` is *unreliable*: on Camelback it returns just the dealer
    # installed accessories ($499) rather than the vehicle price.
    price = _digits(pricing.get("internetPrice") or pricing.get("salePrice") or pricing.get("askingPrice"))
    vdp_link = it.get("link") or ""
    if vdp_link and vdp_link.startswith("/"):
        vdp_link = f"https://{dealer['host']}{vdp_link}"
    return {
        "vin": it.get("vin") or attrs.get("vin"),
        "year": it.get("year"),
        "make": it.get("make"),
        "model": it.get("model"),
        "trim": it.get("trim") or "",
        "mileage": _digits(attrs.get("odometer") or it.get("odometer")),
        "price": _sanitize_price(price),
        "ext_color": it.get("exteriorColor") or attrs.get("exteriorColor"),
        "int_color": it.get("interiorColor") or attrs.get("interiorColor"),
        "body_style": it.get("bodyStyle"),
        "cpo_tier": it.get("cpoTier"),
        "vdp_url": vdp_link,
        "dealer_key": dealer["key"],
        "dealer_name": dealer["name"],
    }


FETCHERS = {
    "dealeron": fetch_dealeron,
    "dealercom": fetch_dealercom,
}


def fetch_with_retry(dealer: dict, session: requests.Session, max_attempts: int = 3) -> list[dict]:
    """Fetch one dealer with retry/backoff. Returns [] on permanent failure (logged)."""
    fetcher = FETCHERS[dealer["platform"]]
    for attempt in range(1, max_attempts + 1):
        try:
            listings = fetcher(dealer, session)
            # VINs are required; drop entries without one
            listings = [l for l in listings if l.get("vin")]
            log.info("dealer=%s ok count=%d", dealer["key"], len(listings))
            return listings
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            log.warning("dealer=%s http_error status=%s attempt=%d/%d", dealer["key"], status, attempt, max_attempts)
        except Exception as e:
            log.warning("dealer=%s error=%r attempt=%d/%d", dealer["key"], e, attempt, max_attempts)
        if attempt < max_attempts:
            time.sleep(5 * attempt)
    log.error("dealer=%s failed after %d attempts; returning empty list", dealer["key"], max_attempts)
    return []
