# Repo Agent: sửa file có duyệt → GitHub Draft PR

## Chức năng thực thi

Giao diện `/repo-agents/ui` → chọn **Bản vá & PR nháp** cho đúng Agent
và repository. Sau khi Agent có **báo cáo CI đã hoàn tất**, chủ máy:

1. Chọn path file UTF-8 đang có (ví dụ `src/app.py`), bấm **Đọc file main để chỉnh sửa**.
2. Chỉnh sửa đúng **toàn bộ nội dung file mới**, điền lý do và cách kiểm thử.
3. Bấm **Lưu đề xuất & xem diff**. Backend lấy SHA main và SHA của file
   trực tiếp từ GitHub, tính diff, lưu SQLite `awaiting_approval`;
   bước này **chưa ghi lên GitHub**.
4. Xem diff và bấm **Duyệt tạo Pull Request nháp**, xác nhận riêng.
   Backend kiểm tra lại main HEAD và file blob SHA. Nếu main/file đã thay đổi,
   trả `stale`, **không** ghi file cũ đè lên.
5. Khi hợp lệ, backend tạo một branch `agent-repair/<agent>/<id>`, cập
   nhật đúng một file, rồi mở **Draft Pull Request**. Không merge,
   không release, không tự deploy.
6. Nếu lỗi sau khi đã bắt đầu ghi GitHub: `needs_reconciliation`.
   Chủ máy cần đối soát branch/PR trực tiếp. Backend **không retry mù**
   để tránh tạo PR hoặc commit trùng.

## Quyền và bảo vệ dữ liệu

- Chỉ 9 repository đã cố định trong server. Không gửi URL, owner hoặc
  repository tự do từ phía người dùng/LLM.
- Cần **`LHM_GITHUB_WRITE_TOKEN`** đặt ở môi trường backend. Quyền tối
  thiểu GitHub: Contents read/write và Pull requests read/write trên
  đúng repository cần sửa. Không nhập token vào textbox của UI.
- Token đọc `LHM_GITHUB_READ_TOKEN` **không đủ** cấp quyền ghi.
- Token chưa có → HTTP 424, đề xuất giữ nguyên trạng thái chờ duyệt.
- Chặn đường dẫn chứa `..`, `.github/`, dữ liệu có tên nhạy cảm và các
  loại file ngoài whitelist; chặn binary/file lớn.
- API dùng cùng bảo vệ localhost/admin token của FastAPI hiện tại.
  **Chưa đủ tenant security để cung cấp dịch vụ SaaS qua Internet**.
- Không thực thi mã từ source repo tại máy chủ khi tạo PR; bộ cài
  Windows/Chrome không cần mở quyền để tiếp cận token GitHub.
- Lưu full `new_content` trong SQLite cục bộ để xem lại, không nhúng
  token vào prompt hay gửi sang model trong luồng này.

## Nghiệm thu

- GitHub API được mô phỏng ở HTTP boundary, còn FastAPI/SQLite thật:
  xem source, lập diff, chống idempotency, duyệt, tạo branch, PUT contents,
  tạo draft PR, không auto merge.
- Kiểm tra các trường hợp: không có write token, main đổi, repo khác,
  workspace khác, đường dẫn bí mật, từ chối, lỗi nửa chừng → reconcile.
- GitHub Actions: unit/sidecar/Chrome và Windows EXE/GUI/NSIS đều PASS
  trước khi merge vào `main`.
- Chưa có cơ chế **AI tự tạo replacement source text**: chủ máy vẫn chỉnh
  nội dung đề xuất. Chưa kiểm thử ghi repository private thực vì
  không đưa token sản xuất vào CI. Draft PR vẫn phải qua GitHub CI và review.

## API

- `GET /repo-agents/{agent_id}/repair-source?path=src/app.py`
- `POST /workspaces/{ws}/repo-agents/{agent_id}/repairs`
- `GET /workspaces/{ws}/repo-agents/{agent_id}/repairs`
- `GET /workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}`
- `POST /workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/approve`
- `POST /workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/reject`

Lưu ý: Draft PR không đồng nghĩa đã sửa được lỗi CI hoặc đã deploy.
