#!/usr/bin/env python3
"""
Daily refresher for the CALABARZON health field report.

WHY THIS VERSION IS DIFFERENT FROM v1:
  The first version rewrote health_data.json from scratch every run,
  looking only at whatever happened to be on PIA's front page at that
  exact moment. Two problems with that:
    1. If an item scrolled off the front page before a run caught it,
       it was gone for good — nothing remembered it existed.
    2. On a quiet day with no new CALABARZON health news, the file (and
       the report) would show an EMPTY feed instead of "nothing new
       today, here's what we knew before."
  This version fixes both: it loads the previous run's health_data.json,
  merges in anything new it finds (deduped by URL), and keeps a rolling
  window of the most recent items — so the feed only grows or refreshes,
  it never silently loses something or goes blank.

WHAT IT DOES, STEP BY STEP:
  1. Scrapes PAGASA's Southern Luzon regional forecast page for the
     current condition + a short outlook. (Weather has no history to
     keep — it's always just "today's" snapshot, overwritten each run.)
  2. Scrapes PIA's news listing (https://pia.gov.ph/news/) for articles
     tagged CALABARZON whose headline mentions a health/outbreak keyword.
  3. Loads any existing health_data.json (from the repo checkout) and
     merges: same URL = update it (last_seen bumped), new URL = add it
     with today's date as first_seen.
  4. Runs the keyword lexicon ("triggers" — see TRIGGERS section below)
     over every item's text, old and new, so tags stay in sync if you
     edit the lexicon later.
  5. Sorts by date, trims to the most recent MAX_SIGNALS, writes the
     result to health_data.json.

TRIGGERS — how the tagging actually works:
  KEYWORD_LEXICON below is a list of (regex, label, css_class) tuples.
  Every signal's text is tested against every pattern; whichever ones
  match get attached as tags. To add a new trigger, add one line, e.g.:
      (re.compile(r"contact tracing|case investigation", re.I),
       "Contact tracing", "response"),
  The css_class must match one already styled in health_report.html
  (cluster / emergency / response / rising / declining / rank) unless
  you also add a new `.chip.<class>` rule there.

RUN FREQUENCY:
  The included GitHub Actions workflow runs this once a day. PIA and
  PAGASA don't publish on a fixed schedule, so running more than once a
  day (e.g. every 6 hours) will catch same-day news faster if that
  matters to you — just add more cron lines in daily-refresh.yml.

IMPORTANT — read this before relying on it:
  Government sites change their HTML without notice. The selectors below
  are a best-effort based on the sites' structure as observed manually;
  they are NOT guaranteed to keep working. Treat failures as "the site
  changed its markup, go look at it again," not as a sign anything else
  is wrong.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CalabarzonHealthBot/1.0)"}

PAGASA_URL = "https://www.pagasa.dost.gov.ph/regional-forecast/slprsd"
PIA_NEWS_URL = "https://pia.gov.ph/news/"
DATA_FILE = "health_data.json"

MAX_SIGNALS = 15      # keep the feed from growing forever
CRAWL_LIMIT = 40      # how many PIA articles to actually check per run
                       # (v1 stopped at 5 matches, which was too shallow —
                       # this checks up to 40 cards on the page before
                       # giving up, so a slow news day doesn't cut it off
                       # before it finds the CALABARZON+health items)

HEALTH_KEYWORDS = [
    "dengue", "leptospirosis", "measles", "rubella", "doh ", "department of health",
    "outbreak", "immunization", "vaccination", "vaccine", "influenza", "flu-like",
    "covid", "health advisory", "epidemic", "code white", "monitoring", "surveillance",
    "hospital", "case investigation", "contact tracing",
]

# --- TRIGGERS ---------------------------------------------------------
# (regex, label shown as a chip, css class used to color that chip)
KEYWORD_LEXICON = [
    (re.compile(r"clustering|cluster(s|ed)?", re.I), "Clustering", "cluster"),
    (re.compile(r"state of public health emergency|emergency", re.I), "Emergency declared", "emergency"),
    (re.compile(r"fast lanes?|hydration station|code white alert|on standby|heightened alert", re.I), "Response activated", "response"),
    (re.compile(r"immunization (drive|campaign)|vaccination (drive|campaign)|supplemental immunization", re.I), "Immunization drive", "response"),
    (re.compile(r"contact tracing|case investigation|surveillance", re.I), "Contact tracing", "response"),
    (re.compile(r"rising|increase|increasing|climb(ed|ing)?|higher|surge", re.I), "Rising cases", "rising"),
    (re.compile(r"down|decrease|declin(e|ing)|lower", re.I), "Declining", "declining"),
    (re.compile(r"third-highest|highest|topped|most cases|leading", re.I), "Regional rank alert", "rank"),
]


def tag_text(text):
    tags = []
    seen = set()
    for pattern, label, cls in KEYWORD_LEXICON:
        if pattern.search(text) and label not in seen:
            tags.append(label)
            seen.add(label)
    return tags


def parse_date(date_str):
    """Best-effort parse for sorting; falls back to epoch (sorts last)."""
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except (ValueError, AttributeError):
            continue
    return datetime.min


def fetch_pagasa():
    """Best-effort scrape of PAGASA's Southern Luzon regional forecast.
    No history here — weather is always just today's snapshot."""
    try:
        r = requests.get(PAGASA_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ", strip=True)

        condition_match = re.search(
            r"(Cloudy skies[^.\d]{0,60}|Occasional rains[^.\d]{0,40}|Rainfall Advisory[^.]{0,80}|"
            r"Thunderstorm Watch[^.]{0,80})",
            text,
        )
        condition = condition_match.group(0).strip() if condition_match else \
            "See PAGASA Southern Luzon regional forecast for current conditions"

        temp_match = re.search(r"(\d{2})\D{1,3}(\d{2})\D{0,3}(?:°|deg)", text)
        highlight = f"{temp_match.group(1)}°–{temp_match.group(2)}°C" if temp_match else ""

        wind_match = re.search(r"Wind Speed:\s*([^·]+)", text)
        meta_bits = []
        if wind_match:
            meta_bits.append(wind_match.group(1).strip())
        if "habagat" in text.lower() or "southwest monsoon" in text.lower():
            meta_bits.append("Habagat (SW Monsoon) active")
        meta = " · ".join(meta_bits) if meta_bits else "See PAGASA for full synopsis"

        outlook = []
        for day, lo, hi in re.findall(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\D{0,20}(\d{2})\D{1,3}(\d{2})", text):
            outlook.append({"label": day[:3], "time": f"{lo}\u2013{hi}\u00b0"})
            if len(outlook) >= 3:
                break

        return {
            "condition": condition,
            "highlight": highlight or "See PAGASA bulletin",
            "meta": meta,
            "outlook": outlook,
        }
    except Exception as e:
        print(f"[warn] PAGASA fetch failed: {e}", file=sys.stderr)
        return None


def fetch_pia_calabarzon_health_news():
    """Best-effort scrape of PIA's news listing for CALABARZON health items.
    Checks up to CRAWL_LIMIT cards on the page — not just the first few —
    so a quiet-looking front page doesn't cut the search off early."""
    items = []
    try:
        r = requests.get(PIA_NEWS_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        cards = soup.select("article, .post, .news-item")[:CRAWL_LIMIT]
        for article in cards:
            title_el = article.find(["h1", "h2", "h3", "a"])
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not title:
                continue

            link_el = article.find("a", href=True)
            link = link_el["href"] if link_el else PIA_NEWS_URL
            if not link.startswith("http"):
                link = f"https://pia.gov.ph{link}"

            full_text = article.get_text(" ", strip=True)
            is_calabarzon = "calabarzon" in full_text.lower() or "region 4-a" in full_text.lower()
            is_health = any(k in title.lower() for k in HEALTH_KEYWORDS)

            if is_calabarzon and is_health:
                date_match = re.search(
                    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}",
                    full_text,
                )
                items.append({
                    "where": "CALABARZON",
                    "date": date_match.group(0) if date_match else datetime.now().strftime("%b %d, %Y"),
                    "text": title,
                    "url": link,
                    "src": "PIA",
                })
    except Exception as e:
        print(f"[warn] PIA fetch failed: {e}", file=sys.stderr)
    return items


def load_previous():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("signals", [])
        except Exception as e:
            print(f"[warn] could not read previous {DATA_FILE}: {e}", file=sys.stderr)
    return []


def merge_signals(previous, fresh):
    """Dedup by URL. Existing items keep their original first_seen date
    and get last_seen bumped; new items are added with today as both."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    by_url = {item["url"]: item for item in previous}

    new_count = 0
    for item in fresh:
        if item["url"] in by_url:
            by_url[item["url"]]["last_seen"] = today
        else:
            item["first_seen"] = today
            item["last_seen"] = today
            by_url[item["url"]] = item
            new_count += 1

    merged = list(by_url.values())
    for item in merged:
        item["tags"] = tag_text(item["text"])

    merged.sort(key=lambda x: parse_date(x.get("date", "")), reverse=True)
    print(f"[info] {new_count} new item(s) found this run, {len(merged)} total in feed")
    return merged[:MAX_SIGNALS]


def main():
    previous = load_previous()
    fresh = fetch_pia_calabarzon_health_news()
    signals = merge_signals(previous, fresh)

    weather = fetch_pagasa()

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC"),
        "signals": signals,
        "weather": weather,
    }

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Wrote {DATA_FILE} \u2014 {len(signals)} signals in feed, weather={'ok' if weather else 'failed'}")


if __name__ == "__main__":
    main()
