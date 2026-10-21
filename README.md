# StudyHub — مساحتي

A calm, self-hosted study workspace for university students. You get one place for courses, tasks with deadlines, the weekly timetable, Obsidian lecture notes and uploaded files. It runs on a small home server and installs as a PWA on phone and desktop. The interface is Arabic-first and has a one-click English switch.

It has two modes:

- **Personal site.** One owner, one password.
- **Friends site** (`STUDY_MULTIUSER=1`). Open sign-up, and every user gets private data on the server. A college archive can be shared read-only with everyone.

## Try it

The friends site is live at **https://study-joox-friends.loophole.site**. Open it, create an account (you may need an invite code from the owner) and install it on your phone from the browser menu.

## Video walkthrough

https://github.com/user-attachments/assets/26c5a38d-f465-41d8-87da-a19e826c9ecc

This 3-minute walkthrough (English captions) covers everything a new user does: signing up, adding courses, the timetable, tasks and deadlines, the assistant, the focus timer, Obsidian notes, the weekly review, voice reminders, Claude and installing it on a phone.

You can also download [walkthrough.mp4](docs/walkthrough.mp4), and there are [English subtitles (.srt)](docs/walkthrough.en.srt) and a [written transcript](docs/walkthrough-transcript.md).

## Features

- **Today and Overview:** the day's timetable, priorities, overdue tasks and a quick-add box.
- **Courses:** university and personal courses. Each one can be linked to an Obsidian folder and to the shared college archive.
- **Tasks:** status, priority and deadlines (Africa/Cairo time). A task has a do-date that is separate from its due date. A server-side focus timer survives page reloads.
- **Weekly review dashboard:** a Saturday-to-Friday week with focus minutes per day and per course, finished tasks, missed deadlines, a streak and a 12-week heatmap.
- **Library and reader:**
  - Obsidian-style Markdown: callouts, task lists, `==highlight==`, `%%comments%%`, wiki-links, KaTeX math and Mermaid diagrams.
  - Code highlighting, a table of contents and a reading-progress bar.
  - A collapsible folder tree.
- **Deadline voice reminders:** Web Push reminders at 1 day, 12 hours and 1 hour before a deadline. Each stage plays a random clip from a folder of your own recorded voice notes.
- **In-app assistant (optional):** chat with any OpenAI-compatible model (Groq, Gemini, OpenRouter, Ollama…) that can create tasks, courses, timetable slots and notes for you. A fallback provider takes over when the main one is rate-limited.
- **MCP server:** connect Claude (with your own subscription) as a custom connector. It gets 13 tools: today, tasks, courses, schedule, notes search/read/create, files and the weekly review. See [MCP.md](MCP.md).
- **Friends mode:**
  - Accounts: open sign-up and per-user data folders.
  - Limits: quotas and a daily assistant cap.
  - Vault import: Obsidian vault upload as a ZIP or a browser folder picker.
  - Per-user MCP tokens.
- **Security:**
  - Strict CSP, and every library is self-hosted with no CDN.
  - PBKDF2 password hashes; only hashes of tokens are stored.
  - Rate-limited login and sign-up.
  - Path-traversal-safe uploads and ZIP extraction.

## Stack

FastAPI with SQLite (JSON documents) on the server. The frontend is plain JavaScript and CSS with no build step: `static/`, a service worker and self-hosted KaTeX, Mermaid, highlight.js and IBM Plex Sans Arabic.

## Run it locally

The repository folder **is** the Python package `studyhub`, so clone it into a parent folder:

```bash
mkdir -p ~/studyhub && cd ~/studyhub
git clone https://github.com/yousefJooX/studyhub.git studyhub
python3 -m venv .venv
.venv/bin/pip install -r studyhub/requirements.txt

export STUDY_CONFIG_FILE=$PWD/data/config.json
export STUDY_SECURE_COOKIE=false            # only for plain http://localhost
.venv/bin/python -m studyhub init           # sets the owner password (12+ chars)
.venv/bin/python -m studyhub serve --port 8091
```

Then open <http://127.0.0.1:8091>.

`init` also takes two optional flags:

