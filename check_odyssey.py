#!/usr/bin/env python3
"""
Checks Cineplex's "bookable dates" API for The Odyssey (IMAX 70mm, filmId
37617) and emails you when a NEW date appears that wasn't there last run.

This endpoint returns bookable dates nationally (not split by theatre), so
a new date here means "opened up somewhere in Canada" -- given there are
only a handful of 70mm IMAX theatres in the country, that's still a strong
signal. Confirm the specific date applies to Mississauga/Vaughan on
cineplex.com before buying.

State is stored in state.json so re-runs only alert on genuinely new dates.
"""

import json
import os
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

import requests

STATE_FILE = Path("state.json")

API_URL = "https://apis.cineplex.com/prod/cpx/theatrical/api/v1/dates/bookable"
FILM_ID = "37617"  # The Odyssey


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"dates": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def fetch_bookable_dates(subscription_key: str) -> list:
    """
    Hits Cineplex's public bookable-dates endpoint. Returns a list of
    ISO date strings, e.g. ["2026-07-21T00:00:00", "2026-07-22T00:00:00", ...]
    """
    headers = {
        "Ocp-Apim-Subscription-Key": subscription_key,
        "Accept": "application/json",
        "Referer": "https://www.cineplex.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    }
    params = {"filmId": FILM_ID}
    resp = requests.get(API_URL, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, list):
        print("WARNING: unexpected response shape, dumping raw response:")
        print(json.dumps(data, indent=2)[:2000])
        return []

    return sorted(set(data))


def send_email(new_dates: list) -> None:
    smtp_server = os.environ["SMTP_SERVER"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    email_to = os.environ["EMAIL_TO"]

    lines = [
        "New IMAX 70mm bookable date(s) just opened up for The Odyssey:\n",
    ]
    for d in new_dates:
        lines.append(f"  - {d}")
    lines.append("")
    lines.append(
        "This list is nationwide, not theatre-specific -- double check "
        "Mississauga and Vaughan directly on cineplex.com before buying."
    )
    body = "\n".join(lines)

    msg = MIMEText(body)
    msg["Subject"] = "New Odyssey IMAX 70mm date(s) available!"
    msg["From"] = smtp_user
    msg["To"] = email_to

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [email_to], msg.as_string())

    print("Email sent.")


def main() -> int:
    subscription_key = os.environ.get("CHECK_URL")  # reusing this secret name for the key
    if not subscription_key:
        print("ERROR: CHECK_URL (subscription key) environment variable not set.", file=sys.stderr)
        return 1

    current_dates = fetch_bookable_dates(subscription_key)

    if not current_dates:
        print("WARNING: no dates returned. The API may have changed or the key may be stale/rotated.")

    state = load_state()
    old_dates = set(state.get("dates", []))
    new_dates = sorted(set(current_dates) - old_dates)

    if new_dates:
        print("New dates found:", new_dates)
        if os.environ.get("SMTP_SERVER"):
            send_email(new_dates)
        else:
            print("SMTP not configured, skipping email (dry run).")
    else:
        print("No new dates since last check.")

    state["dates"] = current_dates
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
