# Making the report catch news day to day

The HTML report works fine on its own — it ships with a manually-compiled
snapshot. This automation is what makes it actually accumulate new items
and re-check its "triggers" (keyword tags) on a schedule, without you or
Claude touching it.

## Why this needs to live outside claude.ai

The report is a static HTML file with no server behind it. DOH, PIA, and
PAGASA don't offer a public API, and a browser can't run a job on a
schedule — so "catch it day to day" has to happen *somewhere that can
run code on a timer*. GitHub Actions is the easiest free option that fits.

## How the day-to-day catching actually works

- **It doesn't overwrite itself.** Each run loads the previous
  `health_data.json`, scrapes PIA's news listing again, and merges:
  same article URL = just bumps a `last_seen` date; a URL it's never
  seen before = added fresh, stamped with today as `first_seen`. Nothing
  gets silently dropped just because it scrolled off PIA's front page.
- **It re-tags everything, every run.** If you add a new trigger to the
  lexicon (see below), old items get re-tagged with it too the next
  time the script runs — you don't lose history when you tune triggers.
- **It keeps the most recent 15** (see `MAX_SIGNALS` in the script) so
  the feed doesn't grow forever. Raise that number if you want more.
- **It runs 4x a day by default** (00:00 / 06:00 / 12:00 / 18:00 UTC),
  so a same-day story doesn't wait a full 24 hours to show up. Trim the
  `.github/workflows/daily-refresh.yml` schedule down to once a day if
  you'd rather keep GitHub Actions usage minimal.

## The "triggers" — how to add or tune them

Open `refresh_health_data.py` and find `KEYWORD_LEXICON`. Each line is:

```python
(re.compile(r"rising|increase|increasing|climb(ed|ing)?|higher|surge", re.I), "Rising cases", "rising"),
```

`(regex pattern, chip label, css class)`. Every signal's headline gets
tested against every pattern; whatever matches becomes a chip in the
report. To add a new trigger — say, "hospital admissions" — add:

```python
(re.compile(r"admission|hospitaliz", re.I), "Hospital admissions", "rising"),
```

The css class (`cluster` / `emergency` / `response` / `rising` /
`declining` / `rank`) controls the chip's color and must already exist
in `health_report.html`'s CSS — reuse one of those unless you also add a
new `.chip.<yourclass>` rule there.

You can also loosen `HEALTH_KEYWORDS` (what counts as a "health" headline
in the first place) the same way — it's just a list of substrings
checked against each article's title.

## Setup (about 5 minutes)

1. **Create a public GitHub repo** (e.g. `calabarzon-health-data`).
2. **Add these files**, keeping the folder structure:
   - `refresh_health_data.py`
   - `.github/workflows/daily-refresh.yml`
3. **Commit and push.** Go to the repo's **Actions** tab, find "Daily
   health data refresh," and click **Run workflow** once manually — it
   should create `health_data.json` in the repo root and print
   `[info] N new item(s) found this run` in the log.
4. **Copy the raw URL** of that file:
   `https://raw.githubusercontent.com/YOUR_USERNAME/calabarzon-health-data/main/health_data.json`
5. **Open `health_report.html`**, find:
   ```js
   const LIVE_DATA_URL = null;
   ```
   and change it to your raw URL from step 4.
6. Save, reopen the report. A green **● LIVE** tag appears next to the
   PAGASA panel title when the fetch succeeds, and the signals feed
   footer switches from "last compiled" to "live, refreshed <timestamp>."

## Honest limitations

- **Selectors are best-effort.** I wrote them against how PAGASA's and
  PIA's pages are structured right now, but I can't test them against
  the live internet from where I'm running, and government sites
  redesign without notice. Check the Actions log first if it stops
  finding anything — it's almost always a selector needing an update.
- **PIA's listing page is the only news source right now.** DOH doesn't
  have a scrapable press-release feed of its own, so anything PIA
  doesn't cover, this won't catch either. Tell me if you want another
  source added (e.g. a specific DOH regional office's Facebook page,
  if you have a way to access it) and I'll wire it in.
- **If a run fails for any reason**, the previous `health_data.json`
  just stays as-is (nothing gets deleted), and the report falls back to
  its baked-in snapshot if the fetch itself fails — it never breaks
  the page.
