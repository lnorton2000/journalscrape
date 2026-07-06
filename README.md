# journalscrape

Polls RSS feeds for the journals in `journals_config.json` and emails a daily
digest of new articles (title + link). Feeds without RSS are flagged for
future scraping but aren't polled yet.

## Files

- `journals_config.json` — journal name, feed URL, and status (confirmed /
  needs scraping / no feed found) for each source.
- `poll_journals.py` — fetches each feed, compares against `seen_state.json`
  to find new articles, and emails a digest if there are any.
- `seen_state.json` — tracks which article links have already been seen, so
  you don't get emailed the same article twice. Committed to git so state
  survives between runs.

## One-time setup: Gmail App Password

The script sends email through Gmail's SMTP server. This needs an **App
Password**, not your normal Gmail password:

1. Go to your Google Account → Security → turn on **2-Step Verification**
   (required before app passwords are available).
2. Go to https://myaccount.google.com/apppasswords, sign in, and create a new
   app password (name it anything, e.g. "journalscrape").
3. Google shows you a 16-character password — copy it.

Then set these as environment variables on this Claude Code environment
(see https://code.claude.com/docs/en/claude-code-on-the-web for how to add
environment variables to an environment):

- `GMAIL_ADDRESS` — your Gmail address (used to send, and as the default recipient)
- `GMAIL_APP_PASSWORD` — the 16-character app password from step 3
- `TO_EMAIL` — (optional) where the digest should go, if different from `GMAIL_ADDRESS`

Do not commit these values to git or paste the app password into chat.

## Running manually

```
python3 poll_journals.py
```

If the Gmail env vars aren't set, it prints the digest to the console instead
of emailing, so you can test it safely.

## Automatic daily run

A daily trigger runs this script and emails you if anything new was
published. No cron/systemd setup needed on your end — it's handled by the
Claude Code environment's scheduler.
