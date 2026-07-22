#!/usr/bin/env python3
"""
Checks Cineplex's showtime-availability API for The Odyssey (IMAX 70mm)
at specific theatres, and emails you when a NEW date appears that
wasn't there on the last run.

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

# Theatre names to watch for (case-insensitive substring match against
# whatever name field Cineplex returns for each theatre in the API response)
TARGET_THEATRES = [
    "Mississauga",
    "Vaughan",
]


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def fetch_availability(check_url: str) -> dict:
    """
    Hits the Cineplex 'availabletheatres/dates' endpoint you captured from
    your browser's dev tools. Returns the parsed JSON.
    """
    headers = {
        # Mimic a real browser; Cineplex's API can be picky about this.
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    resp = requests.get(check_url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def extract_theatre_dates(data: dict) -> dict:
    """
    Cineplex's response shape can shift slightly, so this walks the JSON
    defensively looking for theatre entries with a name + list of dates.
    Returns: { theatre_name: [date_str, ...] }
    """
    results = {}

    # The endpoint typically returns something like:
    # { "theatres": [ { "name": "...", "dates": ["2026-08-20", ...] }, ... ] }
    # but we handle a couple of shapes defensively.
    candidates = None
    if isinstance(data, dict):
        for key in ("theatres", "Theatres", "results", "data"):
            if key in data and isinstance(data[key], list):
                candidates = data[key]
                break
    elif isinstance(data, list):
        candidates = data

    if candidates is None:
        # Unknown shape — dump it so you can inspect and adjust the parser.
        print("WARNING: unrecognized JSON shape, dumping raw response:")
        print(json.dumps(data, indent=2)[:2000])
        return results

    for entry in candidates:
        name = (
            entry.get("name")
            or entry.get("Name")
            or entry.get("theatreName")
            or ""
        )
        dates = (
            entry.get("dates")
            or entry.get("Dates")
            or entry.get("availableDates")
            or []
        )
        # Normalize date entries whether they're plain strings or dicts
        clean_dates = []
        for d in dates:
            if isinstance(d, str):
                clean_dates.append(d)
            elif isinstance(d, dict):
                clean_dates.append(
                    d.get("date") or d.get("Date") or d.get("showDate") or str(d)
                )
        results[name] = sorted(set(clean_dates))

    return results


def matches_target(theatre_name: str) -> bool:
    lname = theatre_name.lower()
    return any(t.lower() in lname for t in TARGET_THEATRES)


def send_email(new_dates: dict) -> None:
    smtp_server = os.environ["SMTP_SERVER"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    email_to = os.environ["EMAIL_TO"]

    lines = ["New IMAX 70mm dates just opened up for The Odyssey:\n"]
    for theatre, dates in new_dates.items():
        lines.append(f"{theatre}:")
        for d in dates:
            lines.append(f"  - {d}")
        lines.append("")
    body = "\n".join(lines)

    msg = MIMEText(body)
    msg["Subject"] = "New Odyssey IMAX 70mm dates available!"
    msg["From"] = smtp_user
    msg["To"] = email_to

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [email_to], msg.as_string())

    print("Email sent.")


def main() -> int:
    check_url = os.environ.get("CHECK_URL")
    if not check_url:
        print("ERROR: CHECK_URL environment variable not set.", file=sys.stderr)
        return 1

    data = fetch_availability(check_url)
    current = extract_theatre_dates(data)
    current = {name: dates for name, dates in current.items() if matches_target(name)}

    if not current:
        print(
            "WARNING: no matching theatres found in response. "
            "The API shape may have changed, or TARGET_THEATRES needs adjusting."
        )

    state = load_state()
    new_dates = {}

    for theatre, dates in current.items():
        old_dates = set(state.get(theatre, []))
        added = sorted(set(dates) - old_dates)
        if added:
            new_dates[theatre] = added

    if new_dates:
        print("New dates found:", new_dates)
        if os.environ.get("SMTP_SERVER"):
            send_email(new_dates)
        else:
            print("SMTP not configured, skipping email (dry run).")
    else:
        print("No new dates since last check.")

    # Update state regardless, so next run compares against latest known dates
    state.update(current)
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
