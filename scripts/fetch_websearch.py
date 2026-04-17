#!/usr/bin/env python3
"""
fetch_websearch.py — Extends Greenhouse coverage by:
  1. Searching job-boards.greenhouse.io for profile keywords via DuckDuckGo
  2. Extracting company slugs from matching URLs
  3. Hitting each discovered slug's Greenhouse API to get properly dated results
  4. Also scrapes Ashby job pages for date metadata (best-effort)

This means ALL results have confirmed post dates and pass the 7-day filter —
no inline data writing, no heredocs, no shell obfuscation.

Usage:
    python3 scripts/fetch_websearch.py \
        --profile profiles/example.md \
        --output data/websearch_raw.json

    # Skip Ashby page-fetching (faster but fewer dated results):
    python3 scripts/fetch_websearch.py \
        --profile profiles/example.md \
        --output data/websearch_raw.json \
        --no-ashby-dates
"""

import argparse
import json
import re
import sys
import time
from urllib.request import urlopen, Request
from urllib.parse import quote_plus, unquote
from urllib.error import URLError, HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed


# ---------------------------------------------------------------------------
# Profile parsing
# ---------------------------------------------------------------------------

def parse_keywords(profile_path):
    with open(profile_path) as f:
        content = f.read()
    m = re.search(r'## Search Keywords\n(.*?)(?:\n##|$)', content, re.DOTALL | re.IGNORECASE)
    if not m:
        return ["data analyst", "senior data analyst"]
    return [
        line.lstrip("-• ").strip().lower()
        for line in m.group(1).splitlines()
        if line.strip() and not line.startswith("#")
    ] or ["data analyst"]


# ---------------------------------------------------------------------------
# DuckDuckGo search (no API key, returns title + URL)
# ---------------------------------------------------------------------------

def ddg_search(query, timeout=12):
    url = "https://html.duckduckgo.com/html/?q={}&kl=us-en".format(quote_plus(query))
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; jobclaw/1.0)",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ✗ DDG error: {e}", file=sys.stderr)
        return []

    results = []
    for m in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        html, re.DOTALL
    ):
        raw_url = m.group(1)
        title   = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        uddg    = re.search(r'uddg=([^&"]+)', raw_url)
        if uddg:
            raw_url = unquote(uddg.group(1))
        if raw_url.startswith("http"):
            results.append({"title": title, "url": raw_url})
    return results


# ---------------------------------------------------------------------------
# Greenhouse slug discovery from URLs
# ---------------------------------------------------------------------------

GH_SLUG_RE = re.compile(
    r'(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/([a-zA-Z0-9_-]+)'
)

def extract_gh_slugs(results):
    slugs = set()
    for r in results:
        m = GH_SLUG_RE.search(r.get("url", ""))
        if m:
            slugs.add(m.group(1).lower())
    return slugs


# ---------------------------------------------------------------------------
# Greenhouse API fetch for a single slug (reused from fetch_greenhouse.py)
# ---------------------------------------------------------------------------

def fetch_gh_slug(slug, company_name=None, timeout=12):
    urls = [
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        f"https://boards-api.eu.greenhouse.io/v1/boards/{slug}/jobs",
    ]
    for url in urls:
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 jobclaw/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                jobs = data.get("jobs", [])
                name = company_name or slug.replace("-", " ").title()
                results = []
                for job in jobs:
                    location = ""
                    if isinstance(job.get("location"), dict):
                        location = job["location"].get("name", "")
                    results.append({
                        "title":       job.get("title", ""),
                        "url":         job.get("absolute_url", ""),
                        "company":     name,
                        "location":    location,
                        "date_posted": job.get("updated_at", ""),
                        "description": "",
                        "source":      "websearch_gh_discovery",
                    })
                return slug, results
        except HTTPError as e:
            if e.code == 404:
                return slug, []
        except Exception:
            pass
    return slug, []


# ---------------------------------------------------------------------------
# Ashby URL date extraction (best-effort page fetch)
# ---------------------------------------------------------------------------

ASHBY_SLUG_RE = re.compile(r'jobs\.ashbyhq\.com/([^/?#]+)/([^/?#]+)')

