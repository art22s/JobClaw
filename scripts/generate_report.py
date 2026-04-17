#!/usr/bin/env python3
"""
generate_report.py — Converts filtered job JSON into an HTML report (tabular, email-ready)
and optionally a plain Markdown summary for terminal display.

Usage:
    python3 generate_report.py \
        --input data/filtered.json \
        --profile profiles/example.md \
        --output reports/YYYY-MM-DD-<name>.html

    # Also print a short markdown summary to stdout:
    python3 generate_report.py ... --print-summary
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from collections import defaultdict


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def get_candidate_name(profile_path):
    with open(profile_path) as f:
        content = f.read()
    m = re.search(r'\*\*Name:\*\*\s+(.+)', content)
    return m.group(1).strip() if m else "Candidate"


def humanise_date(date_str):
    """Convert ISO timestamp or relative date string to a human-friendly label."""
    if not date_str or date_str in ("Unknown", ""):
        return "Unknown"
    s = date_str.lower().strip()
    if any(x in s for x in ("today", "just now", "minutes ago", "hours ago")):
        return "Today"
    if any(x in s for x in ("yesterday", "1 day ago", "a day ago")):
        return "Yesterday"
    m = re.search(r'(\d+)\s+days?\s+ago', s)
    if m:
        return f"{m.group(1)}d ago"
    # ISO timestamp
    iso = re.match(r'(\d{4}-\d{2}-\d{2})', s)
    if iso:
        try:
            d = datetime.strptime(iso.group(1), "%Y-%m-%d")
            days = (datetime.now() - d).days
            if days == 0:   return "Today"
            if days == 1:   return "Yesterday"
            if days <= 30:  return f"{days}d ago"
            return d.strftime("%b %d, %Y")
        except ValueError:
            pass
    return date_str


TIER_LABELS  = {3: "⭐⭐⭐ Top Matches",  2: "⭐⭐ Good Matches",  1: "⭐ Partial Matches"}
TIER_COLOR   = {3: "#1a7f37",            2: "#9a6700",           1: "#57606a"}
TIER_BG      = {3: "#dafbe1",            2: "#fff8c5",           1: "#f6f8fa"}
TIER_BORDER  = {3: "#1a7f37",            2: "#d4a72c",           1: "#d0d7de"}


# --------------------------------------------------------------------------- #
# HTML report
# --------------------------------------------------------------------------- #

def generate_html(jobs, name, today):
    total = len(jobs)
    by_tier = defaultdict(list)
    for job in jobs:
        by_tier[len(job.get("rating", ""))].append(job)

    rows_html = ""
    for stars in [3, 2, 1]:
        tier_jobs = by_tier.get(stars, [])
        if not tier_jobs:
            continue
        label  = TIER_LABELS[stars]
        color  = TIER_COLOR[stars]
        bg     = TIER_BG[stars]
        border = TIER_BORDER[stars]
        rows_html += f"""
    <tr>
      <td colspan="4" style="background:{bg};color:{color};font-weight:700;font-size:13px;
          padding:9px 14px;border-top:2px solid {border};">
        {label} &nbsp;<span style="font-weight:400;font-size:12px;">({len(tier_jobs)} jobs)</span>
      </td>
    </tr>"""
        for job in tier_jobs:
            title   = job.get("title",    "")
            company = job.get("company",  "")
            loc     = job.get("location", "")
            posted  = humanise_date(job.get("date_posted", ""))
            url     = job.get("url",      "#")
            rows_html += f"""
    <tr style="border-bottom:1px solid #e8ebee;">
      <td style="padding:8px 14px;font-weight:600;">
        <a href="{url}" style="color:#0969da;text-decoration:none;">{title}</a>
      </td>
      <td style="padding:8px 14px;color:#24292f;">{company}</td>
      <td style="padding:8px 14px;color:#57606a;font-size:13px;">{loc}</td>
      <td style="padding:8px 14px;color:#57606a;font-size:13px;white-space:nowrap;">{posted}</td>
    </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Job Search Results — {name}</title>
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
             max-width:920px;margin:32px auto;color:#24292f;">

  <h2 style="margin-bottom:4px;">🦞 Job Search Results — {name}</h2>
  <p style="color:#57606a;margin-top:4px;font-size:14px;">
    <strong>Date:</strong> {today} &nbsp;|&nbsp;
    <strong>Portals:</strong> Greenhouse API, Ashby, Lever, Wellfound, Workable, RemoteFront &nbsp;|&nbsp;
    <strong>Total:</strong> {total} matching jobs
  </p>

  <table width="100%" cellpadding="0" cellspacing="0"
         style="border-collapse:collapse;border:1px solid #d0d7de;border-radius:6px;
                overflow:hidden;font-size:14px;">
    <thead>
      <tr style="background:#f6f8fa;">
        <th style="padding:10px 14px;text-align:left;border-bottom:1px solid #d0d7de;
                   font-weight:600;width:40%;">Role</th>
        <th style="padding:10px 14px;text-align:left;border-bottom:1px solid #d0d7de;
                   font-weight:600;width:20%;">Company</th>
        <th style="padding:10px 14px;text-align:left;border-bottom:1px solid #d0d7de;
                   font-weight:600;width:28%;">Location</th>
        <th style="padding:10px 14px;text-align:left;border-bottom:1px solid #d0d7de;
                   font-weight:600;width:12%;">Posted</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>

  <p style="font-size:12px;color:#8c959f;margin-top:20px;">
    Generated by jobclaw · OpenClaw ·
    All jobs have confirmed post dates · Filtered for US locations &amp; H1B-friendly
  </p>
