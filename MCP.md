# StudyHub MCP server + API token

StudyHub exposes an MCP endpoint so Claude (with your own Claude subscription) can read and
update your tasks, timetable and notes. The same token also works as a Bearer token for every
`/api/...` endpoint (n8n, scripts, Claude Code).

## 1. Create the token (on the server)

```bash
cd ~/studyhub
STUDY_CONFIG_FILE=~/studyhub/data/config.json \
STUDY_PUBLIC_ORIGIN=https://YOUR-SITE \
  .venv/bin/python -m studyhub token
sudo systemctl restart studyhub.service
```

It prints the token once, plus the connector URL. Running it again rotates the token.
Only a SHA-256 hash is stored in `config.json`.

## 2. Connect Claude

- **Claude (web / desktop / mobile):** Settings → Connectors → Add custom connector →
  URL `https://YOUR-SITE/mcp/<TOKEN>`. Treat this URL like a password.
- **Claude Code:**
  `claude mcp add --transport http studyhub https://YOUR-SITE/mcp --header "Authorization: Bearer <TOKEN>"`

## 3. Tools

| Tool | What it does |
|---|---|
| get_today | Cairo time, tasks for today / next 48h, overdue, today's timetable |
| list_tasks | Tasks by status / course / text |
| create_task / update_task | Add or change tasks (due "YYYY-MM-DD" = end of day, Cairo) |
| list_courses | Courses + Obsidian folder + open tasks |
| get_schedule | Weekly timetable (or one day) |
| search_notes / read_note | Full-text search over Obsidian + StudyHub notes, then read one |
| create_note | Save a StudyHub note (Obsidian files are never modified) |
| create_course / create_slot | Add a course, or a weekly lecture/section/lab/study slot |
| list_files | PDFs / slides / code in the course folders + uploads |
| weekly_review | Focus minutes per day/course, done tasks, missed deadlines, streak (Sat–Fri) |

## 4. API examples

```bash
curl -H "Authorization: Bearer $TOKEN" https://YOUR-SITE/api/state
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"title":"Sheet 3","due":"2026-10-25T23:59:00+03:00"}' \
  https://YOUR-SITE/api/tasks
```

## Notes

- Transport: MCP streamable HTTP, stateless JSON responses (no sessions, no SSE).
- The URL token is redacted from uvicorn access logs (`/mcp/***`).
- Revoke access: run the `token` command again and restart (old token stops working).
- Focus minutes are logged from the task timer (table `study_sessions`) since this update.

## In-app assistant (chat inside StudyHub)

The ✦ button opens a chat that uses the same tools, through any OpenAI-compatible model.
Your Claude subscription can't be used inside the site, so pick a model once on the server:

```bash
cd ~/studyhub
STUDY_CONFIG_FILE=~/studyhub/data/config.json .venv/bin/python -m studyhub llm
sudo systemctl restart studyhub.service
```

1 = Groq (free key from console.groq.com), 2 = Gemini (aistudio.google.com), 3 = OpenRouter, 4 = local Ollama.
