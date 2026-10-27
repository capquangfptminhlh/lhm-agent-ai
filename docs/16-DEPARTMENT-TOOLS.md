# LHM 16 department tools — role-bound integration pilot

**Status:** Code/adapter integration in this PR, not a claim that 16 external services are installed or connected. All third-party access requires the owner's own credentials and service deployments. The 16 native roles still use one local workspace and one Worker. This is not a public multi-tenant SaaS security boundary.

## Architecture

Each of the 16 specialist roles has:
1. One immutable fixed profession in `agent_work_orders.py` and a stable primary repository reference.
2. A limited set of existing Tool Hub actions (7 direct HTTP integrations), never generic arbitrary HTTP/JS. Postiz and WordPress are publication adapters; Crawl4AI, Twenty, Chatwoot, ComfyUI and Activepieces are the other implemented adapters.
3. A **distinct Activepieces Flow webhook URL** `LHM_DEPARTMENT_<ROLE>_WEBHOOK_URL`. No user-controlled URL, headers, credentials or workflow script is accepted by the request. This creates the role-specific action `department.run.<role>`.
4. A local work order and real Worker output, mandatory Quality review bound to the full output SHA-256, then a separate human authorization before any webhook call.
5. One-shot dispatch and an explicit **`workflow_triggered_unverified`** receipt. A 202/200 only means the webhook received the data; it does not prove a social post, CRM entry or financial transaction happened.

Readiness in the UI is **configuration-only**. No API response, credentials, screenshots, role job or Postiz connection are fabricated.

## Roles and tools

| Role | Primary domain service (source repository reference) | Already implemented actions | Own workflow bridge |
| --- | --- | --- | --- |
| Director | Activepieces | Activepieces trigger | director |
| Research | Crawl4AI | Crawl4AI crawl | research |
| Strategy | Crawl4AI | Crawl4AI crawl | strategy |
| Lead | Crawl4AI | Crawl4AI crawl, Twenty create person | lead |
| Content / Social | WordPress, Postiz | WordPress create post, Postiz create post, ComfyUI image job | content |
| Ads | Google Ads API (not connected), ComfyUI | ComfyUI image job, Activepieces | ads |
| CRM | Twenty CRM | Twenty create person, Chatwoot create contact | crm |
| Sales | Twenty CRM | Twenty create person, Activepieces | sales |
| Quality | Crawl4AI | Crawl4AI crawl | quality |
| Analytics | Search Console/Metabase (not connected) | Activepieces | analytics |
| Operations | Activepieces | Activepieces trigger | operations |
| Finance | ERPNext (not connected) | Activepieces | finance |
| HR | Frappe HRMS (not connected) | Activepieces | hr |
| Procurement | Crawl4AI/ERPNext | Crawl4AI crawl, Activepieces | procurement |
| Support | Chatwoot | Chatwoot create contact, Activepieces | support |
| Compliance | Activepieces | Crawl4AI crawl, Activepieces | compliance |

**Maturity matters**: a repository in the catalog is a source-code reference, not a deployed executable or verified authenticated service. Domain-specific providers such as ERPNext, HRMS, Metabase, Google Ads and Meta Ads need their own future connectors and account authorization. A Flow can orchestrate them only after the operator has really installed/configured the provider and checked the Flow.

## Setup

1. Install and start the LHM backend, Worker and model from the project README. In Electron choose **Công cụ 16 phòng ban**, or browse local `http://127.0.0.1:7770/agents/departments/ui`.
2. Install Activepieces yourself (its original source: https://github.com/activepieces/activepieces) or use an account. Create **separate** webhook-triggered Flows, one per department. Each Flow must validate the fixed incoming `role`, `work_order_id`, `task_id` and `content`. Enable and test each Flow in a staging environment.
3. In the local server's `.env`, set the corresponding `LHM_DEPARTMENT_<ROLE>_WEBHOOK_URL`. Leave unused roles blank. HTTPS required except `localhost` for local tests. Never copy the webhook or account credentials into the browser, chat or repository.
4. Use the screen: **Giao việc** → wait for Worker completion → **Giao Quality kiểm tra** → after QA `pass`, **Tạo yêu cầu Tool** → **Duyệt** → **Gửi workflow**.
5. Check Activepieces execution logs and target-system record IDs, URLs or screenshots as appropriate. A network timeout or a provider error is **not** permission to post again immediately: reconcile the first attempt before any manual replacement action.

### Input contract

The webhook receives only:

```json
{
  "source": "LHM-Agent-OS",
  "role": "content",
  "work_order_id": "<real work order ID>",
  "task_id": "<real fixed-agent task ID>",
  "content": "<exact completed QA-approved output>"
}
```

No AI-generated headers, endpoint URLs, shell commands or arbitrary scripts. All approved requests use an action + payload digest; a changed source fails closed at creation, approval and execution.

## API

- `GET /agents/departments`: 16 role toolboxes, connection *configuration* flags, no credential disclosure.
- `GET /agents/departments/ui`: role tool desk.
- `POST /workspaces/{ws}/agents/{role}/department-tool-jobs`: fixed-role handoff `{"work_order_id":"...","idempotency_key":"..."}`, requires completed matching fixed task, QA pass, role webhook configuration. Returns a Tool Job waiting for owner approval.
- `POST /tool-jobs/{id}/approve`: human authorization.
- `POST /tool-jobs/{id}/execute`: executes **only once**, replies `accepted_unverified` if webhook accepts. Replays are blocked.
- `GET /workspaces/{ws}/tool-jobs`: audit, status and receipts.

## Social login on a Windows PC

Postiz/API is preferred when a connected social account supports publication. Existing browser sessions are NOT automatically attached to LHM: the Computer Runner has a separate Playwright profile and currently requires explicit operator approval for sensitive clicks/fill, and the Windows desktop pilot shares one cursor. To publish through an already logged-in personal account, the operator must use the supervised computer UI and independently verify results. Do not promise automatic Facebook Group or personal Zalo group publishing where API rights/terms are not supported. A future Browser Use integration must have a dedicated authorized profile, capability boundaries, step approvals, anti-duplicate records and live verification before enabling an automatic publishing button.

## Release gates

CI (no third-party credentials) covers all roles, QA, idempotency, role/workspace restrictions, tamper rejection, one real HTTP dispatch path mocked at the network boundary, and browser JS syntax. **Live Flow, ERP, Google/Meta Ads, social publishing and Windows owner-machine acceptance are not covered by mocked CI.** Commercial release needs per-tenant secrets/RBAC, retention policy, signed workflow receipts, real provider smoke tests and desktop pilot acceptance.
