# Availability checker

Checks a configured API endpoint every 5 minutes for new entries in a
list, and emails you the moment something new appears. Runs on GitHub's
servers (free, unlimited on public repos), so your computer doesn't need
to be on.

Deliberately generic naming/code throughout — the specific target isn't
referenced anywhere in this repo, so it won't surface in casual searches.
The three secrets below (`API_ENDPOINT`, `ITEM_ID`, `AUTH_KEY`) hold all
the specifics, and only you have those values.

## Setup (about 10 minutes, one time)

### 1. Create the repo
- Create a new repo on GitHub (name doesn't matter).
- Upload all the files in this folder to it.

### 2. Add secrets
**Settings → Secrets and variables → Actions → New repository secret.**
Add these eight:

| Secret name | Value |
|---|---|
| `API_ENDPOINT` | The API URL you're targeting |
| `ITEM_ID` | The specific item ID being tracked |
| `AUTH_KEY` | The API key/subscription key required by the endpoint |
| `SMTP_SERVER` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | The Gmail address you're sending from |
| `SMTP_PASS` | A Gmail app password (see below) |
| `EMAIL_TO` | Where you want the alert sent |

**Gmail app password:** turn on 2-Step Verification on the sending
account, then go to **myaccount.google.com/apppasswords**, generate one
for "Mail," and copy the 16-character code (no spaces) as `SMTP_PASS`.

### 3. Test it
Go to the **Actions** tab → the workflow → **Run workflow**. Check the
logs:
- First run will likely email you everything currently in the list —
  expected, since `state.json` starts empty. After that, only genuinely
  new entries trigger an email.
- A warning about an unexpected response shape means the API changed —
  you'll need to recapture `AUTH_KEY` (and possibly `API_ENDPOINT`) from
  the site's network requests via browser dev tools, the same way you
  found them originally.

After that, it runs itself every 5 minutes.

## Adjusting the check frequency
Edit the `cron` line in `.github/workflows/check.yml`. GitHub won't run a
schedule more often than every 5 minutes, and disables scheduled
workflows on repos with no activity for 60+ days (a manual "Run workflow"
click resets that clock). Public repos get unlimited free Actions
minutes; private repos get 2,000 free minutes/month, so a 5-minute
schedule on a private repo would incur a small cost.