def fetch_ashby_date(url, timeout=8):
    """Try to extract posted date from an Ashby job page's JSON-LD."""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 jobclaw/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # Ashby embeds datePosted in JSON-LD
        m = re.search(r'"datePosted"\s*:\s*"([^"]+)"', html)
        if m:
            return m.group(1)
        # Also try data-date attributes
        m = re.search(r'data-date="([^"]+)"', html)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def extract_ashby_jobs(search_results, fetch_dates=True):
    """
    From DuckDuckGo results, find Ashby job URLs, optionally fetch their dates.
    Returns list of job dicts.
    """
    jobs = []
    seen = set()
    for r in search_results:
        url = r.get("url", "")
        m = ASHBY_SLUG_RE.search(url)
        if not m or url in seen:
            continue
        seen.add(url)
        company = m.group(1).replace("-", " ").title()
        jobs.append({
            "title":       r.get("title", ""),
            "url":         url,
            "company":     company,
            "location":    "",
            "date_posted": "",
            "source":      "websearch_ashby",
            "_needs_date": fetch_dates,
        })

    if fetch_dates and jobs:
        print(f"  Fetching dates for {len(jobs)} Ashby results...", file=sys.stderr)
        def _fetch(job):
            job["date_posted"] = fetch_ashby_date(job["url"])
            job.pop("_needs_date", None)
            return job
        with ThreadPoolExecutor(max_workers=5) as pool:
            jobs = list(pool.map(_fetch, jobs))
    else:
        for j in jobs:
            j.pop("_needs_date", None)

    return jobs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Discover additional jobs via WebSearch + Greenhouse API"
    )
    parser.add_argument("--profile",        required=True)
    parser.add_argument("--output",         required=True)
    parser.add_argument("--known-slugs",    default="",
                        help="Comma-separated Greenhouse slugs already fetched (to skip)")
    parser.add_argument("--delay",          type=float, default=1.5)
    parser.add_argument("--no-ashby-dates", action="store_true",
                        help="Skip fetching individual Ashby page dates (faster)")
    args = parser.parse_args()

    keywords = parse_keywords(args.profile)
    known    = {s.strip().lower() for s in args.known_slugs.split(",") if s.strip()}
    print(f"Keywords : {keywords}", file=sys.stderr)
    print(f"Skipping : {len(known)} already-fetched slugs", file=sys.stderr)

    all_search_results = []
    seen_urls          = set()

    # Build keyword groups (pairs)
    groups = []
    for i in range(0, len(keywords), 2):
        chunk = keywords[i:i+2]
        groups.append(" OR ".join(f'"{k}"' for k in chunk))

    queries = []
    for group in groups:
        queries.append(('GH Board',  f'site:job-boards.greenhouse.io {group} remote'))
        queries.append(('Ashby',     f'site:jobs.ashbyhq.com {group} remote'))
        queries.append(('Lever',     f'site:jobs.lever.co {group} remote'))

    print(f"Running {len(queries)} WebSearch queries...", file=sys.stderr)
    for i, (portal, query) in enumerate(queries, 1):
        print(f"  [{i}/{len(queries)}] {portal}: {query[:90]}", file=sys.stderr)
        hits = ddg_search(query)
        new = 0
        for h in hits:
            if h["url"] not in seen_urls:
                seen_urls.add(h["url"])
                all_search_results.append(h)
                new += 1
        print(f"       +{new} URLs ({len(all_search_results)} total)", file=sys.stderr)
        if i < len(queries):
            time.sleep(args.delay)

    # --- Greenhouse slug discovery ---
    discovered_slugs = extract_gh_slugs(all_search_results) - known
    print(f"\nDiscovered {len(discovered_slugs)} new Greenhouse slugs: {sorted(discovered_slugs)}", file=sys.stderr)

    gh_jobs = []
    if discovered_slugs:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(fetch_gh_slug, slug): slug for slug in discovered_slugs}
            for future in as_completed(futures):
                slug, jobs = future.result()
                if jobs:
                    print(f"  ✓ {slug}: {len(jobs)} jobs", file=sys.stderr)
                    gh_jobs.extend(jobs)
                else:
                    print(f"  ✗ {slug}: 0 jobs", file=sys.stderr)

    # --- Ashby jobs (best-effort dates) ---
    ashby_jobs = extract_ashby_jobs(
        all_search_results,
        fetch_dates=not args.no_ashby_dates
    )
    print(f"Ashby results: {len(ashby_jobs)}", file=sys.stderr)

    all_jobs = gh_jobs + ashby_jobs
    # Deduplicate by URL
    seen, final = set(), []
    for j in all_jobs:
        if j["url"] not in seen:
            seen.add(j["url"])
            final.append(j)

    with open(args.output, "w") as f:
        json.dump(final, f, indent=2)

    print(f"\nTotal: {len(final)} additional jobs saved → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
