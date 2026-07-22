#!/usr/bin/env python3
"""
Generic "watch a list, alert on new entries" checker.

Fetches a JSON list from a configured API endpoint, compares it against
what was seen last run, and emails on anything new. All identifying
details (target URL, item ID, auth key) are supplied via environment
variables / secrets rather than hardcoded here.

State is stored in state.json so re-runs only alert on genuinely new entries.
"""

import json
import os
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

import requests

STATE_FILE = Path("state.json")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"entries": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def fetch_entries(api_url: str, item_id: str, auth_key: str) -> list:
    """
    Hits the configured endpoint. Returns a list of entries (typically
    date strings), or an empty list if the response shape is unexpected.
    """
    headers = {
        "Ocp-Apim-Subscription-Key": auth_key,
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    }
    params = {"filmId": item_id}
    resp = requests.get(api_url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, list):
        print("WARNING: unexpected response shape, dumping raw response:")
        print(json.dumps(data, indent=2)[:2000])
        return []

    return sorted(set(data))


def send_email(new_entries: list) -> None:
    smtp_server = os.environ["SMTP_SERVER"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    email_to = os.environ["EMAIL_TO"]

    lines = ["New entries just appeared:\n"]
    for e in new_entries:
        lines.append(f"  - {e}")
    body = "\n".join(lines)

    msg = MIMEText(body)
    msg["Subject"] = "New availability detected"
    msg["From"] = smtp_user
    msg["To"] = email_to

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [email_to], msg.as_string())

    print("Email sent.")


def main() -> int:
    api_url = os.environ.get("API_ENDPOINT")
    item_id = os.environ.get("ITEM_ID")
    auth_key = os.environ.get("AUTH_KEY")

    missing = [
        name
        for name, val in [
            ("API_ENDPOINT", api_url),
            ("ITEM_ID", item_id),
            ("AUTH_KEY", auth_key),
        ]
        if not val
    ]
    if missing:
        print(f"ERROR: missing required environment variable(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    current_entries = fetch_entries(api_url, item_id, auth_key)

    if not current_entries:
        print("WARNING: no entries returned. The API may have changed or the key may be stale/rotated.")

    state = load_state()
    old_entries = set(state.get("entries", []))
    new_entries = sorted(set(current_entries) - old_entries)

    if new_entries:
        print("New entries found:", new_entries)
        if os.environ.get("SMTP_SERVER"):
            send_email(new_entries)
        else:
            print("SMTP not configured, skipping email (dry run).")
    else:
        print("No new entries since last check.")

    state["entries"] = current_entries
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
