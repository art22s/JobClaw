#!/usr/bin/env python3
"""
filter_jobs.py — Filters and ranks job listings from Greenhouse API + WebSearch results
based on a candidate's profile (keywords, location, visa exclusions).

Usage:
    python3 filter_jobs.py \
        --greenhouse data/greenhouse_raw.json \
        --websearch data/websearch_raw.json \
        --profile profiles/example.md \
        --output data/filtered.json
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.request import Request, urlopen


# --------------------------------------------------------------------------- #
# Profile parsing
# --------------------------------------------------------------------------- #

def parse_profile(profile_path):
    with open(profile_path) as f:
        content = f.read()

    def extract_section(heading):
        m = re.search(rf'## {re.escape(heading)}\n(.*?)(?:\n##|$)', content, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    # Keywords — one per line
    kw_section = extract_section("Search Keywords")
    keywords = [
        line.lstrip("-• ").strip().lower()
        for line in kw_section.splitlines()
        if line.strip() and not line.startswith("#")
    ]

    # Exclusions — extract quoted phrases or whole cleaned lines
    ex_section = extract_section("Exclusions")
    exclusions = []
    for line in ex_section.splitlines():
        cleaned = re.sub(r'[❌\-•]', '', line).strip()
        quotes = re.findall(r'"([^"]+)"', cleaned)
        if quotes:
            exclusions.extend(q.lower() for q in quotes)
        elif cleaned:
            # Remove leading filler phrases
            cleaned = re.sub(
                r'^(skip jobs that (explicitly state|say|require)|skip)\s*',
                '', cleaned, flags=re.IGNORECASE
            ).strip()
            if cleaned:
                exclusions.append(cleaned.lower())

    if not exclusions:
        exclusions = [
            "no visa sponsorship",
            "must be authorized to work without sponsorship",
            "unable to sponsor",
            "will not sponsor",
            "us citizenship required",
            "security clearance",
        ]

    # Location: check for "US only"
    loc_section = extract_section("Target Locations")
    us_only = "united states" in loc_section.lower() or "us only" in loc_section.lower()

    return keywords, exclusions, us_only


# --------------------------------------------------------------------------- #
# Location helpers
# --------------------------------------------------------------------------- #

FOREIGN_TERMS = [
    # Asia-Pacific
    'singapore', 'india', 'japan', 'china', 'australia', 'new zealand',
    'sydney', 'melbourne', 'bengaluru', 'bangalore', 'hyderabad', 'chennai', 'mumbai',
    'noida', 'pune', 'kolkata', 'gurugram', 'gurgaon',
    'tokyo', 'beijing', 'shanghai', 'hong kong', 'taiwan', 'manila',
    'philippines', 'jakarta', 'indonesia', 'vietnam', 'thailand', 'malaysia',
    'kuala lumpur', 'bangkok', 'ho chi minh', 'hanoi',
    'pakistan', 'bangladesh', 'sri lanka', 'nepal',
    'south korea', 'seoul', 'myanmar', 'cambodia',
    'sgp', 'ind', 'jpn',
    # Common Indian city aliases
    'new delhi', 'delhi', 'ahmedabad', 'kochi', 'coimbatore', 'mysore', 'nagpur', 'indore', 'thiruvananthapuram', 'trivandrum',
    # Europe
    'uk', 'united kingdom', 'england', 'scotland', 'wales', 'northern ireland',
    'london', 'manchester', 'edinburgh', 'birmingham', 'bristol', 'leeds',
    'france', 'paris', 'lyon', 'marseille',
    'germany', 'berlin', 'munich', 'hamburg', 'frankfurt', 'cologne',
    'spain', 'madrid', 'barcelona', 'zaragoza',
    'ireland', 'dublin',
    'netherlands', 'amsterdam', 'rotterdam',
    'sweden', 'stockholm', 'gothenburg',
    'norway', 'oslo',
    'denmark', 'copenhagen',
    'finland', 'helsinki',
    'austria', 'vienna',
    'switzerland', 'zurich', 'geneva', 'lausanne',
    'belgium', 'brussels',
    'italy', 'milan', 'rome',
    'portugal', 'lisbon', 'porto',
    'poland', 'warsaw', 'krakow',
    'czech republic', 'czechia', 'prague',
    'hungary', 'budapest',
    'romania', 'bucharest',
    'bulgaria', 'sofia',
    'greece', 'athens',
    'lithuania', 'vilnius',
    'latvia', 'riga',
    'estonia', 'tallinn',
    'croatia', 'zagreb',
    'slovenia', 'ljubljana',
    'serbia', 'belgrade',
    'ukraine', 'kyiv',
    'slovakia', 'bratislava',
    'iceland', 'reykjavik',
    'malta', 'valletta',
    'cyprus', 'nicosia',
    'luxembourg',
    # Middle East / Africa
    'israel', 'tel aviv',
    'uae', 'united arab emirates', 'dubai', 'abu dhabi',
    'saudi arabia', 'riyadh', 'jeddah',
    'egypt', 'cairo',
    'morocco', 'casablanca', 'rabat',
    'nigeria', 'lagos', 'abuja',
    'kenya', 'nairobi',
    'south africa', 'cape town', 'johannesburg',
    'ghana', 'accra',
    'ethiopia', 'addis ababa',
    'egy',
    # Regional indicators (non-US)
    'emea', 'apac', 'latam', 'apj',
    # Americas (non-US)
    'canada', 'toronto', 'montreal', 'vancouver', 'ottawa', 'calgary',
    'brazil', 'sao paulo', 'rio de janeiro', 'bra',
    'mexico', 'mexico city', 'guadalajara', 'monterrey',
    'costa rica', 'san jose, costa rica',
    'argentina', 'buenos aires',
    'chile', 'santiago',
    'colombia', 'bogota',
    'peru', 'lima',
    'ecuador', 'quito',
    'uruguay', 'montevideo',
    'panama', 'panama city',
    'guatemala',
    'el salvador',
    'honduras',
    # Central Asia
    'uzbekistan', 'tashkent',
    'kazakhstan', 'almaty',
    'georgia', 'tbilisi',
    'armenia', 'yerevan',
    'azerbaijan', 'baku',
]

# Compile into a single word-boundary regex for fast matching
_FOREIGN_RE = re.compile(
    r'\b(' + '|'.join(re.escape(t) for t in FOREIGN_TERMS) + r')\b',
    re.IGNORECASE,
)

US_STATES = {
    'al', 'ak', 'az', 'ar', 'ca', 'co', 'ct', 'de', 'fl', 'ga', 'hi', 'id',
    'il', 'in', 'ia', 'ks', 'ky', 'la', 'me', 'md', 'ma', 'mi', 'mn', 'ms',
    'mo', 'mt', 'ne', 'nv', 'nh', 'nj', 'nm', 'ny', 'nc', 'nd', 'oh', 'ok',
    'or', 'pa', 'ri', 'sc', 'sd', 'tn', 'tx', 'ut', 'vt', 'va', 'wa', 'wv',
    'wi', 'wy',
}

US_CITIES = [
    'cupertino', 'sunnyvale', 'san diego', 'santa clara', 'seattle', 'austin',
    'new york', 'san francisco', 'los angeles', 'boston', 'chicago', 'atlanta',
    'dallas', 'denver', 'miami', 'washington', 'boulder', 'raleigh', 'palo alto',
    'mountain view', 'san jose', 'redmond', 'bellevue', 'cambridge', 'new york city',
    'nyc', 'sf', 'bay area', 'silicon valley', 'philadelphia', 'portland',
    'minneapolis', 'nashville', 'salt lake city', 'phoenix', 'tucson', 'houston',
    'san antonio', 'charlotte', 'columbus', 'indianapolis', 'detroit', 'memphis',
    'beaverton', 'plano', 'irving', 'scottsdale', 'tempe', 'orlando', 'tampa',
    'st louis', 'kansas city', 'cincinnati', 'pittsburgh', 'cleveland',
    'richmond', 'baltimore', 'new haven', 'hartford', 'providence',
]


def is_us_location(location):
    """Return True if the location looks US-based, False if clearly foreign, None if unknown."""
    loc = location.lower()
    title_words = set(re.findall(r'\b\w+\b', loc))

    # Reject if any foreign term matches (word-boundary — won't fire on
    # "india" inside "indianapolis" or "san jose" inside "costa rica, san jose")
    if _FOREIGN_RE.search(loc):
        return False

    # Explicit US indicators
    if any(x in loc for x in ['united states', 'usa', 'u.s.', 'remote - us', 'remote (us)', 'us remote', 'anywhere in the us']):
        return True
    if any(state in title_words for state in US_STATES):
        return True
    if any(city in loc for city in US_CITIES):
        return True
    if 'remote' in loc and not loc.strip().startswith('remote'):
        return None

    # Bare "Remote" with nothing else — keep (benefit of the doubt)
    if loc.strip() in ('remote', 'remote worldwide', 'fully remote', 'distributed', 'anywhere'):
        return None

    return None  # Unknown — keep by default


# --------------------------------------------------------------------------- #
# Date helpers
# --------------------------------------------------------------------------- #

def days_ago(date_str):
    """Convert a date string to approximate days ago. Returns 999 if unknown."""
    if not date_str or date_str in ('Unknown', ''):
        return 999

    s = date_str.lower().strip()

    if any(x in s for x in ('today', 'just now', 'minutes ago', 'hours ago', '1 hour')):
        return 0
    if any(x in s for x in ('yesterday', '1 day ago', 'a day ago')):
        return 1

    m = re.search(r'(\d+)\s+days?\s+ago', s)
    if m:
        return int(m.group(1))

    if 'a month ago' in s or '30+ days' in s:
        return 30
    m = re.search(r'(\d+)\s+months?\s+ago', s)
    if m:
        return int(m.group(1)) * 30

    # ISO timestamp (Greenhouse returns e.g. "2026-03-15T10:00:00.000Z")
    m = re.match(r'(\d{4}-\d{2}-\d{2})', s)
    if m:
        try:
            d = datetime.strptime(m.group(1), '%Y-%m-%d')
            return max(0, (datetime.utcnow() - d).days)
        except ValueError:
            pass

    # "April 3, 2026" style
    clean = re.sub(r'(?<=\d)(st|nd|rd|th)', '', s)
    clean = re.sub(r'\s+', ' ', clean).strip()
    for fmt in ('%B %d, %Y', '%b %d, %Y', '%B %d %Y', '%b %d %Y', '%Y-%m-%d', '%m/%d/%Y'):
        try:
            d = datetime.strptime(clean, fmt)
            return max(0, (datetime.now() - d).days)
        except ValueError:
            continue

    return 999


# --------------------------------------------------------------------------- #
# Relevance rating
# --------------------------------------------------------------------------- #

# Tier definitions — evaluated against job title (lowercase)
TIER_3 = [
    "data analyst", "senior data analyst", "bi analyst",
    "business intelligence analyst", "sr. data analyst", "sr data analyst",
    "lead data analyst", "principal data analyst", "staff data analyst",
    "business intelligence engineer", "bi engineer",
    "bi developer", "business intelligence developer",
    # --- fuzzy synonyms ---
    "analyst, data", "analyst - data", "analyst (data",
    "data analytics analyst", "data analysis",
    "insights analyst", "reporting analyst", "decision analyst",
    "sr. analyst", "sr analyst", "senior analyst",
    "healthcare data analyst", "clinical data analyst",
    "marketing data analyst", "finance data analyst",
    "product data analyst", "revenue data analyst",
    "sales data analyst", "customer data analyst",
    "people data analyst", "hr data analyst",
    "operations data analyst", "supply chain data analyst",
    "data and analytics analyst", "data & analytics analyst",
    "bi & analytics analyst", "bi and analytics analyst",
    "analytics and data analyst", "analytics & data analyst",
    "measurement analyst", "metrics analyst",
    "dashboard analyst", "visualization analyst",
    "data specialist", "analytics specialist",
    "bi specialist",
]
TIER_2 = [
    "product analyst", "growth analyst", "business analyst",
    "analytics engineer", "data analytics", "reporting analyst",
    "marketing analyst", "operations analyst", "financial analyst",
    "quantitative analyst", "insights analyst",
    "ai and analytics", "data analytics consultant",
    "analytics consultant", "bi consultant",
    "intelligence analyst",
    # --- fuzzy synonyms ---
    "associate analyst", "junior analyst",
    "data & reporting analyst", "data and reporting analyst",
    "strategy analyst", "strategic analyst",
    "risk analyst", "compliance analyst",
    "pricing analyst", "pricing data analyst",
    "commercial analyst", "revenue analyst",
    "sales analyst", "marketing analytics",
    "customer analyst", "cx analyst",
    "workforce analyst", "people analyst",
    "data quality analyst", "data governance analyst",
    "data management analyst",
    "bi analyst", "bi & reporting analyst",
    "etl analyst", "data warehouse analyst",
    "data scientist analyst", "ml analyst",
    "decision support analyst",
    "performance analyst", "kpi analyst",
    "research analyst", "survey analyst",
    "analytics lead", "data analytics lead",
    "analytics manager", "data analytics manager",
    "bi lead", "bi manager",
]
TIER_1_KEYWORDS = ["analyst", "analytics", "intelligence", "reporting", "bi ", "insights", "data ", " dashboards", "visualization", "measurement", "metrics", " tableau ", " power bi"]

SKILL_BOOSTS = ["power bi", "tableau", "sql", "python", "alteryx", "thoughtspot", "dax",
                 "powerquery", "power query", "excel", "tableau desktop", "tableau prep",
                 "m code"]

# Experience thresholds from profile
MAX_YEARS_EXPERIENCE = 5  # Candidate has ~5 years of relevant experience
SENIOR_MIN_YEARS = 3       # "Senior" titles typically need 3+
STAFF_MIN_YEARS = 8        # "Staff" titles typically need 8+
PRINCIPAL_MIN_YEARS = 10   # "Principal" titles typically need 10+
MANAGER_MIN_YEARS = 7     # "Manager" titles typically need 7+
DIRECTOR_MIN_YEARS = 10   # "Director" titles typically need 10+
VP_MIN_YEARS = 12          # "VP" titles typically need 12+


def extract_experience_years(text):
    """Extract minimum years of experience required from job description."""
    if not text:
        return None
    import re
    # Common patterns: "5+ years", "5+ years of experience", "minimum 5 years"
    patterns = [
        r'(?:minimum|min\.)?\s*(\d+)\+?\s*(?:\+|plus)?\s*years?\s+(?:of\s+)?experience',
        r'(\d+)\+?\s*(?:\+|plus)?\s*years?\s+(?:of\s+)?(?:professional|relevant|related|industry|work)',
        r'experience[^.]{0,30}(\d+)\+?\s*years',
        r'(?:at least|minimum of)\s+(\d+)\+?\s*years',
    ]
    years_found = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            years_found.append(int(m.group(1)))
    if years_found:
        return max(years_found)  # Take the highest requirement mentioned
    return None


def extract_education_requirement(text):
    """Check if job requires a PhD or higher that the candidate doesn't have."""
    if not text:
        return None
    tl = text.lower()
    if 'phd' in tl or 'ph.d' in tl or 'doctorate' in tl:
        return 'phd'
    return None


