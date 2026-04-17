#!/usr/bin/env python3
"""
update_sheet.py — Syncs filtered jobs to Google Sheet via Sheets API v4.

Does a hard sync: keeps rows whose Apply URL is in the current batch,
prunes rows no longer present, and appends new ones.

Uses OAuth2 credentials from gog CLI config to get an access token.

Usage:
    python3 scripts/update_sheet.py --input data/filtered.json
    python3 scripts/update_sheet.py --clear
"""

import argparse
import json
import os
import re
import sys
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

CONFIG_DIR = os.environ.get("JOBCLAW_CONFIG_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config"))
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
GOG_CRED_PATH = os.environ.get("GOG_CRED_PATH", os.path.expanduser("~/.config/gogcli/credentials.json"))
GOG_TOKEN_PATH = os.path.join(CONFIG_DIR, "gog_token.json")

SS_ID = os.environ.get("JOBCLAW_SHEET_ID", "")
SHEET_NAME = "Jobs"
NCOLS = 8

# Column indices (1-based)
COL_RATING  = 1
COL_TITLE   = 2
COL_COMPANY = 3
COL_LOCATION= 4
COL_POSTED  = 5
COL_SOURCE  = 6
COL_URL     = 7
COL_SEEN    = 8


# ---------------------------------------------------------------------------
# OAuth2 token helpers
# ---------------------------------------------------------------------------

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def get_access_token():
    """Get a fresh OAuth2 access token using gog's stored refresh token."""
    with open(GOG_CRED_PATH) as f:
        creds = json.load(f)

    # Find the gog token
    token_path = GOG_TOKEN_PATH
    if not os.path.exists(token_path):
        # Fall back to finding any exported token
        import glob
        candidates = glob.glob(os.path.join(CONFIG_DIR, "gog_token*.json"))
        if candidates:
            token_path = candidates[0]
        else:
            print(f"No gog token found. Export one with: gog auth tokens export ... --out {GOG_TOKEN_PATH}", file=sys.stderr)
            sys.exit(1)

    with open(token_path) as f:
        token_data = json.load(f)

    data = urlencode({
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": token_data["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()

    req = Request("https://oauth2.googleapis.com/token", data=data)
    with urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        return result["access_token"]


# ---------------------------------------------------------------------------
# Sheets API helpers
# ---------------------------------------------------------------------------

def sheets_api(method, path, token, body=None):
    """Call the Google Sheets API v4."""
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SS_ID}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode("utf-8") if body else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"Sheets API error HTTP {e.code}: {err_body[:400]}", file=sys.stderr)
        raise


def read_sheet_values(token, range_str):
    """Read values from a range."""
    path = f"/values/{range_str.replace(' ', '%20')}?valueRenderOption=FORMULA"
    result = sheets_api("GET", path, token)
    return result.get("values", [])


def write_sheet_values(token, range_str, values, value_input_option="USER_ENTERED"):
    """Write values to a range."""
    path = f"/values/{range_str.replace(' ', '%20')}?valueInputOption={value_input_option}"
    body = {"values": values}
    return sheets_api("PUT", path, token, body)


def clear_sheet_values(token, range_str):
    """Clear values in a range."""
    path = f"/values/{range_str.replace(' ', '%20')}:clear"
    return sheets_api("POST", path, token)


def batch_update(token, requests):
    """Send a batchUpdate to the spreadsheet."""
    return sheets_api("POST", ":batchUpdate", token, {"requests": requests})


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def parse_hyperlink_url(formula):
    """Extract the URL from a =HYPERLINK(\"url\",\"text\") formula."""
    if not formula:
        return ""
    match = re.match(r'=HYPERLINK\("([^"]*)"', formula)
    if match:
        return match[1]
    # If it's a plain URL, return as-is
    if formula.startswith("http"):
        return formula
    return ""


