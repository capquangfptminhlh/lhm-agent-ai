# LHM Agent Computer — Codex-like Execution Runtime (Pilot)

LHM Agent AI now includes a new **separate Computer Runner** with a bounded
AI → tool → observation loop. This is **not** the OpenAI Codex product,
nor a claim of complete functional equivalence. Its purpose is to
give each of the 16 role-specific LHM Agents a persistent browser profile
and file workspace plus access to a supervised terminal.

## Live architecture

```text
LHM Director / Computer Console (/computer)
       |
 FastAPI + SQLite (runs, steps, approval decisions, audit)
       |
 LHM worker -> OpenRouter free or DeepSeek -> JSON tool choice
       |
 Approval policy -- waiting for human on risky actions
       |
 Private service HTTP (Bearer token, no public port)
       |
 COMPUTER RUNNER CONTAINER (non-root, dedicated storage volume)
       +--> file list / read / write / exact edit
       +--> terminal argv (time-limited, no shell)
       +--> persistent Chrome per workspace+role (Playwright)
                | HTTPS hosts explicitly allowlisted by operator
       |
 Return real tool result -> LLM chooses next step
```

### Implemented actions

| Tool | Description | Gate |
|---|---|---|
| list_files | Read a directory (max 100 entries) | Automatic |
| read_file | Read UTF-8 file inside Agent workspace | Automatic |
| write_file | Write UTF-8 file (max 96 KB) | Human approval by default |
| edit_file | Exact one-match text replace | Human approval by default |
| run_command | Start allowlisted executable with argv, no shell, 18-second timeout | **Always human approval** |
| browser_open | Navigate to operator-approved HTTPS host | Automatic |
| browser_snapshot | Read current page text, links and controls | Automatic |
| browser_scroll | Scroll current browser up/down | Automatic |
| browser_click | Click one CSS locator | **Always human approval** |
| browser_fill | Fill one CSS-located form input | **Always human approval** |
| browser_screenshot | PNG snapshot through authenticated preview route | Direct UI only |

Model chooses one action at a time from a fixed vocabulary; there is **no
generic arbitrary API executor** in the computer loop. Output is recorded in
SQLite, and model only receives bounded observations. The system blocks
self-reported completion before any successful tool observation. Sessions
stop at 1–15 model steps (default 10); the worker does not run the model
forever or auto-charge when a free OpenRouter quota is exhausted.

**Every terminal command and browser mutation requires per-step human
approval.** File writes also require approval by default. Set
`LHM_COMPUTER_AUTO_WRITE=1` only when the agent filesystem is disposable
and the administrator explicitly accepts that risk. This DOES NOT enable
auto-click, auto-fill or unrestricted OS control.

### Initial setup (Docker, local development only)

1. Copy `.env.example` to `.env`.
2. Configure `LHM_LLM_PROVIDER=openrouter`, an actual
   `OPENROUTER_API_KEY`, or supply DeepSeek credentials.
3. Generate a unique runner token **on your own machine**:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Paste into `LHM_COMPUTER_RUNNER_TOKEN` in `.env`. Never put the
   token or social/LLM credentials into GitHub.
4. Set `LHM_COMPUTER_ALLOWED_HOSTS=example.com` (comma-separated exact
   HTTPS domain names the Agent can visit). **Default: no external
   browser navigation.** If websites use third-party assets/CDNs, those
   exact trusted domains may also need allowlisting.
5. Run `docker compose --profile computer up --build -d`.
   The default `docker compose up` does not start the computer sidecar.
6. Open `http://127.0.0.1:7770/computer`. Select workspace, Agent,
   enter a goal, start. The worker will progress one step at a time.
   Review and approve risky steps on the console. A manual one-step
   button uses the same safeguards.
7. Choose **Chụp màn hình** after opening a page to inspect actual
   Chrome. Browser state, files and reports live in the separate named
   Docker volume `lhm_computer_data`.

`LHM_COMPUTER_URL` defaults to `http://computer:7781` and accepts only
the named private sidecar or localhost. The Runner has **no host port**.
The main API/worker still listen on local loopback port 7770.
Do not expose this setup directly to the internet.

### Security and operational limits

- The Runner is a **separate non-root Docker sidecar** with
  `cap_drop: ALL`, `no-new-privileges`, `read_only` root filesystem,
  pids limit, RAM limit, separate named volume and no host mounts.
- **Critically, the runner DOES NOT receive the main `.env`**: no
  DeepSeek, OpenRouter, Postiz, Twenty, Chatwoot or WordPress keys.
  Its child commands inherit a tiny scrubbed environment.
- Paths are restricted under `/agent-data/{workspace}/{role}/files`.
  Each role also has its own persistent Chrome user data directory.
- **Directories and Chrome profiles are NOT per-Agent VMs.** The current
  sidecar still shares a process and OS security boundary between roles.
  Since the terminal can run Python inside that process, it is NOT
  appropriate for hostile/untrusted tenants. True per-Agent isolation
  requires a dedicated container/VM per Agent and strong egress policy.
- Browser network requests are checked against exact approved HTTPS
  hosts. Terminal commands inside the runner are NOT guaranteed
  network-isolated: production should enforce container-level egress
  firewalls, DNS controls and resource limits.
- Browser login forms and saved cookies may contain sensitive data.
  Use disposable test accounts only until the isolation/security review.
  Do not automate banking, purchases or social publishing without a
  separate reviewed tool and explicit approval.
- Screenshot endpoint shows **real PNG from the runner**, not a 3D
  animated proxy. It may contain sensitive personal information.
- If worker crashes while performing an action, run enters
  `needs_review` after timeout, not an automatic retry. A tool request
  whose remote outcome is ambiguous is never blindly resubmitted.
- One worker picks one step per loop. SQLite+one runner is a local
  prototype, not a high-concurrency, multi-tenant SaaS.
- LLM-based action generation is tested with mocks in CI. We have not
  authenticated with a real OpenRouter/DeepSeek account to test end-to-end
  inference, or exercised an actual Chromium instance inside the
  sidecar on Windows hardware.
- The Playwright sidecar image is large; allocate disk/RAM accordingly.

### Acceptance checklist for true Codex-like parity

Still NOT implemented: desktop-wide OS control, full graphical screen
reasoning with multimodal tool results, Git working-copy checkout from
private repositories, code search indexes, PR generation in this loop,
streaming terminal PTY, editor/diff review, resumable VM snapshots,
multi-agent per-VM provisioning, robust secrets/OAuth vault, external
scheduler, hardened network sandbox, large-file/artifact handling,
and end-to-end testing with production accounts.

These are necessary before promising an identical experience to Codex.
