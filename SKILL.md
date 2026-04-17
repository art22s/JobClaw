---
name: JobClaw
description: Automated job search pipeline — create candidate profiles from PDF resumes, aggregate jobs from Greenhouse/Lever/Workday APIs, filter by title/location/sponsorship, generate tiered HTML reports, and sync to Google Sheets. Use when a user wants to search for jobs, run a job search, create a candidate profile from a resume, find open positions, scan job boards, or run the job hunt pipeline for someone.
---

# JobClaw Pipeline

Automated job search: profile creation → multi-source aggregation → filtering → reports → sheet sync.

## Step 1: Create Profile

If the user provides a PDF resume (or asks to set up a new candidate), generate a profile:

1. **Read the PDF** — use the `read` tool or `exec` with: `python3 -c "import fitz; doc=fitz.open('PATH'); print('\\n'.join(p.get_text() for p in doc))"`

2. **Ask the user for:**
   - **Target location** (e.g., "United States (US only)", "Remote worldwide")
   - **Immigration status:** US Citizen/Green Card · Requires H1B/sponsorship · Other (OPT, H4-EAD, etc.)
   - **Email for job reports** (if not in resume)

3. **Generate the profile** — write a structured markdown file to `profiles/{firstname_lastname}.md` using this template:

```markdown
# {Full Name} - JobClaw Profile

## Contact
- **Name:** ...
- **Email:** ...
- **Phone:** ...
- **LinkedIn:** ...

## Email Recipients
- **Send results to:** ...
- **CC:** (none)

## Search Keywords
- {10–15 relevant job title keywords based on skills and experience}

## Target Locations
- {location}

## Experience Summary
{2–3 sentence overview}

## Key Skills
- **Category:** skills...

## Work History
1. **Company** — Title (Dates)
   - Achievement...
   - Achievement...

## Education
- **Degree** — University (Year)

## Job Preferences
- **Level:** ...
- **Type:** Full-time
- **Industries:** ...
- **Role focus:** ...
- **Visa:** {immigration status}

## Exclusions
### Company Exclusions (no sponsorship)
{companies known not to sponsor, if any}

### Text-Based Exclusions
- ❌ {exclusion rules based on immigration status}

## Notes
{2–3 relevant highlights}
```

**Search keyword generation tips:**
- Extract current/past titles and add senior/junior variants
- Include skill-adjacent roles (Power BI → "BI analyst", "BI developer")
- Include industry-specific titles if applicable
- Include 10–15 keywords total

**Sponsorship exclusion rules** — add to `### Text-Based Exclusions` based on immigration status:

If requires sponsorship:
- ❌ Skip jobs that explicitly state "no visa sponsorship"
- ❌ Skip jobs that say "must be authorized to work without sponsorship"
- ❌ Skip jobs that require US citizenship or security clearance
- ❌ Skip jobs that say "unable to sponsor" or "will not sponsor"
- ❌ Skip jobs that say "not provide immigration sponsorship" or "does not sponsor"
- ❌ Skip jobs requiring ITAR compliance or export-controlled work (US persons only)

If citizen/GC: no sponsorship exclusions, but still exclude ITAR/export-controlled if not a US person.

If the user already has a profile, skip to Step 2.

## Step 2: Fetch Greenhouse Jobs

```bash
cd {project_dir}
python3 scripts/fetch_greenhouse.py --output data/greenhouse_raw.json
```

Hits 100+ Greenhouse API endpoints in parallel (~5s). Companies include tech, finance, healthcare, and more. See `scripts/fetch_greenhouse.py` → `GREENHOUSE_COMPANIES` for the full list.

## Step 3: Fetch Lever Jobs

```bash
cd {project_dir}
python3 scripts/fetch_lever.py --output data/lever_raw.json --timeout 30
```

Hits Lever public Postings API for 35+ companies. Full descriptions included. Companies: Spotify, Binance, Plaid, Whoop, Octopus Energy, Dun & Bradstreet, Sword Health, Rover, Outreach, and more.

## Step 4: Fetch Workday Jobs

```bash
cd {project_dir}
python3 scripts/fetch_workday.py \
  --profile profiles/{name}.md \
  --output data/workday_raw.json
```

Hits 26+ verified Workday portals. Uses Playwright for rendering dynamic pages. Companies: NVIDIA, Salesforce, Adobe, Cisco, Intel, Visa, Mastercard, PayPal, Fidelity, Target, Walmart, Nike, Disney, Comcast, and more.

