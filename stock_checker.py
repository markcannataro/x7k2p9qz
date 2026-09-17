"""Email on a confirmed online availability transition for specific product pages."""

from __future__ import annotations

import html
import json
import os
import re
import smtplib
import sys
from datetime import datetime, timedelta, timezone
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
        return response.read(10_000_001).decode("utf-8", errors="replace")


def fetch_rendered(product: dict) -> str:
    """Read purchase status after the retailer's JavaScript has finished loading."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(locale="en-CA")
            page.goto(product["url"], wait_until="domcontentloaded", timeout=30000)
            last_page = ""
            for _ in range(20):
                page.wait_for_timeout(1000)
                last_page = page.content()
                if classify(last_page, product)[0] != "unknown":
                    break
            return last_page
        finally:
            browser.close()



class PurchasePanel(HTMLParser):
    """Collect only the configured purchase panel, excluding hidden controls."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, panel_id):
        super().__init__(convert_charrefs=True)
        self.panel_id = panel_id
        self.stack = []
        self.parts = []
        self.controls = []
        self.found = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        parent = self.stack[-1] if self.stack else ("", False, False, None)
        inside = parent[1] or attrs.get("id") == self.panel_id
        style = re.sub(r"\s+", "", attrs.get("style", "").lower())
        hidden = parent[2] or tag in {"script", "style", "noscript", "svg"} or "hidden" in attrs or attrs.get("aria-hidden") == "true" or "display:none" in style or "visibility:hidden" in style or bool({"aok-hidden", "a-hidden"} & set(attrs.get("class", "").split()))
        self.found |= attrs.get("id") == self.panel_id
        control = parent[3]
        if inside and not hidden and tag in {"button", "input"}:
            enabled = "disabled" not in attrs and attrs.get("aria-disabled") != "true" and attrs.get("type") != "hidden"
            if enabled:
                control = len(self.controls)
                self.controls.append([attrs.get("value", ""), attrs.get("aria-label", "")])
        if tag not in self.VOID:
            self.stack.append((tag, inside, hidden, control))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack:
            _, inside, hidden, control = self.stack[-1]
            if inside and not hidden:
                self.parts.append(data)
                if control is not None:
                    self.controls[control].append(data)


def classify_purchase_panel(page, product):
    parser = PurchasePanel(product["panel_id"])
    parser.feed(page)
    if not parser.found:
        return "unknown", "Product purchase panel was not found"
    panel = re.sub(r"\s+", " ", " ".join(parser.parts)).strip().lower()
    if any(phrase.lower() in panel for phrase in product["unavailable"]):
        return "unavailable", "Purchase panel says unavailable"
    labels = {re.sub(r"\s+", " ", label).strip().lower() for control in parser.controls for label in [*control, " ".join(control)]}
    if not labels.intersection(phrase.lower() for phrase in product["available"]):
        return "unknown", "No enabled purchase button was confirmed"
    if not re.search(product["seller_pattern"], panel, re.I):
        return "unknown", "Official seller was not confirmed"
    prices = {float(value.replace(",", "")) for value in re.findall(r"\$\s*([\d,]+\.\d{2})", panel)}
    prices = {value for value in prices if value >= 100}
    if len(prices) != 1:
        return "unknown", "Offer price was not unambiguously confirmed"
    price = prices.pop()
    if price > product["max_price"]:
        return "unavailable", "Offer exceeds configured price limit"
    return "available", f"Enabled purchase button; official seller; CAD {price:.2f}"


def classify(page: str, product: dict) -> tuple[str, str]:
    """Only explicit online status counts; unknown never means available."""
    text = visible_text(page)
    if len(page) > 10_000_000 or len(text) < 300:
        return "unknown", f"Missing or oversized product page ({len(page)} bytes, {len(text)} text chars)"
    for marker in product["identity"]:
        if marker.lower() not in text and marker.lower() not in page.lower():
            return "unknown", "Product identity marker missing"

    if product.get("panel_id"):
        return classify_purchase_panel(page, product)

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


def send_check_warning(products: list[dict], recipient: str, sender: str, password: str) -> None:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "Restock monitor: some stores cannot be checked"
    lines = ["Three consecutive checks could not read these product pages:", ""]
    for product in products:
        lines.extend((f"{product['store']}: {product['url']}", ""))
    lines.append("No stock conclusion was drawn for these stores. Their pages may block automated checks. Other stores continue to be checked.")
    message.set_content("\n".join(lines))
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(sender, password)
        smtp.send_message(message)


def main() -> int:
    try:
        products = json.loads(os.environ["TARGETS_JSON"])
        products.extend(json.loads(os.environ.get("EXTRA_TARGETS_JSON") or "[]"))
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
    checked_at = datetime.now(timezone.utc)
    now = checked_at.isoformat(timespec="seconds")

    changed = False
    newly_unreadable = []
    for product in products:
        key = product["key"]
        entry = state.get(key, {})
        retry_after = entry.get("retry_after")
        if retry_after and datetime.fromisoformat(retry_after) > checked_at:
            print(f"{now} {key}: paused after unreadable page; retry after {retry_after}")
            continue

        try:
            page = fetch_rendered(product) if product.get("render_js") else fetch(product["url"])
            status, reason = classify(page, product)
        except Exception as error:
            status, reason = "unknown", f"Fetch failed: {type(error).__name__}"
        entry = state.get(key, {})
        previous = entry.get("status")
        print(f"{now} {key}: {status} ({reason}); previous={previous or 'none'}")
        if status == "unknown":
            if entry.get("health") == "unreadable":
                entry["retry_after"] = (checked_at + timedelta(hours=1)).isoformat(timespec="seconds")
                state[key] = entry
                changed = True
                continue

            if entry.get("health") != "unreadable":
                entry["unknown_count"] = entry.get("unknown_count", 0) + 1
                if entry["unknown_count"] >= 3:
                    entry["health"] = "unreadable"
                    newly_unreadable.append(product)
                state[key] = entry
                changed = True
            continue  # Preserve the last reliable observation.
        if status == "available" and previous != "available":
            send_email(product, reason, recipient, sender, password)
            print(f"Alert sent for {key}")
        if previous != status or entry.get("health") == "unreadable" or entry.get("unknown_count"):
            state[key] = {"status": status, "checked_at": now}
            changed = True
    if newly_unreadable:
        send_check_warning(newly_unreadable, recipient, sender, password)
        print(f"Health warning sent for {len(newly_unreadable)} unreadable store(s)")
    if changed:
        STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
