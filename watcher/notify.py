"""Email notification via SMTP (Gmail App Password).

Renders an HTML email with one card per listing, plus a plain-text fallback for
clients that don't render HTML. Fields shown per card:

    Title:     "{year} Toyota {model} {trim}"
    Price:     big bold value, with strikethrough+delta line for price drops
    VIN:       full 17-char string
    Mileage:   formatted with thousands separator
    Exterior Color
    Interior Color
    Dealer:    human-readable dealer name
    [View Vehicle]: button linking to VDP

Subject is fixed via the SUBJECT constant — change it here if the filter scope
in `filters.py` changes from Camry to something else.
"""
from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape

log = logging.getLogger(__name__)

# ─────────────────────────── Brand / theme ───────────────────────────
SUBJECT = "Certified Pre-Owned Camry Alert!"

# Arizona uses Mountain Standard Time year-round (no DST in the Phoenix metro
# where all 5 dealers live), so a fixed UTC-7 offset is always correct. Using a
# fixed offset avoids needing the `tzdata` package on Windows for `zoneinfo`.
ARIZONA = timezone(timedelta(hours=-7), name="MST")

# Inline-CSS palette. Email clients (especially Outlook) don't support CSS
# variables or stylesheets, so these get duplicated into every style="" below.
PAGE_BG = "#f4f6f8"
CARD_BG = "#ffffff"
HEADER_BG = "#1c2733"
HEADER_TEXT = "#ffffff"
HEADER_SUB = "#a8b3bf"
TEXT = "#1a1a1a"
MUTED = "#6c7680"
BORDER = "#e5e8eb"
ACCENT = "#d62027"        # Toyota-ish red — used for price drops + CTA button
BUTTON_BG = "#d62027"
BUTTON_TEXT = "#ffffff"

# Order/labels for the per-card stat rows. Keep in sync with the text-fallback.
STAT_FIELDS: list[tuple[str, str]] = [
    ("VIN", "vin"),
    ("Mileage", "mileage"),
    ("Exterior Color", "ext_color"),
    ("Interior Color", "int_color"),
    ("Dealer", "dealer_name"),
]


# ───────────────────────────── Formatters ─────────────────────────────
def _fmt_price(p: int | None) -> str:
    return f"${p:,}" if p else "—"


def _fmt_miles(m: int | None) -> str:
    return f"{m:,} mi" if m is not None else "—"


def _fmt_title(listing: dict) -> str:
    parts = [
        str(listing.get("year") or "").strip(),
        (listing.get("make") or "Toyota").strip(),
        (listing.get("model") or "").strip(),
        (listing.get("trim") or "").strip(),
    ]
    return " ".join(p for p in parts if p)


def _safe_url(u: str | None) -> str:
    """Only allow https:// URLs through; everything else collapses to `#`."""
    return u if (u and u.startswith("https://")) else "#"


def _fmt_stat(field_key: str, listing: dict) -> str:
    v = listing.get(field_key)
    if field_key == "mileage":
        return _fmt_miles(v)
    if v is None or v == "":
        return "—"
    return str(v)


# ───────────────────────────── HTML render ─────────────────────────────
def _stat_row_html(label: str, value: str) -> str:
    return (
        '<tr>'
        f'<td style="padding:6px 18px 6px 0;color:#6c7680;font-size:13px;'
        f'white-space:nowrap;vertical-align:top;font-weight:500;">{escape(label)}</td>'
        f'<td style="padding:6px 0;color:#1a1a1a;font-size:14px;vertical-align:top;'
        f'word-break:break-all;">{escape(value)}</td>'
        '</tr>'
    )