</body>
</html>"""


# --------------------------------------------------------------------------- #
# Markdown summary (for terminal display)
# --------------------------------------------------------------------------- #

def generate_markdown_summary(jobs, name, today):
    total = len(jobs)
    by_tier = defaultdict(list)
    for job in jobs:
        by_tier[len(job.get("rating", ""))].append(job)

    lines = [
        f"# Job Search Results — {name}",
        f"**Date:** {today} | **Total:** {total} matching jobs",
        "",
    ]
    for stars in [3, 2, 1]:
        tier_jobs = by_tier.get(stars, [])
        if not tier_jobs:
            continue
        lines.append(f"## {TIER_LABELS[stars]}")
        lines.append("")
        for job in tier_jobs:
            title   = job.get("title",    "Unknown")
            company = job.get("company",  "Unknown")
            loc     = job.get("location", "")
            posted  = humanise_date(job.get("date_posted", ""))
            url     = job.get("url",      "#")
            lines += [
                f"### {title} — {company}",
                f"**Location:** {loc}  ",
                f"**Posted:** {posted}  ",
                f"**Apply:** {url}",
                "",
                "---",
                "",
            ]
    if not any(by_tier.get(s) for s in [3, 2, 1]):
        lines.append("_No matching jobs found._")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Generate HTML job report (email-ready)")
    parser.add_argument("--input",         required=True, help="Path to filtered jobs JSON")
    parser.add_argument("--profile",       required=True, help="Path to candidate profile .md")
    parser.add_argument("--output",        required=True, help="Path for output .html report")
    parser.add_argument("--print-summary", action="store_true",
                        help="Also print a Markdown summary to stdout")
    args = parser.parse_args()

    with open(args.input) as f:
        jobs = json.load(f)

    name  = get_candidate_name(args.profile)
    today = datetime.now().strftime("%B %d, %Y")

    html = generate_html(jobs, name, today)
    with open(args.output, "w") as f:
        f.write(html)
    print(f"HTML report saved → {args.output} ({len(jobs)} jobs)", file=sys.stderr)

    if args.print_summary:
        print(generate_markdown_summary(jobs, name, datetime.now().strftime("%Y-%m-%d")))


if __name__ == "__main__":
    main()