def title_exceeds_experience(title):
    """Check if the seniority level in the title exceeds the candidate's experience."""
    tl = title.lower()
    # These titles suggest levels beyond the candidate's experience
    if any(w in tl for w in ['vp ', 'vice president', 'head of', 'chief']):
        return 'executive'
    if any(w in tl for w in ['director', 'sr. director', 'senior director']):
        return 'director'
    if 'principal' in tl and 'principal' not in ['principal data analyst']:  # principal data analyst is borderline
        return 'principal'
    if 'staff' in tl:
        return 'staff'
    # Manager of analysts (not just "manager" in title like "product manager")
    if re.search(r'manager[^/]*?(?:analytics|analyst|bi |data|intelligence|reporting)', tl):
        return 'manager'
    return None


def check_experience_match(title, description_text):
    """
    Returns (ok, reason). ok=False means the job is likely overqualified.
    reason is a human-readable explanation.
    """
    # Check title-level seniority
    title_level = title_exceeds_experience(title)
    if title_level == 'executive':
        return False, 'VP/C-level title'
    if title_level == 'director':
        return False, 'Director-level title'
    if title_level == 'principal':
        return False, 'Principal-level title'

    # Check description for explicit year requirements
    years_req = extract_experience_years(description_text)
    if years_req and years_req > 8:
        return False, f'Requires {years_req}+ years'

    # Check for PhD requirement
    edu = extract_education_requirement(description_text)
    if edu == 'phd':
        return False, 'Requires PhD'

    # Seniority warnings (don't skip, but flag)
    if title_level == 'staff':
        return True, 'Staff-level (borderline)'
    if title_level == 'manager':
        return True, 'Manager-level (borderline)'
    if years_req and years_req >= STAFF_MIN_YEARS:
        # 8+ years is a hard reject — clearly overqualified for 5-year candidate
        return False, f'Requires {years_req}+ years'
    if years_req and years_req > MAX_YEARS_EXPERIENCE:
        return True, f'Requires {years_req}+ years (borderline)'

    return True, None


