#!/usr/bin/env python3
"""Poll journal RSS feeds for new articles and email a digest of any that are new.

Reads feed URLs from journals_config.json, compares against previously-seen
article links in seen_state.json, and emails any new articles (title + link)
via the Resend HTTPS API. Run this once per day (see README.md for scheduling
setup).

Required environment variables:
    RESEND_API_KEY  API key from resend.com (see README.md)
    TO_EMAIL        Recipient address (must match the email you signed up to Resend with,
                     until a sending domain is verified)
"""
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "journals_config.json"
STATE_PATH = BASE_DIR / "seen_state.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

def _local_tag(element):
    """Strip the '{namespace}' prefix ElementTree adds to tag names."""
    tag = element.tag
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(element, local_name):
    for child in element:
        if _local_tag(child) == local_name:
            return (child.text or "").strip()
    return ""


def fetch_feed_items(url):
    """Return a list of (title, link) tuples from an RSS 2.0, RSS 1.0/RDF, or Atom feed URL.

    Matches elements by local tag name (ignoring namespace) since RSS 1.0/RDF
    feeds (e.g. Taylor & Francis) declare a default namespace that ElementTree's
    unqualified './/item' searches silently fail to match.
    """
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        data = resp.read()
    root = ET.fromstring(data)

    items = []
    for element in root.iter():
        local = _local_tag(element)
        if local == "item":
            title = _child_text(element, "title")
            link = _child_text(element, "link")
            if title and link:
                items.append((title, link))
        elif local == "entry":  # Atom
            title = _child_text(element, "title")
            link = ""
            for child in element:
                if _local_tag(child) == "link":
                    link = child.get("href", "").strip()
                    break
            if title and link:
                items.append((title, link))
    return items


def fetch_html(url):
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def scrape_cia_studies_in_intelligence(index_url):
    """Return (title, link) tuples for every article across all listed issues.

    No RSS feed exists for this journal. The index page links to one page per
    issue; each issue page lists its articles as plain <a class="link-button
    bold" href="...">Title</a> tags, which is stable enough to scrape directly.
    """
    html = fetch_html(index_url)
    issue_paths = sorted(set(re.findall(
        r'href="(/resources/csi/studies-in-intelligence/studies-in-intelligence-vol-[^"]+/)"',
        html,
    )))

    items = []
    for path in issue_paths:
        issue_url = urljoin(index_url, path)
        issue_html = fetch_html(issue_url)
        for link, title in re.findall(
            r'<a class="link-button bold" href="([^"]+)">([^<]+)</a>', issue_html
        ):
            items.append((title.strip(), urljoin(issue_url, link)))
    return items


def scrape_csis_analysis(listing_url):
    """Return (title, link) tuples for the current pieces on CSIS's analysis listing.

    CSIS is a large site that publishes many content types -- commentary,
    briefs, reports, "critical questions", weekly columns -- but they all get a
    /analysis/<slug> URL and surface together on the /analysis listing page.
    The site's advertised RSS feed (csis.org/rss.xml) is dead (it returns a
    handful of 2016 events), so we scrape the listing instead.

    Each item appears as <a href="/analysis/<slug>">Title</a>; we capture the
    anchor text as the title, stripping any nested markup. Only the most recent
    ~9 items are shown and pagination (?page=N) is WAF-blocked (403), but the
    daily cadence means a handful of items per day is captured comfortably.
    """
    html = fetch_html(listing_url)
    pattern = re.compile(r'<a[^>]+href="(/analysis/[^"?#]+)"[^>]*>(.*?)</a>', re.S)

    items = []
    seen_links = set()
    for href, inner in pattern.findall(html):
        title = re.sub(r"<[^>]+>", " ", inner)  # strip nested tags (spans, etc.)
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) <= 15:  # skip icon/"read more" links with no real title
            continue
        link = urljoin(listing_url, href)
        if link in seen_links:
            continue
        seen_links.add(link)
        items.append((title, link))
    return items


SCRAPERS = {
    "cia_studies_in_intelligence": scrape_cia_studies_in_intelligence,
    "csis_analysis": scrape_csis_analysis,
}