**Workday caching**: Pass `--cache {project_dir}/workday_cache.json` to skip already-rendered URLs on re-runs (saves ~9 min).

## Step 5: Web Search (Optional Supplement)

Use `web_search` to find jobs on Ashby, Wellfound, Workable, and Remotive. For each keyword from the profile:

- `site:jobs.ashbyhq.com "{keyword}" remote`
- `site:wellfound.com "{keyword}"`
- `site:jobs.workable.com "{keyword}" remote`

Collect results as JSON objects: `{"title", "url", "company", "location", "date_posted": "", "source": "websearch"}`

Save to `data/websearch_raw.json` using the `write` tool (not exec).

**Note**: Web search results without dates get dropped by the 7-day filter — this is expected. This step mainly discovers companies not in the main API lists.

## Step 6: Filter Jobs

```bash
cd {project_dir}
python3 scripts/filter_jobs.py \
  --greenhouse data/greenhouse_raw.json \
  --lever data/lever_raw.json \
  --workday data/workday_raw.json \
  --websearch data/websearch_raw.json \
  --profile profiles/{name}.md \
  --max-days 7 \
  --cache {project_dir}/workday_cache.json \
  --output data/filtered.json
```

Filtering logic:
- **Title match** — must contain a keyword from profile (or close variant)
- **Sponsorship** — rejects jobs with "no visa sponsorship" phrases
- **ITAR/security clearance** — excluded
- **Location** — US-only by default
- **Experience** — hard reject: VP/C-level, Director, Principal, 8+ years, PhD. Flag: Staff, Manager, 6–7 years
- **Recency** — 7-day max (override with `--max-days N`)
- **Per-company cap** — max 20 jobs per company (override with `--max-per-company N`)

**Rating:**
- ⭐⭐⭐ — Exact title match for top keyword
- ⭐⭐ — Close variant
- ⭐ — Partial match

## Step 7: Generate Report

```bash
cd {project_dir}
python3 scripts/generate_report.py \
  --input data/filtered.json \
  --profile profiles/{name}.md \
  --output reports/YYYY-MM-DD-{name}.html \
  --print-summary
```

Creates an HTML report grouped by tier (⭐⭐⭐ / ⭐⭐ / ⭐) with clickable apply links. `--print-summary` outputs markdown summary to conversation.

## Step 8: Sync to Google Sheet

Requires a Google Sheet ID and gog OAuth token.

**Configure sheet ID** (one of):
- `export JOBCLAW_SHEET_ID=your-sheet-id`
- `--sheet-id your-sheet-id` CLI arg
- Add `{"sheet_id": "your-sheet-id"}` to `config/config.json`

**Set up OAuth**:
```bash
gog auth login
gog auth tokens export --out config/gog_token.json
```

Then sync:
```bash
cd {project_dir}
python3 scripts/update_sheet.py --input data/filtered.json
```

Adds new jobs, removes rows no longer in filtered results. If no sheet configured, tell user to set up via `scripts/JobSearch.gs`.

## Step 9: Send Email (Optional)

Only if user asks, or if no Google Sheet is configured:

```bash
# Send using your preferred email method, or use the built-in OpenClaw email skill
  --to "{email}" \
  --cc "{cc_email}" \
  --subject "🦞 Job Hunt Results — $(date +%Y-%m-%d)" \
  --html-file "reports/$(date +%Y-%m-%d)-{name}.html"
```

## Quick Run (All Steps)

For cron or one-shot runs, use `cron_run.sh` (edit paths inside first) or `job_search.py`:

```bash
python3 job_search.py run --profile profiles/{name}.md
```

Or individual steps:
```bash
python3 job_search.py fetch --profile profiles/{name}.md --source all
python3 job_search.py filter --profile profiles/{name}.md
python3 job_search.py report --profile profiles/{name}.md
```

## Setup

1. Install dependencies: `pip install playwright pymupdf && playwright install chromium`
2. (Optional) Google Sheet sync: `gog auth login && gog auth tokens export --out config/gog_token.json`
3. Set `JOBCLAW_SHEET_ID` env var or add `sheet_id` to `config/config.json` for sheet sync
4. Create profile from resume (Step 1) or copy `profiles/example.md`
5. Run the pipeline

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `JOBCLAW_CONFIG_DIR` | `config/` | Path to config directory |
| `JOBCLAW_SHEET_ID` | _(none)_ | Google Sheet ID for sync |
| `GOG_CRED_PATH` | `~/.config/gogcli/credentials.json` | gog CLI credentials path |
