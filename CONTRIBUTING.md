# Contributing to StudyHub

شكرًا إنك عايز تساعد! Contributions in Arabic or English are both welcome: issues, pull requests and reviews.

## Ways to help

- **Use it and report what breaks.** Open a bug with the page, the language (عربي/English), the theme and your device.
- **Pick an issue** labelled `good first issue` or `help wanted`.
- **Translate.** The UI is Arabic-first; English strings live in `static/i18n.js` (one `"Arabic": "English"` map).
- **Bring StudyHub to your faculty.** College features are driven by config and source adapters (`college_sources.py`), so other universities can plug in their own channels: see the README's "Bring it to your faculty".

## Run it locally

With Docker: `docker compose run --rm studyhub init && docker compose up -d`. Without Docker:

```bash
mkdir -p ~/studyhub && cd ~/studyhub
git clone https://github.com/<you>/studyhub.git studyhub     # the repo folder IS the Python package
python3 -m venv .venv
.venv/bin/pip install -r studyhub/requirements.txt -r studyhub/requirements-dev.txt
.venv/bin/playwright install chromium
export STUDY_CONFIG_FILE=$PWD/data/config.json STUDY_SECURE_COOKIE=false
.venv/bin/python -m studyhub init && .venv/bin/python -m studyhub serve
```

## Before you open a pull request

```bash
cd ~/studyhub && .venv/bin/python -m pytest studyhub/tests -q
```

All tests must pass (GitHub Actions runs them on every pull request). Add a test for the behaviour you change: API tests go in `tests/test_*_api.py` and `tests/test_study_tools.py`, UI checks in `tests/test_e2e_browser.py`. Anything that calls another site (Codeforces, LeetCode, a model, a college feed) is tested against a small local fake server; see `tests/conftest.py`.

## How the code is laid out

| Area | Where |
|---|---|
| HTTP API (FastAPI) | `app.py`; friends/multi-user accounts in `accounts.py` |
| Assistant, insights, reminders | `assistant.py`, `insights.py`, `reminders.py` |
| College tasks: sync / sources | `board.py` / `college_sources.py` (adapters per faculty) |
| Exams + study plan | `exams.py` |
| Flashcards (spaced repetition) | `cards.py` |
| Codeforces / LeetCode tracker | `cp.py` |
| Calendar link (iCalendar) | `calendar_feed.py` |
| Sharing a course | `shares.py` |
| MCP server for Claude | `mcp_server.py` (see `MCP.md`) |
| Frontend (no build step, no framework) | `static/app.js`, `static/app.css`; the study tools above in `static/study.js` |
| Language / themes | `static/i18n.js`, `static/theme.js` + a token block in `app.css` |

## Style

- Keep it dependency-light: plain JS and CSS, no bundler, no CDN (the CSP blocks third-party hosts).
- Match the surrounding code: short functions, few comments, Arabic UI text in Egyptian Arabic.
- New UI text: `L('عربي', 'English')` (see `static/study.js`), or Arabic in the markup plus its English line in `static/i18n.js`.
- New colours: use the CSS variables (`--panel`, `--ink`, `--accent`, …) so every theme, including dark, keeps working.
- Never commit data, `config.json`, `deploy/deploy.env`, tokens or keys.

## Security

Please don't open public issues for vulnerabilities; see [SECURITY.md](SECURITY.md).

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