def load_json(path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def send_digest_email(new_by_journal, warning=None):
    """Send the digest via Resend's HTTPS API.

    Plain SMTP is blocked by this environment's network sandbox (only HTTPS
    egress is allowed), so email goes through Resend's HTTP API instead.

    `warning`, if set, is prepended to the email body and flagged in the
    subject -- used to surface a state-persistence failure directly to the
    user, since that failure must never be silent (see commit_and_push_state).
    """
    api_key = os.environ.get("RESEND_API_KEY")
    to_email = os.environ.get("TO_EMAIL")

    if not api_key or not to_email:
        print("RESEND_API_KEY / TO_EMAIL not set -- skipping email, printing instead.")
        if warning:
            print(warning)
        print_digest(new_by_journal)
        return

    lines = []
    if warning:
        lines.append(warning)
        lines.append("")
    total = sum(len(v) for v in new_by_journal.values())
    for journal_name, articles in new_by_journal.items():
        lines.append(f"{journal_name}")
        for title, link in articles:
            lines.append(f"  - {title}\n    {link}")
        lines.append("")
    body = "\n".join(lines)

    subject = f"Journal digest: {total} new article{'s' if total != 1 else ''}"
    if warning:
        subject = "[ACTION NEEDED] " + subject

    payload = json.dumps({
        "from": "Journal Digest <onboarding@resend.dev>",
        "to": [to_email],
        "subject": subject,
        "text": body,
    }).encode()

    req = Request(
        "https://api.resend.com/emails",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:
            resp.read()
    except HTTPError as e:
        print(f"Resend API error {e.code}: {e.read().decode()}", file=sys.stderr)
        raise
    print(f"Emailed digest ({total} new articles) to {to_email}")


def commit_and_push_state():
    """Persist seen_state.json to git immediately after computing it.

    Returns (success, log) -- log is the full transcript of every command run,
    so a caller can surface it (e.g. embed in the email) rather than relying on
    stderr being noticed. This must never fail silently: a run that emails a
    digest but fails to push the updated state causes the *same* articles to
    be re-flagged as new -- and re-emailed -- the next time it runs from a
    fresh checkout, which is exactly the bug this is guarding against.

    Commit signing is explicitly disabled for this one commit (it's a
    bot-maintained tracking file, not authored code) since the environment's
    commit-signing helper may not be reliably available in a headless/
    automated session. Local git identity is also set explicitly in case a
    fresh clone has no user.name/user.email configured at all.
    """
    log_lines = []

    def run(*args):
        result = subprocess.run(
            ["git", *args], cwd=BASE_DIR, capture_output=True, text=True
        )
        log_lines.append(f"$ git {' '.join(args)}\n{result.stdout}{result.stderr}".rstrip())
        return result

    diff = run("status", "--porcelain", "--", "seen_state.json")
    if diff.returncode != 0 or not diff.stdout.strip():
        log_lines.append("No changes to seen_state.json, nothing to persist.")
        print("\n".join(log_lines))
        return True, "\n".join(log_lines)

    run("config", "user.email", "journalscrape-bot@localhost")
    run("config", "user.name", "journalscrape-bot")

    steps = (
        ("add", "seen_state.json"),
        ("-c", "commit.gpgsign=false", "commit", "-m", "Update seen article state"),
        ("push", "origin", "HEAD"),
    )
    for args in steps:
        result = run(*args)
        if result.returncode != 0:
            run("remote", "-v")
            log = "\n".join(log_lines)
            print(log, file=sys.stderr)
            return False, log

    log = "\n".join(log_lines)
    print(log)
    return True, log


def print_digest(new_by_journal):
    for journal_name, articles in new_by_journal.items():
        print(f"\n{journal_name}")
        for title, link in articles:
            print(f"  - {title}\n    {link}")


# If a single source reports more than this many *new* items in one run, treat
# it as a malfunction (a broken feed now serving a full archive, or a scraper
# whose page was redesigned so its pattern over-matches) rather than a real
# burst -- no source legitimately publishes this many pieces in a day. Such a
# source is dropped from the digest and flagged instead, so a mistake can never
# flood the inbox with hundreds of "new" items at once.
MAX_NEW_PER_SOURCE = 25


def main():
    config = load_json(CONFIG_PATH, {"journals": []})
    state = load_json(STATE_PATH, {})

    new_by_journal = {}
    errors = []
    floods = []

    for journal in config["journals"]:
        feed_url = journal.get("feed_url")
        scrape_type = journal.get("scrape_type")
        if not feed_url and not scrape_type:
            continue
        name = journal["name"]
        seen_links = set(state.get(name, []))

        try:
            if feed_url:
                items = fetch_feed_items(feed_url)
            else:
                items = SCRAPERS[scrape_type](journal["scrape_url"])
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue

        new_items = [(title, link) for title, link in items if link not in seen_links]
        if len(new_items) > MAX_NEW_PER_SOURCE:
            # Almost certainly a broken source, not a real burst -- suppress the
            # flood but still mark everything seen below, so it self-heals and
            # doesn't re-trigger every day.
            floods.append((name, len(new_items)))
        elif new_items:
            new_by_journal[name] = new_items

        state[name] = sorted({link for _, link in items} | seen_links)

    save_json(STATE_PATH, state)
    persisted, persist_log = commit_and_push_state()

    if errors:
        print("Errors fetching some feeds:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)

    warning_parts = []
    if floods:
        detail = "\n".join(
            f"  - {name}: {count} new items in one run (limit {MAX_NEW_PER_SOURCE})"
            for name, count in floods
        )
        warning_parts.append(
            "NOTE: these sources returned an abnormal number of new items and "
            "were left OUT of the digest below to avoid flooding your inbox -- "
            "this usually means the feed or scraper broke, not that this many "
            "articles were really published. They've been marked as seen so this "
            "won't repeat; check the source(s):\n" + detail
        )
    if not persisted:
        warning_parts.append(
            "WARNING: failed to save today's article state to git. This means "
            "today's new articles below (if any) will likely be re-sent as "
            "'new' again tomorrow, until this is fixed. Diagnostic output:\n\n"
            + persist_log
        )
    warning = "\n\n".join(warning_parts) if warning_parts else None

    if new_by_journal or warning:
        send_digest_email(new_by_journal, warning=warning)
    else:
        print("No new articles.")


if __name__ == "__main__":
    main()