def _price_block_html(listing: dict, was_price: int | None) -> str:
    price = listing.get("price")
    main = (
        f'<div style="font-size:28px;font-weight:700;color:#1a1a1a;'
        f'line-height:1.1;letter-spacing:-0.5px;">{escape(_fmt_price(price))}</div>'
    )
    if was_price is not None and price is not None and price < was_price:
        delta = was_price - price
        main += (
            f'<div style="margin-top:4px;font-size:13px;color:#d62027;font-weight:600;">'
            f'was <span style="text-decoration:line-through;color:#6c7680;font-weight:400;">'
            f'{escape(_fmt_price(was_price))}</span>'
            f' · −{escape(_fmt_price(delta))}'
            '</div>'
        )
    return main


def _card_html(listing: dict, was_price: int | None = None) -> str:
    title = _fmt_title(listing)
    vdp = _safe_url(listing.get("vdp_url"))
    stats = "".join(_stat_row_html(label, _fmt_stat(key, listing)) for label, key in STAT_FIELDS)
    button = (
        f'<a href="{escape(vdp, quote=True)}" '
        'style="display:inline-block;padding:12px 24px;background:#d62027;'
        'color:#ffffff;text-decoration:none;font-weight:600;font-size:14px;'
        'border-radius:6px;letter-spacing:0.3px;">View Vehicle &nbsp;→</a>'
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#ffffff;border:1px solid #e5e8eb;border-radius:10px;'
        'margin-bottom:16px;box-shadow:0 1px 2px rgba(0,0,0,0.04);">'
        '<tr><td style="padding:24px 28px;">'
        f'<h3 style="margin:0 0 12px;font-size:18px;color:#1a1a1a;font-weight:700;'
        f'line-height:1.3;letter-spacing:-0.2px;">{escape(title)}</h3>'
        f'<div style="margin-bottom:20px;">{_price_block_html(listing, was_price)}</div>'
        f'<table role="presentation" cellpadding="0" cellspacing="0">{stats}</table>'
        f'<div style="margin-top:22px;">{button}</div>'
        '</td></tr></table>'
    )


def _section_html(heading: str, count: int, cards_html: str) -> str:
    return (
        f'<h2 style="font-size:13px;color:#1a1a1a;margin:28px 0 14px;'
        f'font-weight:700;text-transform:uppercase;letter-spacing:1.5px;">'
        f'{escape(heading)} <span style="color:#6c7680;font-weight:500;">'
        f'· {count}</span></h2>'
        f'{cards_html}'
    )


def _build_html(new_listings: list[dict], price_drops: list[tuple[dict, int]]) -> str:
    # 12-hour clock with no leading zero on the hour ("7:29 AM" not "07:29 AM").
    # strftime("%-I"/"%#I") differs across Unix/Windows, so we format manually.
    now_az = datetime.now(ARIZONA)
    hour_12 = now_az.hour % 12 or 12
    am_pm = "AM" if now_az.hour < 12 else "PM"
    ts = now_az.strftime(f"%b %d, %Y · {hour_12}:%M {am_pm} AZ")

    parts = []
    if new_listings:
        cards = "".join(
            _card_html(l)
            for l in sorted(new_listings, key=lambda x: x.get("price") or 0)
        )
        parts.append(_section_html("New Listings", len(new_listings), cards))
    if price_drops:
        cards = "".join(
            _card_html(l, was_price=old)
            for l, old in sorted(price_drops, key=lambda x: x[0].get("price") or 0)
        )
        parts.append(_section_html("Price Drops", len(price_drops), cards))

    body = "".join(parts)

    summary_bits = []
    if new_listings:
        summary_bits.append(f"{len(new_listings)} new")
    if price_drops:
        summary_bits.append(f"{len(price_drops)} price drop{'s' if len(price_drops) != 1 else ''}")
    summary = " · ".join(summary_bits) or "No changes"

    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(SUBJECT)}</title></head>'
        '<body style="margin:0;padding:0;background:#f4f6f8;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Helvetica,Arial,sans-serif;'
        '-webkit-font-smoothing:antialiased;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f4f6f8;"><tr><td>'
        '<table role="presentation" align="center" cellpadding="0" cellspacing="0" '
        'style="max-width:640px;width:100%;margin:0 auto;"><tr><td style="padding:24px 16px 0;">'
        # Header band
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#1c2733;border-radius:10px 10px 0 0;"><tr>'
        '<td style="padding:32px 28px;">'
        f'<h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:700;'
        f'letter-spacing:-0.3px;line-height:1.2;">{escape(SUBJECT)}</h1>'
        f'<div style="margin-top:8px;color:#a8b3bf;font-size:13px;'
        f'letter-spacing:0.2px;">{escape(summary)} · {escape(ts)}</div>'
        '</td></tr></table>'
        # Cards container
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f4f6f8;border-radius:0 0 10px 10px;"><tr>'
        f'<td style="padding:4px 20px 24px;">{body}</td>'
        '</tr></table>'
        # Footer
        '<div style="text-align:center;color:#6c7680;font-size:11px;'
        'padding:16px 28px 32px;line-height:1.6;">'
        'This alert was generated by cpo-watcher · listings derived from publicly '
        'available dealer inventory and are subject to availability'
        '</div>'
        '</td></tr></table></td></tr></table>'
        '</body></html>'
    )