def rate_job(title, description, keywords):
    tl = title.lower()
    dl = description.lower() if description else ""

    # Exact / near-exact match → 3 stars
    if any(t in tl for t in TIER_3):
        rating = 3
    # Profile keywords (from profile file) → 3 stars
    elif any(kw in tl for kw in keywords if len(kw) > 4):
        rating = 3
    # Close variants → 2 stars
    elif any(t in tl for t in TIER_2):
        rating = 2
    # Partial match → 1 star
    elif any(kw in tl for kw in TIER_1_KEYWORDS):
        rating = 1
    else:
        rating = 0

    if rating > 0 and dl:
        boost_count = sum(1 for s in SKILL_BOOSTS if s in dl)
        if boost_count >= 2:
            rating = min(rating + 1, 3)

    return rating


# --------------------------------------------------------------------------- #
# Workday description enrichment
# --------------------------------------------------------------------------- #

class _TextExtractor(HTMLParser):
    """Strip HTML tags and return plain text."""
    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self):
        return ' '.join(self._parts)


def fetch_workday_description(url, timeout=10):
    """Fetch a Workday job page and return raw HTML (used only for keyword matching).
    NOTE: Workday pages are JS-rendered; use fetch_workday_rendered() instead."""
    try:
        req = Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html',
        })
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', errors='replace')
    except Exception:
        return ""