- `--vault-root PATH` points to an Obsidian vault copy on the server.
- `--archive-root PATH` points to a college archive data folder.

## CLI

| Command | What it does |
|---|---|
| `init` / `password` | Create the config or change the owner password |
| `serve` | Run the web app |
| `seed-vault` | Create courses from the vault's top-level folders |
| `token` | Create or rotate the MCP/API token and print the connector URL |
| `llm` | Configure the assistant's model provider and an optional fallback; it checks which models your key can use |
| `users list \| disable \| enable \| delete \| reset-password NAME` | Manage accounts on the friends site |

## Configuration

Settings live in `config.json` (path from `STUDY_CONFIG_FILE`, mode 600) or in environment variables.

| Key | Purpose |
|---|---|
| `STUDY_DATA_DIR` | Data folder (SQLite, uploads, vault, voice clips) |
| `STUDY_PUBLIC_ORIGIN` | Public HTTPS address (used for CORS, the MCP URL and Web Push) |
| `STUDY_OWNER_NAME` | Name shown in the greeting on the personal site |
| `STUDY_VAULT_ROOT`, `ARCHIVE_DATA_DIR` | Obsidian vault copy and the shared college archive |
| `STUDY_LLM_BASE_URL`, `STUDY_LLM_API_KEY`, `STUDY_LLM_MODEL` | Assistant model; set them with `python -m studyhub llm` |
| `STUDY_LLM_FALLBACK_*` | Optional second provider |
| `STUDY_MULTIUSER=1` | Friends mode |
| `STUDY_SIGNUP` | Friends-mode sign-up: `open` or `closed` |
| `STUDY_MAX_USERS` | Friends-mode account limit |
| `STUDY_USER_QUOTA_MB` | Friends-mode storage limit per user |
| `STUDY_ASSISTANT_DAILY` | Friends-mode assistant messages per user per day |
| `STUDY_SIGNUP_CODE` | Friends-mode invite code; when set, sign-up needs it |
| `STUDY_MIN_FREE_MB` | Friends-mode uploads stop below this much free disk (default 2048) |

## Deploy to a home server

`deploy/` has scripts that rsync the code to the server over SSH, restart the systemd service and run health checks:

```bash
cp deploy/deploy.env.example deploy/deploy.env    # your server, paths and address (git-ignored)
bash deploy/deploy_to_server.sh                   # deploy + restart
bash deploy/deploy_to_server.sh --token           # also rotate the MCP token
bash deploy/setup_friends.sh                      # one-time: friends site on port 8092 with its own HTTPS address
```

For HTTPS you can put a domain behind Caddy (`deploy/Caddyfile.example`). The other option is a free [loophole](https://loophole.cloud) tunnel (`deploy/studyhub-tunnel.service.example`). The service template is `deploy/studyhub.service.example`.

## Security

- The app listens on `127.0.0.1` only; the public address comes from an HTTPS tunnel or reverse proxy.
- Passwords use PBKDF2. Sessions are random tokens stored hashed, in `HttpOnly`, `Secure`, `SameSite=Strict` cookies. API/MCP tokens are stored as SHA-256 hashes.
- Login and sign-up are rate-limited per client address, per username and globally. Behind a local proxy, only the address the proxy adds last to `X-Forwarded-For` is trusted.
- Headers: a strict CSP (no inline scripts, no third-party hosts), HSTS, `X-Frame-Options: DENY`, `nosniff`, COOP and a Permissions-Policy.
- Writes are refused when they come from another origin. Uploads are checked for path traversal and symlinks. Documents other than images, PDF and media are served as downloads.
- Friends mode:
  - Every user's data lives in their own folder and database.
  - Set `STUDY_SIGNUP_CODE` to make sign-up need an invite code.
  - `STUDY_USER_QUOTA_MB` limits each user's storage, and uploads stop when the disk has less than `STUDY_MIN_FREE_MB` free.

Found a security problem? Please open a private security advisory on GitHub instead of a public issue.

## Never commit

`data/`, `config.json`, `deploy/deploy.env`, SQLite files and tokens are all git-ignored. Treat the MCP connector URL like a password.

## License

[MIT](LICENSE)
