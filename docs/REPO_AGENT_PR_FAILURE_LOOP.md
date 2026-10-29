# Agent đọc CI lỗi của Draft Pull Request và giao vòng chẩn đoán mới

Mục đích: khi Draft PR của một trong 9 repository cố định có bài test
**được GitHub xác minh là failure**, chủ máy có thể giao một Agent chuyên
trách phân tích lỗi lần tiếp theo. **Không tự sửa code, tự commit, chạy test
repo đích, merge hoặc deploy**. Báo cáo AI không phải bằng chứng lỗi đã sửa.

## Luồng thao tác

1. Mở `/repo-agents/ui`, chọn Agent → **Bản vá & PR nháp** →
   **Xem bản vá gần nhất**.
2. Nếu đã có Draft PR: **Kiểm tra CI của PR trên GitHub**. Chỉ sau khi
   backend quan sát lỗi thật và verdict `failed_observed` mới hiện nút
   **Giao Agent phân tích CI lỗi của PR**.
3. Người dùng đồng ý để Worker AI sử dụng quota của model; backend **đọc
   lại** GitHub PR/check-runs/status. Không tin kết quả cũ trên trình duyệt.
4. SQLite bảo đảm mỗi `workspace + repair_id + head_sha` chỉ có một
   job chẩn đoán. Bấm lại trả tác vụ cũ; commit mới có thể tạo vòng mới.
5. Worker xử lý nhiệm vụ bằng model đang có và báo cáo giả thuyết, file nên
   xem/test cần chạy. Nút **Xem báo cáo chẩn đoán PR** đọc trạng thái và
   kết quả Worker thực.

## API

- POST `/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/triage`
- GET `/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/triages`
- GET `/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/triages/{id}`

## Ranh giới và kiểm thử

- **Chỉ đọc** GitHub API bằng read token phía backend, không gửi repo tùy ý.
- Dùng PR đã đăng ký từ bản vá cục bộ, xác minh repo/head/base/commit SHA;
  không truy cập nếu sai workspace, Agent hoặc bản vá chưa đăng PR.
- Chỉ gửi tên check lỗi/legacy commit status được giới hạn tối đa 12 mỗi
  loại, không lấy log dài, secret, output hoặc source tự động.
- Mọi tên check là dữ liệu không tin cậy, không phải hướng dẫn cho model.
- GitHub API không truy cập được, PR đã đóng, pending, xanh hoặc thiếu
  chứng cứ → không tạo task.
- Báo cáo chẩn đoán không tự đưa thay đổi nào vào GitHub.
- Test dùng SQLite/FastAPI/Worker thật, mock ranh giới GitHub HTTP và LLM,
  kiểm chứng lỗi CI, worker hoàn thành, chống trùng, SHA đổi, quyền truy
  cập, thiếu chứng cứ, dữ liệu check ác ý và JavaScript.
- Giai đoạn sau mới là đề xuất sửa branch PR theo báo cáo mới và chạy
  test đích trong môi trường cô lập; không khẳng định đã triển khai.

Chủ máy vẫn phải xem diff và cho phép từng thay đổi trên GitHub.
