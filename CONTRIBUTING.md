# Contributing to StudyHub

شكرًا إنك عايز تساعد! Contributions in Arabic or English are both welcome: issues, pull requests and reviews.

## Ways to help

- **Use it and report what breaks.** Open a bug with the page, the language (عربي/English), the theme and your device.
- **Pick an issue** labelled `good first issue` or `help wanted`.
- **Translate.** The UI is Arabic-first; English strings live in `static/i18n.js` (one `"Arabic": "English"` map).
- **Bring StudyHub to your faculty.** College features are driven by config (`STUDY_COLLEGE_CODE`, `STUDY_COLLEGE_NAME`, `STUDY_BOARD_URL`, `STUDY_TELEGRAM_BOT`), so other universities can plug in their own sources.

## Run it locally

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

All tests must pass (GitHub Actions runs them on every pull request). Add a test for the behaviour you change: API tests go in `tests/test_*_api.py`, UI checks in `tests/test_e2e_browser.py`.

## How the code is laid out

| Area | Where |
|---|---|
| HTTP API (FastAPI) | `app.py`; friends/multi-user accounts in `accounts.py` |
| Assistant, insights, reminders | `assistant.py`, `insights.py`, `reminders.py` |
| College tasks (HNU Board) | `board.py` |
| MCP server for Claude | `mcp_server.py` (see `MCP.md`) |
| Frontend (no build step, no framework) | `static/app.js`, `static/app.css` |
| Language / themes | `static/i18n.js`, `static/theme.js` + a token block in `app.css` |

## Style

- Keep it dependency-light: plain JS and CSS, no bundler, no CDN (the CSP blocks third-party hosts).
- Match the surrounding code: short functions, few comments, Arabic UI text in Egyptian Arabic.
- New UI text: write it in Arabic in the markup, then add the English line to `static/i18n.js`.
- New colours: use the CSS variables (`--panel`, `--ink`, `--accent`, …) so every theme, including dark, keeps working.
- Never commit data, `config.json`, `deploy/deploy.env`, tokens or keys.

## Security

Please don't open public issues for vulnerabilities; see [SECURITY.md](SECURITY.md).

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
