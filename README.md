# Odyssey IMAX 70mm availability checker

Checks Cineplex every 20 minutes for new available dates at Mississauga and
Vaughan, and emails you the moment something new opens up. Runs on GitHub's
servers (free), so your computer doesn't need to be on.

## Setup (about 10 minutes, one time)

### 1. Create the repo
- Create a **new private repo** on GitHub (e.g. `odyssey-checker`).
- Upload all the files in this folder to it (or `git push` if you're
  comfortable with git).

### 2. Capture the exact API request Cineplex uses
Cineplex bakes the movie's internal ID into the URL, and I can't browse
their site directly to grab it for you, so this one step is manual:

1. Open **cineplex.com** in Chrome, go to *The Odyssey: The IMAX Experience
   in 70mm Film*, and get to the page where you pick a date/theatre for
   Mississauga or Vaughan.
2. Open DevTools (`F12` or `Ctrl+Shift+I`) → **Network** tab → filter by
   **Fetch/XHR**.
3. Refresh the page or click through the date picker. Look for a request
   with a name like `dates`, `availabletheatres`, or `showtimes` — it'll
   return JSON when you click the **Preview** or **Response** tab.
4. Right-click that request → **Copy** → **Copy URL**. That's your
   `CHECK_URL`.
5. Open that URL directly in a new browser tab to sanity-check it returns
   JSON with theatre names and dates in it (not an error page).

If the JSON shape doesn't match what `check_odyssey.py` expects, the script
will print a warning with the raw response — send that to me and I'll
adjust the parser in `extract_theatre_dates()`.

### 3. Get a Gmail app password (so the script can send email)
- Turn on 2-Step Verification on the Gmail account you want to send *from*
  (can be the same as the account you receive at, or a throwaway).
- Go to **myaccount.google.com/apppasswords**, generate a password for
  "Mail", and copy the 16-character code.

### 4. Add secrets to the repo
In your repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add these five:

| Secret name | Value |
|---|---|
| `CHECK_URL` | The URL you captured in step 2 |
| `SMTP_SERVER` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | The Gmail address you're sending from |
| `SMTP_PASS` | The 16-character app password from step 3 |
| `EMAIL_TO` | Where you want the alert sent (can be the same address) |

### 5. Test it
Go to the **Actions** tab → **Check Odyssey IMAX 70mm availability** →
**Run workflow**. Check the logs:
- First run will likely email you a big list — that's expected, since
  `state.json` starts empty and everything currently open counts as "new."
  After that, you'll only get emails for genuinely new dates.
- If theatre names don't match, tweak `TARGET_THEATRES` in
  `check_odyssey.py` (it does a case-insensitive substring match).

After that, it just runs itself every 20 minutes.

## Adjusting the check frequency
Edit the `cron` line in `.github/workflows/check.yml`. Note GitHub disables
scheduled workflows on repos with no activity for 60+ days, and won't run
more often than every 5 minutes — 20 minutes is a reasonable balance of
speed vs. not hammering Cineplex's servers.

## If Cineplex changes their API
Cineplex's front-end occasionally changes how this request is shaped. If
the checker stops finding your theatres, redo step 2 to grab a fresh
`CHECK_URL` and update the secret.