def sync_jobs(token, incoming_jobs):
    """Hard sync: prune stale rows, append new ones."""
    range_str = f"{SHEET_NAME}!A1:H"
    all_values = read_sheet_values(token, range_str)

    # Build incoming URL set
    incoming_urls = {}
    for job in incoming_jobs:
        if job.get("url"):
            incoming_urls[job["url"]] = job

    # Parse existing rows
    existing_urls = {}
    rows_to_keep = []

    if len(all_values) > 1:
        for i, row in enumerate(all_values[1:]):  # skip header
            # Get the actual URL - could be a HYPERLINK formula or raw URL
            raw = row[COL_URL - 1] if len(row) >= COL_URL else ""
            url = parse_hyperlink_url(raw)
            if not url and raw.startswith("http"):
                url = raw
            if not url or url == "Apply":
                url = ""

            if url and url in incoming_urls:
                rows_to_keep.append(row)
                existing_urls[url] = True
            elif not url:
                # No URL — keep it (manual note)
                rows_to_keep.append(row)
            # else: URL not in incoming → prune

    # Build new rows
    from datetime import date
    today_str = str(date.today())
    new_rows = []

    for job in incoming_jobs:
        url = job.get("url", "")
        if not url or url in existing_urls:
            continue
        new_rows.append([
            job.get("rating", ""),
            job.get("title", ""),
            job.get("company", ""),
            job.get("location", ""),
            job.get("date_posted", ""),
            job.get("source", ""),
            url,  # Store raw URL — we'll add HYPERLINK formatting after
            today_str,
        ])

    # Sort: 3 stars first, then 2, then 1. Within each rating, oldest posted first.
    rating_order = {"⭐⭐⭐": 0, "⭐⭐": 1, "⭐": 2}

    data_rows = rows_to_keep + new_rows

    # Sort by rating asc (⭐⭐⭐ first), then posted desc (newest first) within each group
    from datetime import datetime, date as date_type, timedelta

    def parse_posted_with_seen(row):
        """Convert posted value to a sortable date using First Seen as anchor."""
        posted = row[4] if len(row) > 4 else ""
        first_seen = row[7] if len(row) > 7 else ""

        if not posted:
            return date_type(2000, 1, 1)

        val_lower = posted.lower()

        # Try ISO date first
        try:
            dt = datetime.fromisoformat(posted)
            return dt.date()
        except (ValueError, TypeError):
            pass

        # Relative text — use first_seen as anchor
        seen_date = date_type.today()
        if first_seen:
            try:
                seen_date = datetime.fromisoformat(first_seen).date()
            except (ValueError, TypeError):
                try:
                    seen_date = datetime.strptime(first_seen, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    pass

        if "today" in val_lower:
            return seen_date
        if "yesterday" in val_lower:
            return seen_date - timedelta(days=1)
        if "days ago" in val_lower:
            m = re.search(r'(\d+)', posted)
            if m:
                return seen_date - timedelta(days=int(m.group(1)))
            return seen_date - timedelta(days=1)
        if "week" in val_lower:
            m = re.search(r'(\d+)', posted)
            weeks = int(m.group(1)) if m else 1
            return seen_date - timedelta(weeks=weeks)
        if "month" in val_lower:
            m = re.search(r'(\d+)', posted)
            months = int(m.group(1)) if m else 1
            return seen_date - timedelta(days=months * 30)

        return date_type(2000, 1, 1)

    data_rows.sort(key=parse_posted_with_seen, reverse=True)  # newest first
    data_rows.sort(key=lambda row: rating_order.get(row[0] if len(row) > 0 else "", 9))  # ⭐⭐⭐ first
    
    # Clear and rewrite
    header = all_values[0] if all_values else ["Rating", "Title", "Company", "Location", "Posted", "Source", "Apply", "First Seen"]
    all_new = [header] + data_rows

    # Clear existing data completely
    range_to_clear = f"{SHEET_NAME}!A1:H"
    clear_sheet_values(token, range_to_clear)

    # Write all data
    if len(all_new) > 1:
        end_row = len(all_new)
        
        # Build the values with HYPERLINK formulas for column G
        write_values = []
        write_values.append(header)  # header row
        for row in data_rows:
            url = row[COL_URL - 1] if len(row) >= COL_URL else ""
            out_row = list(row)
            # Pad to NCOLS if needed
            while len(out_row) < NCOLS:
                out_row.append("")
            if url and url.startswith("http"):
                out_row[COL_URL - 1] = f'=HYPERLINK("{url}","Apply")'
            write_values.append(out_row)
        
        write_range = f"{SHEET_NAME}!A1:H{end_row}"
        # Use RAW input so =HYPERLINK is treated as a formula
        write_sheet_values(token, write_range, write_values, "USER_ENTERED")

    pruned = (len(all_values) - 1) - len(rows_to_keep)
    added = len(new_rows)
    total = len(all_new) - 1

    return {"added": added, "pruned": pruned, "total": total}


def style_sheet(token):
    """Apply formatting to the sheet."""
    # Get sheet ID (gid) for the Jobs sheet
    meta = sheets_api("GET", "?fields=sheets.properties", token)
    sheet_id = None
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == SHEET_NAME:
            sheet_id = s["properties"]["sheetId"]
            break

    if sheet_id is None:
        return

    try:
        batch_update(token, [
            # Header styling
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True},
                            "backgroundColor": {"red": 0.1, "green": 0.1, "blue": 0.18}
                        }
                    },
                    "fields": "userEnteredFormat(textFormat.bold,backgroundColor)"
                }
            },
            # Freeze header
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount"
                }
            },
        ])
    except Exception as e:
        print(f"  ⚠ Style error (non-fatal): {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync filtered jobs to Google Sheet")
    parser.add_argument("--input", help="Path to filtered jobs JSON (from filter_jobs.py)")
    parser.add_argument("--clear", action="store_true", help="Clear all rows in the sheet")
    parser.add_argument("--sheet-id", help="Google Sheet ID (overrides JOBCLAW_SHEET_ID env var)")
    parser.add_argument("--style", action="store_true", help="Apply formatting only")
    args = parser.parse_args()

    global SS_ID
    if args.sheet_id:
        SS_ID = args.sheet_id
    elif not SS_ID:
        # Try loading from config.json
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
                SS_ID = cfg.get("sheet_id", SS_ID)

    if not SS_ID:
        print("Error: No sheet ID configured. Set JOBCLAW_SHEET_ID env var or add sheet_id to config.json", file=sys.stderr)
        sys.exit(1)

    token = get_access_token()

    # -- Clear mode ----------------------------------------------------------
    if args.clear:
        print("Clearing all rows...", file=sys.stderr)
        clear_sheet_values(token, f"{SHEET_NAME}!A2:H")
        print("Done.", file=sys.stderr)
        return

    # -- Style mode ----------------------------------------------------------
    if args.style:
        style_sheet(token)
        print("Style applied.", file=sys.stderr)
        return

    # -- Sync mode -----------------------------------------------------------
    if not args.input:
        parser.error("--input is required for sync mode")

    with open(args.input) as f:
        jobs = json.load(f)

    print(f"Syncing {len(jobs)} jobs to Google Sheet...", file=sys.stderr)
    result = sync_jobs(token, jobs)

    print(
        f"  ✓ Added : {result['added']} new jobs\n"
        f"  ✓ Pruned: {result['pruned']} missing/stale jobs\n"
        f"  ✓ Total : {result['total']} rows in sheet",
        file=sys.stderr,
    )

    # Apply styling
    style_sheet(token)


if __name__ == "__main__":
    main()
