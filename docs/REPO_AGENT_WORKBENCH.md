# Repository-bound project agents (pilot)

Open **http://127.0.0.1:7770/repo-agents/ui** after the LHM backend and worker are running.

Unlike the 16 general business-role agents, these nine project agents each own **exactly one verified repository**, an unchangeable CI-triage job, and a single read-only GitHub Actions inspection path. No input from the browser can substitute a different GitHub repository.

| Project agent | Bound repository | Fixed role |
| --- | --- | --- |
| agent-platform | capquangfptminhlh/lhm-agent-ai | platform CI stability |
| agent-seo-crm | capquangfptminhlh/crmminh | SEO publishing pipeline CI |
| agent-autotax | capquangfptminhlh/autotax | AutoTax quality gates |
| agent-browser | capquangfptminhlh/ggminh | Windows browser packaging CI |
| agent-ads | capquangfptminhlh/lhm-ads-ai | Ads app CI |
| agent-pickle | capquangfptminhlh/pickle | Pickleup CI |
| agent-cnlg | capquangfptminhlh/cnlg | CNLG CI |
| agent-english-game | capquangfptminhlh/tienganh | English game CI |
| agent-zalo-crm | capquangfptminhlh/zalo-crm-frontend | Zalo CRM frontend CI |

## What actually executes

1. **Kiểm tra CI thật** sends GET to the **GitHub Actions API** with a fixed repository URL (not to an LLM). It returns up to five real runs and their run IDs/statuses. Inaccessible/failed API returns an error, not "PASS".
2. **Chẩn đoán bằng AI** first fetches the latest real run. Then a fixed-specialty work order is stored in SQLite. The existing background Worker must be running and an OpenRouter Free model must be configured for a diagnostic **draft** to appear. Repeat requests for the same agent + run are idempotent.
3. The report explicitly says whether code was changed and whether a PR was created. **Both are false in this pilot.** It does **not** read job logs or edit code, does not run tests on the bound repository, and does not deploy or publish any content.

Repo URL and a source/tool repository in the 16-role catalog are different concepts. The owner-specific assignment is authoritative for these project CI checks; installing an unrelated third-party tool is not equivalent to GitHub access.

## Configuration

- Public repositories may be queried without a token (subject to GitHub API rate limits).
- For private ones, place a **fine-grained read-only** token only on the server side as `LHM_GITHUB_READ_TOKEN`, with **Metadata: read** and **Actions: read** for the repositories concerned. Never paste it into the chat, browser UI, or commit it.
- Start backend and Worker normally. For AI diagnosis, configure **OpenRouter Free**; otherwise GitHub inspection still works, but the work order remains queued/blocked.
- Backend stays loopback-only without `LHM_ADMIN_TOKEN`. If using the app across the LAN or Internet, first configure authentication and HTTPS; this pilot is not a multi-tenant permission system.

## API contract

- `GET /repo-agents` — fixed, read-only assignments.
- `GET /repo-agents/{id}/ci` — actual GitHub Actions read, no API token returned to client.
- `POST /repo-agents/{id}/diagnose` with `{"workspace":"demo"}` — queues real Worker triage based on observed CI run, not code changes.

## Required gates for a future code-writing release

A separately permissioned GitHub App for each authorized repository, checkout in a disposable sandbox, bounded diffs and test commands, reviewer approval before PR, protected main branches, and verified PR/CI receipts. Until those controls and end-to-end tests exist, **do not label any project agent as an autonomous coding agent**.