def fetch_workday_rendered(urls, timeout=15000, max_pages=0, max_workers=4):
    """Fetch rendered page text for multiple Workday URLs using Playwright (headless Chrome).
    Returns a dict mapping url -> rendered text (or empty string on failure).
    Renders Workday job pages via headless Playwright to get full JS-rendered descriptions.
    Set max_pages=0 to render all (no cap). Caps at max_pages to keep runtime reasonable for cron jobs.
    Uses async Playwright with concurrent pages for speed.
    """
    try:
        from playwright.async_api import async_playwright
        import asyncio
    except ImportError:
        print("  Playwright not installed; falling back to HTTP-only Workday fetch", file=sys.stderr)
        return {url: fetch_workday_description(url) for url in urls}

    # Cap pages to render
    if max_pages > 0 and len(urls) > max_pages:
        print(f"  Capping Workday renders from {len(urls)} to {max_pages} (by order)", file=sys.stderr)
        urls = urls[:max_pages]

    results = {}
    async def _render_page(context, url, sem):
        async with sem:
            page = None
            try:
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=timeout)
                text = await page.inner_text("body")
                return url, f"<html><body><pre>{text}</pre></body></html>"
            except Exception as e:
                return url, ""
            finally:
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass

    async def _run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            )
            sem = asyncio.Semaphore(max_workers)
            tasks = [_render_page(context, url, sem) for url in urls]
            completed = 0
            for coro in asyncio.as_completed(tasks):
                url, text = await coro
                results[url] = text
                completed += 1
                if completed % 25 == 0 or completed == len(urls):
                    print(f"  Rendered {completed}/{len(urls)} Workday pages", file=sys.stderr)
            await context.close()
            await browser.close()

    print(f"  Rendering {len(urls)} Workday pages via Playwright ({max_workers} concurrent)...", file=sys.stderr)
    asyncio.run(_run())
    return results


