"""Email on a confirmed online availability transition for specific product pages."""

from __future__ import annotations

import html
import json
import os
import re
import smtplib
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "stock_state.json"


class PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def visible_text(page: str) -> str:
    parser = PageText()
    parser.feed(page)
    return re.sub(r"\s+", " ", html.unescape(" ".join(parser.parts))).strip().lower()


def fetch(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; personal-restock-check/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-CA,en;q=0.9",
        },
    )
    with urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type:
            raise ValueError(f"Unexpected content type: {content_type}")
        return response.read(3_000_001).decode("utf-8", errors="replace")


def fetch_rendered(url: str) -> str:
    """Read purchase status after the retailer's JavaScript has finished loading."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(locale="en-CA")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.get_by_role(
                "button", name=re.compile(r"^(Sold out|Add to cart|Pre-?order)$", re.I)
            ).first.wait_for(timeout=20000)
            return page.content()
        finally:
            browser.close()


def classify(page: str, product: dict) -> tuple[str, str]:
    """Only explicit online status counts; unknown never means available."""
    text = visible_text(page)
    if len(page) > 3_000_000 or len(text) < 300:
        return "unknown", "Missing or oversized product page"
    for marker in product["identity"]:
        if marker.lower() not in text and marker.lower() not in page.lower():
            return "unknown", f"Product identity marker missing: {marker}"

    # Exact phrases observed on the three Canadian product pages. A generic
    # 'Add to Cart' elsewhere on the page is deliberately insufficient.
    start = product["status_start"].lower()
    end = product["status_end"].lower()
    start_at = text.find(start)
    end_at = text.find(end, start_at + len(start)) if start_at >= 0 else -1
    if start_at < 0 or end_at < 0:
        return "unknown", "Product purchase panel was not found"
    panel = text[start_at:end_at]
    unavailable = product["unavailable"]
    available = product["available"]
    negative = next((phrase for phrase in unavailable if phrase.lower() in panel), None)
    positive = next((phrase for phrase in available if phrase.lower() in panel), None)
    if negative:
        return "unavailable", f"Page says: {negative}"
    if positive:
        official_seller = product.get("official_seller")
        if official_seller and official_seller.lower() not in panel:
            return "unknown", "Official seller was not confirmed"
        return "available", f"Page says: {positive}"
    return "unknown", "No recognized online availability wording"


def send_email(product: dict, reason: str, recipient: str, sender: str, password: str) -> None:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = f"Possible online restock: {product['store']}"
    message.set_content(
        f"{product['store']} may now offer online purchase of the console.\n\n"
        f"Signal: {reason}\n\nOpen the official page and confirm before buying:\n"
        f"{product['url']}\n\nAvailability can disappear quickly. This alert does not reserve an item."
    )
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(sender, password)
        smtp.send_message(message)


def main() -> int:
    try:
        products = json.loads(os.environ["TARGETS_JSON"])
    except (KeyError, json.JSONDecodeError) as error:
        print(f"Missing or invalid TARGETS_JSON: {error}", file=sys.stderr)
        return 2
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    recipient = os.environ.get("EMAIL_TO", "")
    sender = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    if not all((recipient, sender, password)):
        print("Missing EMAIL_TO, SMTP_USER, or SMTP_PASS", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changed = False
    for product in products:
        key = product["key"]
        try:
            page = fetch_rendered(product["url"]) if product.get("render_js") else fetch(product["url"])
            status, reason = classify(page, product)
        except Exception as error:
            status, reason = "unknown", f"Fetch failed: {type(error).__name__}"
        previous = state.get(key, {}).get("status")
        print(f"{now} {key}: {status} ({reason}); previous={previous or 'none'}")
        if status == "unknown":
            continue  # Preserve the last reliable observation.
        if status == "available" and previous != "available":
            send_email(product, reason, recipient, sender, password)
            print(f"Alert sent for {key}")
        if previous != status:
            state[key] = {"status": status, "checked_at": now}
            changed = True
    if changed:
        STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
