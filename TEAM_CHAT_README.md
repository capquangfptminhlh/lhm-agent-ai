# LHM Agent-to-Agent Team Chat

**URL:** `http://127.0.0.1:7770/team-chat`.

Agent-to-Agent communication is implemented with **real, persisted LLM
tasks**; it does not generate artificial conversation bubbles. The
background worker executes the current Agent task via the already
configured OpenRouter/DeepSeek provider, then the coordinator records
the Agent reply and hands the next turn to another permitted coworker.

## What is implemented

1. An operator creates a session with a shared goal, 2–8 distinct
   roles selected from the existing 16 LHM Agents, and a 2–16 turn limit.
   The Director starts by default if selected.
2. The backend creates a **real** task for the first Agent and stores
   the human brief. The optional approval flag pauses this initial
   model invocation.
3. The Agent is instructed to return a JSON object:
   ```json
   {
     "message": "Nội dung và bằng chứng đã kiểm tra",
     "handoff_to": "research",
     "handoff_request": "Kiểm tra lại nguồn dữ liệu được trích dẫn",
     "done": false
   }
   ```
4. The worker ingests only a **completed** Agent task. It saves the
   actual output with a task reference, checks the recipient against
   the session participants, and creates a follow-up task.
   If no valid handoff is supplied, it routes to the next participant
   in deterministic round-robin order.
5. Each new Agent prompt contains the shared goal, initial context,
   and a bounded tail of recorded messages (up to seven, within the
   defined context limit). Human notes can be added at any point,
   and are included in the **following** turn. An in-flight request is
   never silently mutated.
6. The chat is visible at `/team-chat` and is linked from Dashboard,
   2D/3D office, Workflow Builder, Agent Computer and Agent Tools.
7. Sessions may be stopped by a human; queued tasks are cancelled,
   but an LLM call already running may finish before the stop takes
   effect. No new Agent message is scheduled after the stop.
8. Every session stores turn counts, current task IDs, selected roles,
   messages, errors and audit events. Repeat worker sync calls cannot
   create extra handoffs from the same task.

## Operational and safety limits

- A message is **not** a tool execution, social post, browser action
  or privileged instruction. Team Chat cannot bypass Computer Runner
  approvals, specialist tool scopes or publication QA.
- Chats are **sequential task handoffs**, not 16 parallel LLM
  conversations or an independently reasoning multi-agent swarm.
  Agents can choose a colleague within their session; out-of-team
  recipients are ignored. Subsequent workers carry out the handoff.
- The loop is strictly bounded by `max_turns`. When the limit is
  reached, the session changes to `needs_review` and no further
  AI request is created. Failed/budget-blocked/cancelled tasks also
  stop further handoffs.
- If an Agent outputs plain text instead of the requested JSON,
  the actual text is preserved (bounded) and the next participant
  is chosen in round-robin order. No attempted tool calls are parsed
  or executed from text.
- An Agent cannot close the team chat on the first turn; at least one
  other participant must exchange a real response. If a Quality Agent is
  selected, the Quality Agent must contribute a response before anyone
  can conclude. This is a *participation gate*, not proof of QA accuracy.
- `done=true` then produces `completed_unverified`, **not** confirmed
  business completion. A human must verify claims and any cited sources.
- Uses the same task budgets, model key requirements, OpenRouter free
  attempt caps and per-task upper-cost accounting as LHM.
- Messages are scoped by `workspace_id` at the API/SQLite level.
  Existing LHM is a local pilot with one administrator, **not**
  production-grade tenant-specific authorization.
- Designed for local work and approved data. Avoid inserting customer
  PII or secrets into prompts to free model providers without data
  policy and consent review.

## API endpoints

```
GET  /team-chat
POST /workspaces/{ws}/team/sessions
GET  /workspaces/{ws}/team/sessions
GET  /workspaces/{ws}/team/sessions/{session_id}
POST /workspaces/{ws}/team/sessions/{session_id}/notes
POST /workspaces/{ws}/team/sessions/{session_id}/stop
```

The create route accepts `objective`, `roles`, optional `context`,
`max_turns` (default 8), `first_role`, `requires_approval` and
`idempotency_key`. The background worker calls
`sync_ready_team_sessions()` once per cycle, including when no client
has the page open.

## Test plan

`python -m unittest discover -s tests -v` covers role boundaries,
real task handoffs, human notes, workspace isolation, idempotency,
limits, stops, failure propagation, first-turn approval, and UI
JavaScript syntax. CI Docker smoke checks both the chat page and
team session API. Tests use offline fabricated task results **only
inside isolated test databases**, never for user-facing production
messages. Live multi-LLM cooperation with an account API key still
requires an end-to-end pilot run on the owner's Windows machine.
