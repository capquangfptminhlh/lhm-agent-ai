# Nghiệm thu đội 16 Agent chuyên môn — Windows

## Bản chất tính năng
- Trang `/agent-work` có nút **Giao cả 16 Agent** và **Xem tiến độ 16 Agent**.
- Sau khi chủ máy xác nhận, API tạo 16 `agent_work_orders` và 16 task cùng một giao dịch SQLite; nếu một role lỗi, toàn bộ được rollback.
- Mỗi role giữ nguyên **một nhiệm vụ và tiêu chí bàn giao cố định**, không cấp quyền công cụ của vai trò khác.
- Worker xử lý lần lượt theo cấu hình concurrency (mặc định 1, tối đa 4) và giới hạn OpenRouter; **không gọi 16 model đồng thời**.
- Kết quả `drafts_ready_unverified` chỉ có nghĩa đủ 16 bản nháp AI, **không** có nghĩa đã đăng, gửi, chỉnh dữ liệu hay phát hành bản thương mại.
- Các tác vụ ngoại vi phải dùng Tool Hub, nhận phê duyệt và đối soát biên nhận nhà cung cấp.

## Luồng API

1. `POST /workspaces/demo/agent-fleets` với body:
```json
{"mission":"Phân tích kế hoạch kinh doanh dựa trên dữ liệu đã cấp",
 "context":"Dữ liệu không có mật khẩu hoặc token",
 "idempotency_key":"fleet-demo-20261028"}
```
2. API trả `id`, `role_count:16`, 16 `orders` và trạng thái thực.
3. `GET /workspaces/demo/agent-fleets/{id}` đọc tiến độ từ SQLite, không dùng dữ liệu mock ở giao diện.
4. `GET /workspaces/demo/agent-fleets` xem tối đa 20 đợt gần nhất.

Mã idempotency không được dùng lại với mục tiêu/dữ liệu khác.
Phạm vi workspace được kiểm tra trên từng request. Không có API nào trong luồng này ghi thông tin tới Google, Meta, Zalo hoặc dịch vụ bên ngoài.

## Điều kiện nghiệm thu
- Kiểm thử offline: 16 role duy nhất, giao dịch rollback, idempotency, worker thực nhận task + LLM mock, giới hạn quyền workspace, UI JavaScript syntax.
- Windows CI: đóng gói installer NSIS, kiểm tra 6 màn hình Electron và backend thực từ bundle.
- Trên máy thật cần chủ máy cấu hình model, cho Worker hoạt động, giao việc mẫu và xem đúng trạng thái tiến độ. Chưa có kiểm chứng 16 Agent với kết nối dữ liệu live hay tenant isolation thương mại.

## Lỗi UI Repo Agent đã khắc phục
Trước đây giao **chẩn đoán CI repo** trả về `repo diagnosis ID` nhưng UI nhầm đó là `agent_work_order ID` rồi gọi đường dẫn API không tồn tại, gây lỗi "không tìm thấy đơn việc".
Nay nút **Xem báo cáo Repo** dùng đúng `/workspaces/{ws}/repo-agents/{agent_id}/diagnoses` và không xóa lựa chọn Work Order chuyên môn.

Không kết luận phần mềm đã làm được tất cả công việc kinh doanh nếu nhà cung cấp chưa kết nối hoặc chưa có biên nhận công việc thực tế.
