# LHM Virtual Office: "Cần anh" (Owner Desk) — inspired by van-phong-ao-3d

## Nguồn tham khảo
- Repo người dùng chia sẻ: https://github.com/marketingjuliancongdanh79-pixel/van-phong-ao-3d
- README chỉ tới upstream: https://github.com/vinaichatbot-rgb/van-phong-ao-3d
- License repo nguồn: MIT. **Không copy** mã/ảnh/server/agent instructions; tất cả mã thêm vào LHM là triển khai độc lập, giữ backend FastAPI + SQLite + Electron.
- Bản nguồn là Node HTTP web dashboard đọc .claude/.codex và các file data; không phải lõi tác vụ LHM, không phù hợp để gắn nguyên server.js.

## Chức năng đã triển khai
Trang `/office3d` bổ sung bảng **Cần anh xử lý** ở đầu:
- `awaiting_approval`: phê duyệt task hiện có trong Workspace bằng endpoint chính thức `POST /tasks/{id}/approve` sau khi chủ xác nhận. Chỉ dùng trạng thái thật; tuyệt đối không auto-approve.
- `blocked_budget`, `blocked_dependency` và `failed`: cảnh báo và nút xem Agent.
- Số công cụ chờ duyệt: liên kết tới `/tool-hub` thay vì cấp quyền âm thầm.
- Chỉ hiển thị 10 việc ưu tiên, tổng số vẫn chính xác theo các task tải trong 200 task gần nhất, đánh dấu nếu danh sách bị cắt. Worker offline mà có queued tasks thì có cảnh báo.
- Trạng thái SSE `/api/office3d/events` và snapshot hiện có là nguồn duy nhất: không sinh nhiệm vụ giả, không dùng file markdown hoặc nhật ký Claude/Codex để tự cho rằng Agent "đang làm".
- Phân cách workspace: không lấy task của doanh nghiệp khác. Không lộ API key ra frontend.

## Nghiệm thu và hạn chế
Tests khởi tạo SQLite thật để xác minh task awaiting approval/failed, hành động owner approve thật, tách workspace, giới hạn danh sách, route/UI và Node module syntax. Toàn bộ bộ test Linux/Docker/Computer Runner, Chrome/WebGL và Windows Electron/NSIS cần PASS trên branch rồi mới merge.

**Không bao gồm**: nhập hệ thống file hòm thư Node của repo tham khảo, nhận diện session Claude/Codex đang chạy, gửi tin nhắn bằng PIN từ xa, hoặc tự ghép tài khoản người dùng. Những thứ này cần cơ chế quyền riêng và không nên được bật mặc định.
