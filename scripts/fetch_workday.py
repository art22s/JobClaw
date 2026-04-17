#!/usr/bin/env python3
"""
fetch_workday.py — Batch-fetches job listings from 26 Workday career portals.

Uses the undocumented-but-stable internal API that powers every myworkdayjobs.com
career page:
  POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

Output format matches fetch_greenhouse.py so filter_jobs.py can consume it directly.
Workday's `postedOn` field returns relative strings like "Posted 2 Days Ago" which
filter_jobs.py already handles via its days_ago() parser.

Usage:
    python3 scripts/fetch_workday.py \
        --profile profiles/example.md \
        --output data/workday_raw.json

    # Override keywords (comma-separated):
    python3 scripts/fetch_workday.py \
        --keywords "data analyst,BI analyst,analytics engineer" \
        --output data/workday_raw.json
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import HTTPError


# ---------------------------------------------------------------------------
# Verified Workday tenants (26 companies, live-tested April 2026)
# Format: (company_display_name, tenant, wd_number, site_name)
#
# Companies verified NOT on Workday (use other ATS):
#   American Express → Eightfold AI / Taleo
#   ServiceNow       → SmartRecruiters
#   Charles Schwab   → iCIMS
#   UnitedHealth     → Oracle Taleo
#   Under Armour     → SAP SuccessFactors
#   Kaiser Permanente → Oracle Taleo
#   Best Buy         → ServiceNow platform
# ---------------------------------------------------------------------------

WORKDAY_COMPANIES = [
    # --- Tech ---
    ("NVIDIA",                  "nvidia",           "5",  "NVIDIAExternalCareerSite"),
    ("Salesforce",              "salesforce",       "12", "External_Career_Site"),
    ("Adobe",                   "adobe",            "5",  "external_experienced"),
    ("Workday",                 "workday",          "5",  "Workday"),
    ("Netflix",                 "netflix",          "1",  "netflix"),
    ("Cisco",                   "cisco",            "5",  "Cisco_Careers"),
    ("Intel",                   "intel",            "1",  "External"),

    # --- Finance / Payments ---
    ("Capital One",             "capitalone",       "12", "Capital_One"),
    ("Equifax",                 "equifax",          "5",  "External"),
    ("Visa",                    "visa",             "5",  "Visa_Early_Careers"),
    ("Mastercard",              "mastercard",       "1",  "CorporateCareers"),
    ("PayPal",                  "paypal",           "1",  "jobs"),

    ("Vanguard",                "vanguard",         "5",  "vanguard_external"),

    # --- Retail ---
    ("Target",                  "target",           "5",  "targetcareers"),
    ("Walmart",                 "walmart",          "5",  "WalmartExternal"),
    ("Nike",                    "nike",             "1",  "nke"),
    ("Home Depot",              "homedepot",        "5",  "CareerDepot"),
    ("Lowe's",                  "lowes",            "5",  "LWS_External_CS"),
    ("Gap Inc.",                "gapinc",           "1",  "GAPINC"),

    # --- Media / Entertainment ---
    ("Disney",                  "disney",           "5",  "disneycareer"),
    ("Comcast",                 "comcast",          "5",  "Comcast_Careers"),

    # --- Healthcare / Insurance ---
    ("Cigna",                   "cigna",            "5",  "cignacareers"),
    ("Humana",                  "humana",           "5",  "Humana_External_Career_Site"),
    ("Elevance Health",         "elevancehealth",   "1",  "ANT"),
    ("CVS Health",              "cvshealth",        "1",  "cvs_health_careers"),
    ("Johnson & Johnson",       "jj",               "5",  "JJ"),
    ("Pfizer",                  "pfizer",           "1",  "PfizerCareers"),
    ("Medtronic",               "medtronic",        "1",  "MedtronicCareers"),

    # --- Diversified / Industrial ---
    ("3M",                      "3m",               "1",  "Search"),
    ("Boeing",                  "boeing",           "1",  "External_Careers"),
    ("Chevron",                 "chevron",          "5",  "Jobs"),
    ("AstraZeneca",             "astrazeneca",      "3",  "Careers"),

    # --- Boston / Northeast ---
    ("Blue Cross Blue Shield MA","bcbsma",          "5",  "BCBSMA"),
    ("Boston Dynamics",          "bostondynamics",   "1",  "Boston_Dynamics"),
    ("Cambridge Associates",     "cambridgeassociates","5","Cambridge_Associates"),
    ("PTC",                      "ptc",              "1",  "PTC"),
    ("Arbella Insurance",        "arbella",          "5",  "Arbella"),
    ("Goodwin Procter",          "goodwinprocter",   "5",  "External_Careers"),
    ("Northeastern University",  "northeastern",     "1",  "careers"),
    ("HarbourVest Partners",     "harbourvest",      "5",  "HVP"),

    # --- Boston Workday additions ---
    ("DraftKings",               "draftkings",       "1",  "DraftKings"),
    ("Vertex Pharmaceuticals",   "vrtx",             "501","Vertex_Careers"),
    ("IQVIA",                    "iqvia",            "1",  "IQVIA"),
    ("Gilead Sciences",          "gilead",           "1",  "gileadcareers"),
    ("HHMI",                     "hhmi",             "1",  "External"),

    # --- Boston Workday additions (round 2) ---
    ("Moderna",                   "modernatx",        "1",  "M_tx"),
    ("DataRobot",                 "datarobot",        "1",  "DataRobot_External_Careers"),
    ("Devoted Health",            "devoted",           "1",  "Devoted"),

    # --- MA Workday additions (round 2) ---
    ("MSD (Merck)",               "msd",               "5",  "SearchJobs"),
    ("GE Vernova",                "gevernova",         "5",  "Vernova_ExternalSite"),
    ("Autodesk",                  "autodesk",          "1",  "Ext"),

    # --- MA Workday additions (round 3) ---
    ("Takeda",                    "takeda",            "3",  "External"),
    ("Amgen",                     "amgen",             "1",  "Careers"),
    ("Regeneron",                 "regeneron",         "1",  "Careers"),
    ("Intellia Therapeutics",     "intelliatx",        "1",  "intelliatxcareers"),
    ("Iron Mountain",              "ironmountain",      "5",  "iron-mountain-jobs"),

    # --- MA Workday additions (round 5, deep search) ---

    ("Sanofi",                     "sanofi",            "3",  "SanofiCareers"),
    ("Biogen",                     "biibhr",            "3",  "external"),
]


# ---------------------------------------------------------------------------
# Profile parsing (same approach as filter_jobs.py)
# ---------------------------------------------------------------------------

def parse_keywords(profile_path):
    with open(profile_path) as f:
        content = f.read()
    m = re.search(r'## Search Keywords\n(.*?)(?:\n##|$)', content, re.DOTALL | re.IGNORECASE)
    if not m:
        return ["data analyst"]
    return [
        line.lstrip("-• ").strip().lower()
        for line in m.group(1).splitlines()
        if line.strip() and not line.startswith("#")
    ] or ["data analyst"]


# ---------------------------------------------------------------------------
# Workday API fetch
# ---------------------------------------------------------------------------

def fetch_workday_company(company_name, tenant, wdn, site, keywords, timeout=20):
    """
    Fetches jobs from one Workday portal for all given keywords.
    Returns (company_name, list_of_job_dicts).
    """
    endpoint = f"https://{tenant}.wd{wdn}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    base_url = f"https://{tenant}.wd{wdn}.myworkdayjobs.com/en-US/{site}"

    seen_paths = set()
    results = []

    for keyword in keywords:
        payload = json.dumps({
            "appliedFacets": {},
            "limit": 20,
            "offset": 0,
            "searchText": keyword,
        }).encode("utf-8")

        req = Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 jobclaw/1.0",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 404:
                return company_name, []   # tenant/site combo not valid
            continue
        except Exception:
            continue

        for job in data.get("jobPostings", []):
            path = job.get("externalPath", "")
            if path in seen_paths:
                continue
            seen_paths.add(path)
            results.append({
                "title":       job.get("title", ""),
                "url":         base_url + path,
                "company":     company_name,
                "location":    job.get("locationsText", ""),
                "date_posted": job.get("postedOn", ""),   # e.g. "Posted 2 Days Ago"
                "description": "",
                "source":      "workday_api",
            })

    return company_name, results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch jobs from 26 Workday career portals"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--profile",  help="Path to candidate profile .md")
    group.add_argument("--keywords", help="Comma-separated keywords (overrides profile)")
    parser.add_argument("--output",  required=True, help="Output JSON path")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    if args.keywords:
        keywords = [k.strip().lower() for k in args.keywords.split(",") if k.strip()]
    else:
        keywords = parse_keywords(args.profile)

    print(f"Keywords  : {keywords}", file=sys.stderr)
    print(f"Companies : {len(WORKDAY_COMPANIES)}", file=sys.stderr)

    all_jobs = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(fetch_workday_company, name, tenant, wdn, site, keywords, args.timeout): name
            for name, tenant, wdn, site in WORKDAY_COMPANIES
        }
        for future in as_completed(futures):
            company_name, jobs = future.result()
            if jobs:
                print(f"  ✓ {company_name}: {len(jobs)} jobs", file=sys.stderr)
                all_jobs.extend(jobs)
            else:
                print(f"  ✗ {company_name}: 0 jobs", file=sys.stderr)

    # Deduplicate by URL
    seen, final = set(), []
    for j in all_jobs:
        if j["url"] not in seen:
            seen.add(j["url"])
            final.append(j)

    with open(args.output, "w") as f:
        json.dump(final, f, indent=2)

    print(f"\nTotal: {len(final)} Workday jobs → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
