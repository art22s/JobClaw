#!/usr/bin/env python3
"""
collect_websearch.py — Reads WebSearch results from stdin (one JSON object per line,
or a JSON array) and writes a deduplicated array to --output.

The agent collects WebSearch results as structured data and pipes them here,
avoiding the need to write large JSON blobs via shell heredocs (which get
flagged as obfuscation by security gateways).

Usage — pipe newline-delimited JSON from the agent:
    echo '{"title":"...","url":"...","company":"...","location":"...","date_posted":"","source":"websearch"}' | \
    python3 scripts/collect_websearch.py --output data/websearch_raw.json

Usage — append a single result:
    python3 scripts/collect_websearch.py \
      --append data/websearch_raw.json \
      --title "Senior Data Analyst" \
      --url "https://jobs.ashbyhq.com/..." \
      --company "Acme Corp" \
      --location "Remote" \
      --date-posted ""

Usage — initialise an empty file:
    python3 scripts/collect_websearch.py --init data/websearch_raw.json
"""

import argparse
import json
import sys
import os


def load_existing(path):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save(path, entries):
    with open(path, "w") as f:
        json.dump(entries, f, indent=2)


def dedupe(entries):
    seen = set()
    out = []
    for e in entries:
        key = e.get("url", "")
        if key and key not in seen:
            seen.add(key)
            out.append(e)
        elif not key:
            out.append(e)
    return out


def main():
    parser = argparse.ArgumentParser(description="Collect WebSearch results into JSON")
    parser.add_argument("--output",      help="Write full array to this path (reads stdin)")
    parser.add_argument("--append",      help="Append a single entry to this path")
    parser.add_argument("--init",        help="Create empty JSON array at this path")
    parser.add_argument("--title",       default="")
    parser.add_argument("--url",         default="")
    parser.add_argument("--company",     default="")
    parser.add_argument("--location",    default="")
    parser.add_argument("--date-posted", default="", dest="date_posted")
    args = parser.parse_args()

    # -- Init mode: create empty file
    if args.init:
        save(args.init, [])
        print(f"Initialised empty results file: {args.init}", file=sys.stderr)
        return

    # -- Append mode: add one entry from CLI args
    if args.append:
        entries = load_existing(args.append)
        entries.append({
            "title":       args.title,
            "url":         args.url,
            "company":     args.company,
            "location":    args.location,
            "date_posted": args.date_posted,
            "source":      "websearch",
        })
        entries = dedupe(entries)
        save(args.append, entries)
        print(f"Appended 1 entry → {args.append} ({len(entries)} total)", file=sys.stderr)
        return

    # -- Stdin mode: read newline-delimited JSON or a JSON array
    if args.output:
        raw = sys.stdin.read().strip()
        if not raw:
            save(args.output, [])
            print("No input; saved empty array.", file=sys.stderr)
            return

        entries = []
        # Try as JSON array first
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                entries = parsed
            elif isinstance(parsed, dict):
                entries = [parsed]
        except json.JSONDecodeError:
            # Fall back to newline-delimited JSON
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"Skipping non-JSON line: {line[:80]}", file=sys.stderr)

        entries = dedupe(entries)
        save(args.output, entries)
        print(f"Saved {len(entries)} WebSearch entries → {args.output}", file=sys.stderr)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
