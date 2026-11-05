# Chạy lại GitHub Actions CI bị lỗi trên đúng Pull Request

## Chức năng

Sau khi Agent đã tạo Draft PR và PR có `failed_observed`:
vào `/repo-agents/ui` → chọn repo → **Bản vá & PR nháp** →
**Xem bản vá gần nhất** → **Tìm workflow CI lỗi có thể chạy lại**.
Chọn run cụ thể và bấm **Duyệt yêu cầu GitHub chạy lại CI lỗi**.

Backend đọc `pulls/{number}`, `commits/{sha}/check-runs`,
`commits/{sha}/status`, `actions/runs?head_sha={sha}`.
Chỉ cho chọn workflow **đã thất bại** tại chính commit HEAD và nhánh
Draft PR thuộc một trong 9 Agent cố định. Workflow phải trong vòng 30
ngày, status completed, event push hoặc pull_request, path GitHub Actions
và không mang tên deploy/release/publish/production/migration/payment/
billing/secret/rollback. Nếu GitHub workflow không dùng đúng head SHA,
hệ thống không cho chạy lại; dùng giao diện GitHub thủ công thay thế.

### API

- GET `/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/rerunnable-runs`
- POST `/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/rerun-failed`,
  body `{"run_id":12345}`
- GET `/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/rerun-history`

### Bảo mật và vận hành

- Chỉ thao tác sau khi **chủ máy duyệt một run cụ thể**. Không tự phát
  rerun khi AI đọc logs hay tự merge sau khi xanh.
- **LHM_ADMIN_TOKEN** để xác thực owner tại backend.
- **LHM_GITHUB_ACTIONS_TOKEN**: token tách riêng cần **Actions:write**.
  Không dùng `LHM_GITHUB_WRITE_TOKEN` sửa code thay cho token Actions.
- GitHub endpoint POST `/actions/runs/{run_id}/rerun-failed-jobs` trả
  **HTTP 201, không có JSON**, không suy diễn là test PASS. GitHub dùng
  cùng quyền actor đã chạy ban đầu, và workflow có thể dùng secrets/tác
  động ngoài; chủ máy phải tự xem workflow trước khi duyệt.
- Giới hạn workflow theo tên/path chỉ là bảo vệ cơ bản, **không thể bảo
  đảm workflow hoàn toàn không có side effect**.
- Re-read run ID/head SHA/branch/attempt ngay trước khi gửi; mỗi run chỉ
  được yêu cầu tối đa một lần qua Agent ở workspace/PR này.
- Ghi audit SQLite trước call. HTTP không xác nhận → đánh dấu
  `needs_reconciliation`, **không tự retry** khi không rõ trạng thái.
- Run mới có thể phải kiểm tra thủ công; PR CI status vẫn cần đọc lại.
  Nếu token Actions chưa cấu hình, nút duyệt trả thông báo thiết lập.
- Chưa chạy local code của repository đích, chưa xác minh chất lượng
  bản sửa bằng test đích, không tự deploy/release/merge.

### Test acceptance

FastAPI + SQLite thật và mocked GitHub HTTP: chọn workflow đúng PR HEAD,
POST 201 với token Actions-only, giữ idempotency, thiếu admin/token,
PR xanh, workflow deployment bị chặn, branch đổi, 403/reconciliation,
cross-workspace và JavaScript UI. Linux/Docker/Chrome/WebGL/Computer
Runner và Windows EXE/GUI/NSIS là release gate.
