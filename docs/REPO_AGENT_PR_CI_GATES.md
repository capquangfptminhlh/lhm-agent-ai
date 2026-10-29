# Repo Agent — xác minh CI của Pull Request sau khi chủ máy duyệt

## Hành vi đã triển khai

Trong `/repo-agents/ui` chọn Agent, sau đó `Xem bản vá gần nhất`.
Nếu đã có Draft PR thật (`pr_created`), nút
**Kiểm tra CI của PR trên GitHub** xuất hiện.

Backend GET `/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/ci`:

- Bắt buộc PR ID thuộc bản vá đã lưu trong đúng workspace và repo
  thuộc 9 Agent cố định. Không nhận URL, SHA hoặc tên repo tùy ý.
- Đọc PR thật từ GitHub và xác minh PR head repo/branch, base main,
  40 ký tự commit SHA. Nếu repo/branch không khớp, fail-close.
- Đọc `/commits/{sha}/check-runs` (tối đa 100) và
  `/commits/{sha}/status` (tối đa 100 commit statuses).
- Một check failure/timed_out/cancelled/action_required hoặc trạng thái
  commit failure/error → `failed_observed`.
- Có check đang chạy hoặc commit status pending → `checks_running`.
- Có kết quả đầy đủ trong giới hạn và mọi check quan sát đều success →
  `observed_checks_green`, **KHÔNG phải đã qua required checks**.
- Bản ghi thiếu/unknown/pagination/không có check → `unverified`.
- PR đã đóng hoặc merge → `pr_not_open` (không thông báo là PR đang chờ).
- GitHub bị chặn 401/403/404 hoặc lỗi mạng → HTTP 424/503, không giả PASS.
- Kết quả có `required_checks_verified=false`,
  `review_approval_verified=false`, `deployed_verified=false`,
  `automatically_merged=false`.

## Bảo vệ

- Chỉ đọc GitHub API, không tạo commit, rerun, merge, release hay deploy.
- Sử dụng read-only `LHM_GITHUB_READ_TOKEN` nếu có, riêng trên backend.
- Không tin URL từ JSON GitHub; link mở PR được dựng từ repo owner nội bộ.
- Kết quả là **snapshot** tại thời điểm kiểm tra. Repo có nhiều hơn 100
  check/status sẽ không được đánh dấu xanh; repo private cần token read.
- GitHub branch protection, required contexts, review và triển khai
  đích chưa được xác minh độc lập. Không gán nhãn production-ready.

## Acceptance

Test FastAPI + SQLite thực và GitHub HTTP mock bao gồm: nhiều check xanh,
một check lỗi, commit status lỗi, pending, neutral, không có check,
pagination, malformed, PR đóng/merge, repo/ref khác, cross-workspace,
PR chưa được tạo, 403 và kiểm tra JavaScript giao diện.
GitHub Actions Linux unit/Docker/Computer/Chrome và Windows Electron/
GUI/NSIS là gate trước khi merge.
