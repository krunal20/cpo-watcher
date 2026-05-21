"""Email notification via SMTP (Gmail App Password)."""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape

log = logging.getLogger(__name__)


def _fmt_price(p: int | None) -> str:
    if not p:
        return "—"
    return f"${p:,}"


def _fmt_miles(m: int | None) -> str:
    if m is None:
        return "—"
    return f"{m:,} mi"


def _safe_url(u: str | None) -> str:
    """Only allow https:// URLs through; everything else becomes `#`."""
    if u and u.startswith("https://"):
        return u
    return "#"


def _row_html(listing: dict, extra: str = "") -> str:
    title = f'{listing.get("year") or ""} {listing.get("make") or ""} {listing.get("model") or ""} {listing.get("trim") or ""}'.strip()
    return (
        '<tr>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{escape(listing.get("dealer_name") or "")}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{escape(title)}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{escape(_fmt_miles(listing.get("mileage")))}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{escape(_fmt_price(listing.get("price")))}{extra}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eee"><a href="{escape(_safe_url(listing.get("vdp_url")), quote=True)}">View</a></td>'
        '</tr>'
    )


def _build_html(new_listings: list[dict], price_drops: list[tuple[dict, int]]) -> str:
    sections = []
    if new_listings:
        rows = "".join(_row_html(l) for l in sorted(new_listings, key=lambda x: (x.get("dealer_name") or "", x.get("price") or 0)))
        sections.append(
            f'<h2 style="font:600 18px/1.3 system-ui;margin:24px 0 8px">New listings ({len(new_listings)})</h2>'
            f'<table style="border-collapse:collapse;width:100%;font:14px/1.4 system-ui">{rows}</table>'
        )
    if price_drops:
        rows = "".join(
            _row_html(l, extra=f' <span style="color:#0a7">(was {escape(_fmt_price(old))})</span>')
            for l, old in sorted(price_drops, key=lambda x: (x[0].get("dealer_name") or "", x[0].get("price") or 0))
        )
        sections.append(
            f'<h2 style="font:600 18px/1.3 system-ui;margin:24px 0 8px">Price drops ({len(price_drops)})</h2>'
            f'<table style="border-collapse:collapse;width:100%;font:14px/1.4 system-ui">{rows}</table>'
        )
    return f'<div style="max-width:760px;margin:auto;font:14px/1.4 system-ui">{"".join(sections)}</div>'


def _build_text(new_listings: list[dict], price_drops: list[tuple[dict, int]]) -> str:
    lines = []
    if new_listings:
        lines.append(f"NEW LISTINGS ({len(new_listings)})")
        for l in new_listings:
            title = f'{l.get("year") or ""} {l.get("make") or ""} {l.get("model") or ""} {l.get("trim") or ""}'.strip()
            lines.append(f'  [{l["dealer_name"]}] {title} — {_fmt_miles(l.get("mileage"))} — {_fmt_price(l.get("price"))}')
            lines.append(f'    {_safe_url(l.get("vdp_url"))}')
        lines.append("")
    if price_drops:
        lines.append(f"PRICE DROPS ({len(price_drops)})")
        for l, old in price_drops:
            title = f'{l.get("year") or ""} {l.get("make") or ""} {l.get("model") or ""} {l.get("trim") or ""}'.strip()
            lines.append(f'  [{l["dealer_name"]}] {title} — was {_fmt_price(old)} now {_fmt_price(l.get("price"))}')
            lines.append(f'    {_safe_url(l.get("vdp_url"))}')
        lines.append("")
    return "\n".join(lines)


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
    """Send the alert email. Returns True on success, False on any failure.

    Returns True when there is nothing to notify on (no-op).
    All exceptions are caught and logged so callers can decide on exit status
    without their own try/except wrapping.
    """
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

    subject_bits = []
    if new_listings:
        subject_bits.append(f"{len(new_listings)} new")
    if price_drops:
        subject_bits.append(f"{len(price_drops)} price drop{'s' if len(price_drops) != 1 else ''}")
    subject = f"[cpo-watcher] {', '.join(subject_bits)}"

    msg = EmailMessage()
    msg["From"] = f"cpo-watcher <{smtp_user}>"
    msg["To"] = recipient
    msg["Subject"] = subject
    # Date and Message-ID help Gmail-to-Gmail avoid the spam folder.
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="cpo-watcher.local")
    msg.set_content(_build_text(new_listings, price_drops))
    msg.add_alternative(_build_html(new_listings, price_drops), subtype="html")

    log.info("sending email host=%s port=%d subject=%r", smtp_host, smtp_port, subject)
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        # Don't log the exception repr unfiltered — it can echo headers in some
        # SMTP-server-side error messages. Stick to the type and short message.
        log.error("SMTP send failed: %s: %s", type(e).__name__, e)
        return False

    log.info("email sent")
    return True
