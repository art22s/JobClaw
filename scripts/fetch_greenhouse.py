#!/usr/bin/env python3
"""
fetch_greenhouse.py — Batch-fetches job listings from all Greenhouse API endpoints
configured in the career-ops portal list. No scraping required; returns structured JSON.

Usage:
    python3 fetch_greenhouse.py --output data/greenhouse_raw.json [--timeout 15]
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# Greenhouse companies to fetch.
# Includes career-ops AI/tech companies + broad mainstream tech/finance/healthcare
# companies that actively hire data analysts and BI analysts.
# Format: (company_name, greenhouse_slug)
GREENHOUSE_COMPANIES = [
    # --- Tech ---
    ("Airbnb",              "airbnb"),
    ("Coinbase",            "coinbase"),
    ("Dropbox",             "dropbox"),
    ("Pinterest",           "pinterest"),
    ("Reddit",              "reddit"),
    ("Discord",             "discord"),
    ("Lyft",                "lyft"),
    ("Twilio",              "twilio"),
    ("Duolingo",            "duolingo"),
    
    # --- AI / tech platforms (from career-ops) ---
    ("Anthropic",           "anthropic"),
    ("Intercom",            "intercom"),
    ("Hume AI",             "humeai"),
    ("Airtable",            "airtable"),
    ("Vercel",              "vercel"),
    ("Temporal",            "temporal"),
    ("Arize AI",            "arizeai"),
    ("RunPod",              "runpod"),
    ("Glean",               "gleanwork"),
    ("Celonis",             "celonis"),
    ("Contentful",          "contentful"),
    ("Stability AI",        "stabilityai"),
    ("Amplemarket",         "amplemarket"),

    # --- Mainstream tech (large data/analytics orgs) ---
    ("Figma",               "figma"),
    # ("Notion",              "notion"),          # MOVED off Greenhouse (404)
    ("Stripe",              "stripe"),
    ("Databricks",          "databricks"),
    # ("dbt Labs",            "dbtlabs"),         # MOVED off Greenhouse (404)
    ("Amplitude",           "amplitude"),
    ("Mixpanel",            "mixpanel"),
    ("Grafana Labs",        "grafanalabs"),
    ("Toast",               "toast"),
    # ("Ramp",                "ramp"),            # MOVED off Greenhouse (404)
    ("Brex",                "brex"),
    ("Gusto",               "gusto"),
    # ("Rippling",            "rippling"),        # MOVED off Greenhouse (404)
    ("Lattice",             "lattice"),
    # ("Carta",               "cartainc"),        # MOVED off Greenhouse (404)
    # ("Plaid",               "plaid"),           # MOVED off Greenhouse (404)
    ("Checkr",              "checkr"),

    # --- SaaS / growth companies ---
    ("HubSpot",             "hubspot"),
    # ("Zendesk",             "zendesk"),         # MOVED off Greenhouse (404)
    ("Attentive",           "attentive"),
    ("Iterable",            "iterable"),
    # ("Heap",                "heap"),            # MOVED off Greenhouse (404)
    # ("FullStory",           "fullstory"),       # MOVED off Greenhouse (404)
    # ("Loom",                "useloom"),         # MOVED off Greenhouse (404)
    ("Apollo.io",           "apolloio"),
    ("Gong",                "gongio"),         # fixed slug (was "gong")

    # --- Finance / fintech ---
    ("Robinhood",           "robinhood"),
    ("Chime",               "chime"),
    ("Nubank",              "nubank"),
    ("Marqeta",             "marqeta"),
    ("Affirm",              "affirm"),
    ("Faire",               "faire"),
    ("N26",                 "n26"),
    ("Trade Republic",      "traderepublicbank"),
    ("SumUp",               "sumup"),
    ("Mercer Advisors",     "merceradvisors"),
    ("OppFi",               "opploans"),

    # --- Healthcare / life sciences ---
    ("Spring Health",       "springhealth66"),
    # ("Sword Health",        "sword-health"),    # MOVED off Greenhouse (404)
    ("Garner Health",       "garnerhealth"),
    # ("Ro Health",           "ro"),              # MOVED off Greenhouse (404)
    # ("Hinge Health",        "hingehealth"),     # MOVED off Greenhouse (404)
    ("Modern Health",       "modernhealth"),
    # ("Color Health",        "color"),           # MOVED off Greenhouse (404)
    ("SmarterDx",           "smarterdx"),
    ("LetsGetChecked",      "letsgetchecked"),
    ("Doximity",            "doximity"),

    # --- E-commerce / marketplaces ---
    ("Life360",             "life360"),
    ("Pacaso",              "pacaso"),
    ("Mixbook",             "mixbook"),
    # ("Etsy",                "etsy"),            # MOVED off Greenhouse (404)
    # ("Wayfair",             "wayfair"),         # MOVED off Greenhouse (404)
    ("HelloFresh",          "hellofresh"),
    ("GetYourGuide",        "getyourguide"),

    # --- Media / content ---
    # ("Spotify",             "spotify"),         # MOVED off Greenhouse (404) — on Lever as 'spotify'

    # --- Remote-first / distributed ---

    # --- Boston / Northeast ---
    ("PathAI",              "pathai"),
    ("Salsify",             "salsify"),
    ("Real Chemistry",      "realchemistry"),
    ("ElevateBio",          "elevatebio"),
    ("Constant Contact",    "constantcontact"),
    ("Mark43",              "mark43"),
    ("Formlabs",            "formlabs"),
    ("Dataiku",             "dataiku"),           # Boston-area office
    ("AQR Capital",         "aqr"),               # Greenwich CT
    ("PitchBook",           "pitchbookdata"),      # NYC/Boston
    ("Tripadvisor",         "tripadvisor"),        # Boston HQ

    # --- Boston Greenhouse additions ---
    ("Motional",            "motional"),
    ("CarGurus",            "cargurus"),
    ("Tulip",               "tulip"),
    ("Hometap",             "hometap"),
    ("Groma",               "groma"),
    ("Yes Energy",          "yesenergy"),
    ("MKS2 Technologies",   "mks2technologies"),
    ("STR",                 "systemstechnologyresearch"),
    ("Material Bank",       "materialbank"),
    ("MERGE",               "mergeworld"),
    ("Ridgeline",           "ridgeline"),
    ("Olema Oncology",      "olema"),
    ("Cribl",               "cribl"),
    ("Guidepoint Security", "guidepointsecurity"),
    ("Unqork",              "unqork"),

    # --- MA Greenhouse additions (round 2) ---
    ("Pure Storage",        "purestorage"),
    ("Angi",                "angi"),
    ("Nuvalent",            "nuvalent"),
    ("Roivant Sciences",    "roivantsciences"),
    ("BridgeBio",           "bridgebio"),
    ("Tomorrow.io",         "tomorrow"),
    ("Fender",              "fender"),

    # --- MA Greenhouse additions (round 3) ---
    ("Acquia",              "acquia"),
    ("Nasuni",              "nasuni"),
    ("EverQuote",           "everquote"),
    ("Amylyx Pharmaceuticals", "amylyx"),

    # --- MA Greenhouse additions (round 4) ---
    ("Definitive Healthcare",  "definitivehc"),
    ("Lightmatter",           "lightmatter"),
    ("Amwell",                "amwell"),

    # --- Bay Area Greenhouse additions ---
    ("Okta",                 "okta"),
    ("Fivetran",             "fivetran"),
    ("Scale AI",             "scaleai"),
    ("Ripple",               "ripple"),
    ("Astranis",             "astranis"),
    ("JetBrains",            "jetbrains"),
    ("Abnormal Security",    "abnormalsecurity"),
    ("Fastly",               "fastly"),
    ("PagerDuty",            "pagerduty"),
    ("Sumo Logic",           "sumologic"),
    ("CircleCI",             "circleci"),

    # --- Major tech additions ---
    ("Datadog",             "datadog"),
    ("Cloudflare",          "cloudflare"),
    ("Instacart",           "instacart"),
    ("Block (Square)",      "block"),
    ("DeepMind",            "deepmind"),
    ("SpaceX",              "spacex"),
    ("Nuro",                "nuro"),
    ("Waymo",               "waymo"),
    ("Anduril",             "andurilindustries"),
    ("LaunchDarkly",        "launchdarkly"),
    ("Honor",               "honor"),
]

# Try EU endpoint first for European companies, fall back to US
EU_COMPANIES = {"polyai", "parloa"}

def fetch_company_jobs(company_name, slug, timeout=15):
    """Fetch all jobs for a single company from the Greenhouse API."""
    # Try EU endpoint for European companies
    if slug in EU_COMPANIES:
        base_url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        eu_url = f"https://boards-api.eu.greenhouse.io/v1/boards/{slug}/jobs"
        urls_to_try = [eu_url, base_url]
    else:
        urls_to_try = [f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"]

    for url in urls_to_try:
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 jobclaw/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                jobs = data.get("jobs", [])
                results = []
                for job in jobs:
                    # Normalize location: Greenhouse may return a list
                    location = ""
                    if isinstance(job.get("location"), dict):
                        location = job["location"].get("name", "")
                    elif isinstance(job.get("location"), str):
                        location = job["location"]

                    results.append({
                        "title": job.get("title", ""),
                        "url": job.get("absolute_url", ""),
                        "company": company_name,
                        "location": location,
                        "date_posted": job.get("updated_at", ""),
                        "description": "",  # Full JD not in list endpoint
                        "source": "greenhouse_api",
                        "slug": slug,
                    })
                print(f"  ✓ {company_name}: {len(results)} jobs", file=sys.stderr)
                return results
        except HTTPError as e:
            if e.code == 404:
                print(f"  ✗ {company_name}: 404 (slug may have changed)", file=sys.stderr)
                return []
            print(f"  ✗ {company_name}: HTTP {e.code}", file=sys.stderr)
        except URLError as e:
            print(f"  ✗ {company_name}: connection error — {e.reason}", file=sys.stderr)
        except Exception as e:
            print(f"  ✗ {company_name}: {e}", file=sys.stderr)

    return []


def main():
    parser = argparse.ArgumentParser(description="Batch-fetch jobs from Greenhouse API")
    parser.add_argument("--output", required=True, help="Path for output JSON file")
    parser.add_argument("--timeout", type=int, default=15, help="Per-request timeout in seconds")
    parser.add_argument("--workers", type=int, default=6, help="Parallel workers")
    args = parser.parse_args()

    print(f"Fetching jobs from {len(GREENHOUSE_COMPANIES)} Greenhouse companies...", file=sys.stderr)
    start = time.time()

    all_jobs = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(fetch_company_jobs, name, slug, args.timeout): (name, slug)
            for name, slug in GREENHOUSE_COMPANIES
        }
        for future in as_completed(futures):
            jobs = future.result()
            all_jobs.extend(jobs)

    elapsed = time.time() - start
    print(f"\nDone. {len(all_jobs)} total jobs fetched in {elapsed:.1f}s", file=sys.stderr)

    with open(args.output, "w") as f:
        json.dump(all_jobs, f, indent=2)

    print(f"Saved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
