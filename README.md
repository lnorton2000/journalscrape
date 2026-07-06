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

## One-time setup: Resend API key

The script emails through [Resend](https://resend.com)'s HTTPS API rather
than Gmail SMTP, because this environment's network sandbox only allows
outbound HTTPS — raw SMTP connections are blocked.

1. Sign up at https://resend.com using the email address you want the digest
   sent to.
2. In the dashboard, go to **API Keys** → **Create API Key**, and copy the
   key (starts with `re_`).
3. No domain verification needed: Resend's shared `onboarding@resend.dev`
   sender can deliver to the address you signed up with.

Then set these as environment variables on this Claude Code environment
(see https://code.claude.com/docs/en/claude-code-on-the-web for how to add
environment variables to an environment):

- `RESEND_API_KEY` — the API key from step 2
- `TO_EMAIL` — the address you signed up to Resend with

Do not commit these values to git or paste the API key into chat.

## Running manually

```
python3 poll_journals.py
```

If the env vars aren't set, it prints the digest to the console instead of
emailing, so you can test it safely.

## Automatic daily run

A daily trigger runs this script and emails you if anything new was
published. No cron/systemd setup needed on your end — it's handled by the
Claude Code environment's scheduler.
