# Contributing to JobClaw 🦞

Thanks for your interest! Here's how to get involved.

## Quick Start

1. Fork the repo
2. Create a feature branch: `git checkout -b my-feature`
3. Make your changes
4. Submit a pull request

## Ways to Contribute

### 🐛 Report Bugs
Open a [bug report issue](https://github.com/art22s/JobClaw/issues/new?template=bug_report.yml) with steps to reproduce.

### ✨ Suggest Features
Open a [feature request](https://github.com/art22s/JobClaw/issues/new?template=feature_request.yml) describing the problem and your proposed solution.

### 📋 Add Companies / ATS Sources
Know a company that uses Greenhouse, Lever, or Workday? Open a [new company issue](https://github.com/art22s/JobClaw/issues/new?template=new_company.yml) with the company name and careers URL.

### 💻 Submit Code

**Setup:**
```bash
git clone https://github.com/art22s/JobClaw.git
cd JobClaw
pip install playwright pymupdf
playwright install chromium
```

**Before submitting a PR:**
- Test your changes with a real profile (or `profiles/example.md`)
- If adding a new ATS fetcher, follow the pattern in `scripts/fetch_greenhouse.py`
- Keep filtering logic deterministic — no LLM calls in the core pipeline
- Update `SKILL.md` if your change affects the pipeline steps

### 🧪 Testing

Currently there's no formal test suite. For now:
- Run `python3 job_search.py fetch --source <your_source> --profile profiles/example.md` and verify output
- Run `python3 job_search.py filter --profile profiles/example.md` and check filtered results
- Check the HTML report renders correctly

## Code Style

- Python 3.10+
- Follow existing patterns in the codebase
- Descriptive variable names over comments when possible
- Functions should do one thing

## Adding a New ATS Source

1. Create `scripts/fetch_<source>.py` following the pattern of existing fetchers
2. Output raw JSON in the same format as other fetchers
3. Add CLI args (`--source <name>`) to `job_search.py`
4. Update `filter_jobs.py` to accept the new source path
5. Update `SKILL.md` with the new step
6. Add the source to the README features list

## Questions?

Open a [discussion](https://github.com/art22s/JobClaw/discussions) or hop into the [OpenClaw Discord](https://discord.com/invite/clawd).

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
