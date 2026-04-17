#!/usr/bin/env python3
"""
fetch_lever.py — Batch-fetches job listings from Lever's public Postings API.
No authentication required; returns structured JSON with full descriptions.

The Lever Postings API returns full job descriptions (unlike Workday),
similar to Greenhouse. Slugs are discovered from the company list below
and validated at runtime (404s are silently skipped).

Usage:
    python3 fetch_lever.py --output /tmp/js3_lever_raw.json [--timeout 15] [--workers 6]
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# Lever companies to fetch.
# The slug appears after jobs.lever.co/ in the company's career URL.
# Many big tech companies use Greenhouse or their own ATS, not Lever.
# Lever tends to be used by mid-size startups and enterprise companies
# in the 200-2,000 employee range.
# Format: (company_name, lever_slug)
LEVER_COMPANIES = [
    # --- Confirmed valid slugs (validated 2026-04-16) ---
    # Job counts from production test with 30s timeout
    ("Spotify", "spotify"),                  # 180
    ("Binance", "binance"),                  # 369
    ("Octopus Energy", "octoenergy"),        # 181
    ("Whoop", "whoop"),                      # 156
    ("Saronic", "saronic"),                  # 264
    ("Ci&T", "ciandt"),                      # 191
    ("Kyivstar", "kyivstar"),                # 167
    ("Veepee", "veepee"),                    # 137
    ("GoHighLevel", "gohighlevel"),          # 112
    ("Match Group", "matchgroup"),           # 101
    ("Plaid", "plaid"),                      # 91
    ("Dun & Bradstreet", "dnb"),             # 84
    ("Sword Health", "swordhealth"),         # 80
    ("Pigment", "pigment"),                  # 77
    ("WalkMe", "walkme"),                    # 73
    ("Allegiant Air", "allegiantair"),       # 45
    ("Ro", "ro"),                            # 44
    ("Canary Technologies", "canarytechnologies"),  # 37
    ("WeRide", "weride"),                    # 33
    ("Sysdig", "sysdig"),                    # 30
    ("Analytic Partners", "analyticpartners"),      # 29
    ("Rover", "rover"),                      # 28
    ("Outreach", "outreach"),                # 28
    ("Greenlight", "greenlight"),            # 24
    ("Bounteous", "bounteous"),              # 23
    ("Tendo", "tendo"),                      # 20
    ("Arcadia", "arcadia"),                  # 16
    ("Addition Wealth", "additionwealth"),  # 16
    ("Windfall Data", "windfalldata"),       # 16
    ("Nextech", "nextech"),                  # 15
    ("Grant Street Group", "grantstreet"),   # 14
    ("ComputerCare", "computercare"),         # 7
    ("Neighbor", "neighbor"),                # 5
    ("Viabill", "viabill"),                  # 4
    # EXCLUDED: Jobgether (jobgether) — aggregator with 3,858 jobs

    # --- Speculative slugs (404s silently skipped) ---
    # These may or may not work; kept for opportunistic discovery
    ("Xsolla", "xsolla"),
    ("Shield AI", "shieldai"),
    ("HubSpot", "hubspot"),
    ("Brex", "brex"),
    ("Ramp", "ramp"),
    ("Gusto", "gusto"),
    ("Rippling", "rippling"),
    ("Checkr", "checkr"),
    ("Discord", "discord"),
    ("DoorDash", "doordash"),
    ("Coinbase", "coinbase"),
    ("Dropbox", "dropbox"),
    ("Pinterest", "pinterest"),
    ("Reddit", "reddit"),
    ("Lyft", "lyft"),
    ("Twilio", "twilio"),
    ("Duolingo", "duolingo"),
    ("Datadog", "datadog"),
    ("Cloudflare", "cloudflare"),
    ("Instacart", "instacart"),
    ("Notion", "notion"),
    ("Stripe", "stripe"),
    ("Figma", "figma"),
    ("Airtable", "airtable"),
    ("Deel", "deel"),
    ("Vercel", "vercel"),
    ("HashiCorp", "hashicorp"),
    ("Docker", "docker"),
    ("GitLab", "gitlab"),
    ("Segment", "segment"),
    ("LaunchDarkly", "launchdarkly"),
    ("Snyk", "snyk"),
    ("Drata", "drata"),
    ("Benchling", "benchling"),
    ("Coda", "coda"),
    ("Typeform", "typeform"),
    ("Linear", "linear"),
    ("Postman", "postman"),
    ("Grafana Labs", "grafanalabs"),
    ("Fivetran", "fivetran"),
    ("Amplitude", "amplitude"),
    ("Mixpanel", "mixpanel"),
    ("Snowflake", "snowflake"),
    ("MongoDB", "mongodb"),
    ("Okta", "okta"),
    ("Waymo", "waymo"),
    ("Anduril", "andurilindustries"),
    ("SpaceX", "spacex"),
    ("Scale AI", "scaleai"),
    ("Wayfair", "wayfair"),
    ("Etsy", "etsy"),
    ("Robinhood", "robinhood"),
    ("Affirm", "affirm"),
    ("Chime", "chime"),
]


def fetch_company_jobs(company_name, slug, timeout=15):
    """Fetch all jobs for a single company from the Lever Postings API."""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 job-search-3/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not isinstance(data, list):
                # Error response like {"ok": false, "error": "Document not found"}
                return []

            results = []
            for job in data:
                cats = job.get("categories", {})
                desc = job.get("description", {})
                if isinstance(desc, dict):
                    desc_plain = desc.get("plain", "")
                    desc_nice = desc.get("nice", "")
                else:
                    desc_plain = str(desc)
                    desc_nice = ""

                # Convert createdAt timestamp to ISO date
                created_ts = job.get("createdAt")
                if isinstance(created_ts, (int, float)):
                    date_posted = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created_ts / 1000))
                else:
                    date_posted = ""

                results.append({
                    "title": job.get("text", ""),
                    "url": job.get("hostedUrl", ""),
                    "company": company_name,
                    "location": cats.get("location", ""),
                    "team": cats.get("team", ""),
                    "department": cats.get("department", ""),
                    "commitment": cats.get("commitment", ""),  # e.g. "Full Time"
                    "level": cats.get("level", ""),
                    "workplace_type": job.get("workplaceType", ""),
                    "country": job.get("country", ""),
                    "date_posted": date_posted,
                    "description": desc_plain,
                    "description_html": desc_nice,
                    "apply_url": job.get("applyUrl", ""),
                    "source": "lever_api",
                    "slug": slug,
                })
            print(f"  ✓ {company_name} ({slug}): {len(results)} jobs", file=sys.stderr)
            return results
    except HTTPError as e:
        if e.code == 404:
            # Normal — slug doesn't exist
            pass
        else:
            print(f"  ✗ {company_name} ({slug}): HTTP {e.code}", file=sys.stderr)
    except URLError as e:
        print(f"  ✗ {company_name} ({slug}): connection error — {e.reason}", file=sys.stderr)
    except Exception as e:
        print(f"  ✗ {company_name} ({slug}): {e}", file=sys.stderr)
    return []


def main():
    parser = argparse.ArgumentParser(description="Batch-fetch jobs from Lever Postings API")
    parser.add_argument("--output", required=True, help="Path for output JSON file")
    parser.add_argument("--timeout", type=int, default=15, help="Per-request timeout in seconds")
    parser.add_argument("--workers", type=int, default=6, help="Parallel workers")
    args = parser.parse_args()

    # Deduplicate slugs (some appear twice in the list)
    seen = set()
    unique_companies = []
    for name, slug in LEVER_COMPANIES:
        if slug.lower() not in seen:
            seen.add(slug.lower())
            unique_companies.append((name, slug))

    print(f"Fetching jobs from {len(unique_companies)} Lever companies...", file=sys.stderr)
    start = time.time()

    all_jobs = []
    companies_with_jobs = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(fetch_company_jobs, name, slug, args.timeout): (name, slug)
            for name, slug in unique_companies
        }
        for future in as_completed(futures):
            jobs = future.result()
            if jobs:
                all_jobs.extend(jobs)
                companies_with_jobs += 1

    elapsed = time.time() - start
    print(f"\nDone. {len(all_jobs)} total jobs from {companies_with_jobs} companies "
          f"(out of {len(unique_companies)} tried) in {elapsed:.1f}s", file=sys.stderr)

    with open(args.output, "w") as f:
        json.dump(all_jobs, f, indent=2)

    print(f"Saved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