def fetch_greenhouse_description(slug, job_id, timeout=10):
    """Fetch a single Greenhouse job's full description via /v1/boards/{slug}/jobs/{id}."""
    try:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 jobclaw/1.0"})
        with urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
            return data.get('content', '')  # HTML content
    except Exception:
        return ""


def _extract_text_from_html(html):
    """Strip HTML tags for plain-text keyword matching."""
    if not html:
        return ""
    extractor = _TextExtractor()
    extractor.feed(html)
    return extractor.get_text().lower()


# --------------------------------------------------------------------------- #
# Main filtering logic
# --------------------------------------------------------------------------- #

def filter_jobs(greenhouse_path, websearch_path, profile_path, output_path, max_days=30, max_per_company=20, workday_path=None, lever_path=None, cache_path=None):
    keywords, exclusions, us_only = parse_profile(profile_path)
    print(f"Profile keywords : {keywords}", file=sys.stderr)
    print(f"Exclusions       : {exclusions}", file=sys.stderr)
    print(f"US only          : {us_only}", file=sys.stderr)

    all_jobs = []

    # Load Greenhouse jobs
    if greenhouse_path:
        try:
            with open(greenhouse_path) as f:
                all_jobs.extend(json.load(f))
        except Exception as e:
            print(f"Warning: could not load greenhouse file — {e}", file=sys.stderr)

    # Load Workday jobs
    if workday_path:
        try:
            with open(workday_path) as f:
                all_jobs.extend(json.load(f))
        except Exception as e:
            print(f"Warning: could not load workday file — {e}", file=sys.stderr)

    # Load Lever jobs
    if lever_path:
        try:
            with open(lever_path) as f:
                all_jobs.extend(json.load(f))
        except Exception as e:
            print(f"Warning: could not load lever file — {e}", file=sys.stderr)

    # Load WebSearch jobs
    if websearch_path:
        try:
            with open(websearch_path) as f:
                all_jobs.extend(json.load(f))
        except Exception as e:
            print(f"Warning: could not load websearch file — {e}", file=sys.stderr)

    print(f"\nTotal input jobs: {len(all_jobs)}", file=sys.stderr)

    seen_urls = set()
    pre_candidates = []

    # --- Phase 1: title / location / recency (no network) ---
    for job in all_jobs:
        title = job.get("title", "")
        url = job.get("url", "")
        location = job.get("location", "")

        if url in seen_urls:
            continue
        seen_urls.add(url)

        # 1. Title relevance
        if rate_job(title, "", keywords) == 0:
            continue

        # 2. Location check
        if us_only:
            us = is_us_location(location)
            if us is False:
                print(f"  SKIP (location)  {title} — {location}", file=sys.stderr)
                continue

        # 3. Recency
        age = days_ago(job.get("date_posted", ""))
        if age > max_days:
            print(f"  SKIP (stale/no-date) {title} — {age}d ago", file=sys.stderr)
            continue

        job["_age_days"] = age
        pre_candidates.append(job)

    print(f"Pre-filter candidates: {len(pre_candidates)}", file=sys.stderr)

    # --- Phase 2: enrich descriptions in parallel (Greenhouse + Workday) ---
    gh_to_enrich = [
        j for j in pre_candidates
        if j.get("source") == "greenhouse_api" and not j.get("description") and j.get("slug") and j.get("url")
    ]
    # Extract Greenhouse job IDs from URLs (gh_jid= param or path segment)
    for j in gh_to_enrich:
        m = re.search(r'gh_jid=(\d+)', j.get("url", ""))
        if not m:
            m = re.search(r'/(\d{6,})', j.get("url", ""))
        j["_gh_job_id"] = m.group(1) if m else ""
    gh_to_enrich = [j for j in gh_to_enrich if j["_gh_job_id"]]

    wd_to_enrich = [
        j for j in pre_candidates
        if j.get("source") == "workday_api" and not j.get("description")
    ]

    # Greenhouse: parallel HTTP fetch (API returns JSON, no JS rendering needed)
    gh_enrich_count = len(gh_to_enrich)
    if gh_enrich_count:
        print(f"Fetching descriptions for {gh_enrich_count} Greenhouse jobs...", file=sys.stderr)
        gh_done = 0
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = {}
            for j in gh_to_enrich:
                futures[pool.submit(fetch_greenhouse_description, j["slug"], j["_gh_job_id"])] = j
            for future in as_completed(futures):
                html = future.result()
                futures[future]["description"] = html
                gh_done += 1
                if gh_done % 25 == 0 or gh_done == gh_enrich_count:
                    print(f"  {gh_done}/{gh_enrich_count} Greenhouse descriptions fetched", file=sys.stderr)

    # Load URL cache for incremental Workday rendering
    url_cache = {}
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path) as cf:
                url_cache = json.load(cf)
            print(f"  Loaded {len(url_cache)} cached Workday descriptions", file=sys.stderr)
        except Exception as e:
            print(f"  ⚠ Cache read error: {e}", file=sys.stderr)

    # Workday: batch render via Playwright (pages require JS rendering)
    if wd_to_enrich:
        wd_urls = [j["url"] for j in wd_to_enrich]
        url_to_job = {j["url"]: j for j in wd_to_enrich}

        # Separate cached vs new URLs
        cached_urls = [u for u in wd_urls if u in url_cache]
        new_urls = [u for u in wd_urls if u not in url_cache]
        print(f"  Workday: {len(cached_urls)} cached, {len(new_urls)} to render", file=sys.stderr)

        # Apply cached descriptions
        for url in cached_urls:
            if url in url_to_job:
                url_to_job[url]["description"] = url_cache[url]

        # Render only new URLs
        rendered = {}
        if new_urls:
            rendered = fetch_workday_rendered(new_urls)
            for url, desc in rendered.items():
                if url in url_to_job:
                    url_to_job[url]["description"] = desc
                url_cache[url] = desc  # Update cache

        # Save updated cache
        if cache_path and rendered:
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True) if os.path.dirname(cache_path) else None
                with open(cache_path, "w") as cf:
                    json.dump(url_cache, cf)
                print(f"  Saved {len(url_cache)} Workday URLs to cache", file=sys.stderr)
            except Exception as e:
                print(f"  ⚠ Cache write error: {e}", file=sys.stderr)

    # --- Phase 3: description-based filtering + rating ---
    # Extended exclusion phrases that appear in job descriptions
    DESC_EXCLUSIONS = exclusions + [
        "not provide immigration sponsorship",
        "does not sponsor",
        "cannot sponsor",
        "no immigration sponsorship",
        "without sponsorship",
        "not offer sponsorship",
        "unsponsored",
        "must have permanent authorization",
        "does not provide visa sponsorship",
        "not eligible for visa sponsorship",
        "no visa support",
        "will not provide visa sponsorship",
        "not sponsor visas",
        "unable to provide visa sponsorship",
        "does not offer immigration support",
        "does not provide immigration support",
        "does not sponsor employment visas",
        "u.s. citizen or national",
        "must be a u.s. citizen",
        "must be a us citizen",
        "lawful permanent resident of the u.s.",
        "lawful permanent resident (aka green card holder)",
        "itar regulations",
        "export regulations... must be a",
    ]

    sponsorship_keywords = [
        "sponsor", "visa sponsorship", "h1b", "h-1b", "opt", "cpt",
        "immigration", "work authorization",
    ]

    filtered = []
    for job in pre_candidates:
        title = job.get("title", "")
        description = job.get("description", "")
        title_l = title.lower()
        desc_text = _extract_text_from_html(description)

        # Check exclusions against title + description
        disqualified = False
        for ex in DESC_EXCLUSIONS:
            if ex in title_l or ex in desc_text:
                print(f"  SKIP (exclusion: {ex}) {title} — {job.get('company', '')}", file=sys.stderr)
                disqualified = True
                break
        if disqualified:
            continue

        # Experience level check (skip jobs clearly above the candidate's level)
        exp_ok, exp_reason = check_experience_match(title, desc_text)
        if not exp_ok:
            print(f"  SKIP (overqualified: {exp_reason}) {title} — {job.get('company', '')}", file=sys.stderr)
            continue

        rating = rate_job(title, desc_text, keywords)
        job["rating"] = "⭐" * rating

        # Mark if description mentions sponsorship positively
        job["_sponsors"] = any(s in desc_text for s in sponsorship_keywords)

        # Flag borderline experience matches
        if exp_reason:
            job["_exp_note"] = exp_reason

        # Don't store noisy HTML in output
        if job.get("source") in ("workday_api", "greenhouse_api") and description:
            job["description"] = ""

        # Clean temp keys
        job.pop("_gh_job_id", None)
        job.pop("slug", None)

        filtered.append(job)

    print(f"After filtering: {len(filtered)} jobs", file=sys.stderr)

    # Sort: newest first, then by rating (desc)
    filtered.sort(key=lambda j: (j["_age_days"], -len(j.get("rating", ""))))

    # Cap per company
    company_counts = {}
    final = []
    for job in filtered:
        company = job.get("company", "Unknown")
        company_counts[company] = company_counts.get(company, 0) + 1
        if company_counts[company] <= max_per_company:
            final.append(job)

    # --- Phase 4: final non-US sweep (catch anything that slipped through) ---
    definitely_foreign = []
    for job in final:
        loc = job.get("location", "").lower()
        if _FOREIGN_RE.search(loc):
            definitely_foreign.append(job)
    for job in definitely_foreign:
        final.remove(job)
        print(f"  REMOVED (non-US)  {job.get('title','')} — {job.get('location','')}", file=sys.stderr)
    if definitely_foreign:
        print(f"Post-filter non-US removal: {len(definitely_foreign)} jobs", file=sys.stderr)

    # Clean up temp keys
    for job in final:
        job.pop("_age_days", None)
        job.pop("_sponsors", None)
        job.pop("_exp_note", None)

    print(f"Final jobs (after per-company cap): {len(final)}", file=sys.stderr)

    with open(output_path, "w") as f:
        json.dump(final, f, indent=2)

    print(f"Saved to {output_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Filter job listings by profile rules")
    parser.add_argument("--greenhouse", help="Path to raw Greenhouse JSON")
    parser.add_argument("--workday",    help="Path to raw Workday JSON")
    parser.add_argument("--lever",     help="Path to raw Lever JSON")
    parser.add_argument("--websearch",  help="Path to raw WebSearch JSON")
    parser.add_argument("--profile",    required=True, help="Path to candidate profile .md")
    parser.add_argument("--output",     required=True, help="Path for filtered output JSON")
    parser.add_argument("--max-days",   type=int, default=7, help="Max age in days (default 7)")
    parser.add_argument("--max-per-company", type=int, default=20, help="Max jobs per company")
    parser.add_argument("--cache", help="Path to URL cache JSON (for incremental Workday rendering)")
    args = parser.parse_args()

    filter_jobs(
        args.greenhouse,
        args.websearch,
        args.profile,
        args.output,
        max_days=args.max_days,
        max_per_company=args.max_per_company,
        workday_path=args.workday,
        lever_path=args.lever,
        cache_path=args.cache,
    )


if __name__ == "__main__":
    main()
