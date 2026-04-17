#!/usr/bin/env python3
"""
job_search.py — CLI entry point for the job-search pipeline.

Usage:
    # Full pipeline (all sources)
    python3 job_search.py run --profile profiles/example.md

    # Fetch only (no filter/report)
    python3 job_search.py fetch --profile profiles/example.md --source greenhouse

    # Filter previously fetched data
    python3 job_search.py filter --profile profiles/example.md

    # Generate report from filtered data
    python3 job_search.py report --profile profiles/example.md

    # Sync to Google Sheet (requires gog CLI auth)
    python3 job_search.py sheet --input /tmp/js3_filtered.json
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent / "scripts"
CONFIG_DIR = Path(__file__).parent / "config"
DEFAULT_TMP = "/tmp"


def _run_script(script_name, args, timeout=600):
    """Run a script from the scripts/ directory."""
    script_path = SCRIPT_DIR / script_name
    cmd = [sys.executable, str(script_path)] + args
    print(f"  → {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, timeout=timeout, capture_output=False)
    return result.returncode


def cmd_fetch(args):
    """Fetch jobs from specified sources."""
    profile = args.profile
    tmp_dir = args.tmp_dir
    source = args.source
    timeout = args.timeout

    if source in ("greenhouse", "all"):
        print("[fetch] Greenhouse...", file=sys.stderr)
        rc = _run_script("fetch_greenhouse.py", [
            "--output", f"{tmp_dir}/js3_greenhouse_raw.json",
            "--timeout", str(timeout),
        ])
        if rc != 0 and source != "all":
            return rc

    if source in ("lever", "all"):
        print("[fetch] Lever...", file=sys.stderr)
        rc = _run_script("fetch_lever.py", [
            "--output", f"{tmp_dir}/js3_lever_raw.json",
            "--timeout", str(min(timeout, 30)),
        ])
        if rc != 0 and source != "all":
            return rc

    if source in ("workday", "all"):
        print("[fetch] Workday...", file=sys.stderr)
        rc = _run_script("fetch_workday.py", [
            "--profile", profile,
            "--output", f"{tmp_dir}/js3_workday_raw.json",
        ])
        if rc != 0 and source != "all":
            return rc

    if source in ("websearch", "all"):
        print("[fetch] Websearch...", file=sys.stderr)
        rc = _run_script("fetch_websearch.py", [
            "--output", f"{tmp_dir}/js3_websearch_raw.json",
        ])
        if rc != 0 and source != "all":
            return rc

    return 0


def cmd_filter(args):
    """Filter fetched jobs against a candidate profile."""
    tmp_dir = args.tmp_dir
    filter_args = [
        "--profile", args.profile,
        "--output", f"{tmp_dir}/js3_filtered.json",
        "--max-days", str(args.max_days),
        "--max-per-company", str(args.max_per_company),
        "--cache", f"{tmp_dir}/js3_workday_cache.json",
    ]

    # Add source files if they exist
    for source, flag in [
        ("greenhouse", "--greenhouse"),
        ("lever", "--lever"),
        ("workday", "--workday"),
        ("websearch", "--websearch"),
    ]:
        path = f"{tmp_dir}/js3_{source}_raw.json"
        if os.path.exists(path):
            filter_args.extend([flag, path])

    return _run_script("filter_jobs.py", filter_args, timeout=600)


def cmd_report(args):
    """Generate HTML report from filtered jobs."""
    tmp_dir = args.tmp_dir
    name = Path(args.profile).stem
    today = datetime.now().strftime("%Y-%m-%d")
    output = f"{Path(__file__).parent / 'reports' / today}-{name}.html"

    return _run_script("generate_report.py", [
        "--input", f"{tmp_dir}/js3_filtered.json",
        "--profile", args.profile,
        "--output", output,
    ])


def cmd_sheet(args):
    """Sync filtered jobs to Google Sheet (requires gog CLI auth)."""
    input_path = args.input or f"{DEFAULT_TMP}/js3_filtered.json"
    return _run_script("update_sheet.py", ["--input", input_path])


def cmd_run(args):
    """Run the full pipeline: fetch → filter → report → sheet (optional)."""
    start = time.time()
    profile = args.profile
    tmp_dir = args.tmp_dir

    print(f"=== Job Search Pipeline — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===", file=sys.stderr)

    # 1. Fetch all sources
    fetch_args = argparse.Namespace(
        profile=profile, source="all", tmp_dir=tmp_dir, timeout=args.timeout
    )
    rc = cmd_fetch(fetch_args)
    if rc != 0:
        print(f"⚠ Fetch step had errors (rc={rc}), continuing...", file=sys.stderr)

    # 2. Filter
    filter_args = argparse.Namespace(
        profile=profile, tmp_dir=tmp_dir,
        max_days=args.max_days, max_per_company=args.max_per_company,
    )
    rc = cmd_filter(filter_args)

    # 3. Report
    report_args = argparse.Namespace(profile=profile, tmp_dir=tmp_dir)
    rc = cmd_report(report_args)

    # 4. Sheet sync (optional)
    if args.no_sheet:
        print("[sheet] Skipped (--no-sheet)", file=sys.stderr)
    else:
        sheet_args = argparse.Namespace(input=None)
        rc = cmd_sheet(sheet_args)

    elapsed = time.time() - start
    print(f"=== Pipeline complete ({elapsed:.0f}s) ===", file=sys.stderr)
    return rc


def main():
    parser = argparse.ArgumentParser(
        description="Job Search — Multi-source job pipeline for data/analytics roles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full pipeline with Jane Doe's profile
  python3 job_search.py run --profile profiles/example.md

  # Fetch only Greenhouse jobs
  python3 job_search.py fetch --profile profiles/example.md --source greenhouse

  # Filter and report (skip sheet sync)
  python3 job_search.py run --profile profiles/example.md --no-sheet

  # Custom max age (60 days) and higher per-company cap
  python3 job_search.py filter --profile profiles/example.md --max-days 60 --max-per-company 30
""",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- run ---
    p_run = subparsers.add_parser("run", help="Full pipeline: fetch → filter → report → sheet")
    p_run.add_argument("--profile", required=True, help="Path to candidate profile .md")
    p_run.add_argument("--tmp-dir", default=DEFAULT_TMP, help="Temp directory for intermediate files")
    p_run.add_argument("--timeout", type=int, default=30, help="Per-request timeout (seconds)")
    p_run.add_argument("--max-days", type=int, default=30, help="Max job age in days")
    p_run.add_argument("--max-per-company", type=int, default=20, help="Max jobs per company")
    p_run.add_argument("--no-sheet", action="store_true", help="Skip Google Sheet sync")

    # --- fetch ---
    p_fetch = subparsers.add_parser("fetch", help="Fetch jobs from ATS sources")
    p_fetch.add_argument("--profile", required=True, help="Path to candidate profile .md")
    p_fetch.add_argument("--source", choices=["greenhouse", "lever", "workday", "websearch", "all"],
                         default="all", help="Source to fetch (default: all)")
    p_fetch.add_argument("--tmp-dir", default=DEFAULT_TMP, help="Temp directory")
    p_fetch.add_argument("--timeout", type=int, default=30, help="Per-request timeout (seconds)")

    # --- filter ---
    p_filter = subparsers.add_parser("filter", help="Filter fetched jobs by profile")
    p_filter.add_argument("--profile", required=True, help="Path to candidate profile .md")
    p_filter.add_argument("--tmp-dir", default=DEFAULT_TMP, help="Temp directory")
    p_filter.add_argument("--max-days", type=int, default=30, help="Max job age in days")
    p_filter.add_argument("--max-per-company", type=int, default=20, help="Max jobs per company")

    # --- report ---
    p_report = subparsers.add_parser("report", help="Generate HTML report")
    p_report.add_argument("--profile", required=True, help="Path to candidate profile .md")
    p_report.add_argument("--tmp-dir", default=DEFAULT_TMP, help="Temp directory")

    # --- sheet ---
    p_sheet = subparsers.add_parser("sheet", help="Sync to Google Sheet (requires gog auth)")
    p_sheet.add_argument("--input", help="Path to filtered JSON (default: /tmp/js3_filtered.json)")

    args = parser.parse_args()

    commands = {
        "run": cmd_run,
        "fetch": cmd_fetch,
        "filter": cmd_filter,
        "report": cmd_report,
        "sheet": cmd_sheet,
    }

    sys.exit(commands[args.command](args))


if __name__ == "__main__":
    main()