# ───────────────────────────── Text render ─────────────────────────────
def _card_text(listing: dict, was_price: int | None = None) -> list[str]:
    lines = [_fmt_title(listing), _fmt_price(listing.get("price"))]
    if was_price and (p := listing.get("price")) and p < was_price:
        lines.append(f"  (was {_fmt_price(was_price)} · −{_fmt_price(was_price - p)})")
    for label, key in STAT_FIELDS:
        lines.append(f"  {label}: {_fmt_stat(key, listing)}")
    lines.append(f"  {_safe_url(listing.get('vdp_url'))}")
    return lines


def _build_text(new_listings: list[dict], price_drops: list[tuple[dict, int]]) -> str:
    out = [SUBJECT, "=" * len(SUBJECT), ""]
    if new_listings:
        out += [f"NEW LISTINGS ({len(new_listings)})", ""]
        for l in sorted(new_listings, key=lambda x: x.get("price") or 0):
            out += _card_text(l) + [""]
    if price_drops:
        out += [f"PRICE DROPS ({len(price_drops)})", ""]
        for l, old in sorted(price_drops, key=lambda x: x[0].get("price") or 0):
            out += _card_text(l, was_price=old) + [""]
    return "\n".join(out)


# ───────────────────────────── SMTP send ─────────────────────────────
def send(
    new_listings: list[dict],
    price_drops: list[tuple[dict, int]],
    *,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    smtp_user: str | None = None,
    smtp_pass: str | None = None,
    recipient: str | None = None,
) -> bool:
    """Send the alert email. Returns True on success or no-op, False on failure."""
    if not new_listings and not price_drops:
        log.info("nothing to notify")
        return True

    smtp_host = smtp_host or os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(smtp_port or os.environ.get("SMTP_PORT", "587"))
    smtp_user = smtp_user or os.environ.get("SMTP_USER")
    smtp_pass = smtp_pass or os.environ.get("SMTP_PASS")
    recipient = recipient or os.environ.get("ALERT_TO")

    if not (smtp_user and smtp_pass and recipient):
        log.error("missing SMTP credentials or recipient; skipping email")
        return False

    msg = EmailMessage()
    msg["From"] = f"cpo-watcher <{smtp_user}>"
    msg["To"] = recipient
    msg["Subject"] = SUBJECT
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="cpo-watcher.local")
    msg.set_content(_build_text(new_listings, price_drops))
    msg.add_alternative(_build_html(new_listings, price_drops), subtype="html")

    log.info("sending email host=%s port=%d subject=%r", smtp_host, smtp_port, SUBJECT)
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        log.error("SMTP send failed: %s: %s", type(e).__name__, e)
        return False

    log.info("email sent")
    return True
