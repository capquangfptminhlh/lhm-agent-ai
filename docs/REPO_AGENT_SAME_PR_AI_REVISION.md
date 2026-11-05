# Vòng AI sửa tiếp chính Draft PR bị lỗi CI — có người duyệt

## Tác vụ thực hiện

Trong `/repo-agents/ui`: sau khi Agent báo cáo một PR bị lỗi, chọn
**AI đề xuất sửa tiếp cùng PR** → **Xem diff sửa PR của AI** →
**Duyệt commit vào cùng PR**. Không cần tạo thêm Pull Request.

### Ranh giới chức năng

1. Repository cố định từ một trong 9 Agent. Repair ID được ràng buộc với
   workspace, Agent, PR số và branch. **Chỉ sửa file gốc của chính PR**,
   không cho AI tự chọn file khác hay viết workflow/secrets.
2. Triage PR trước đó phải hoàn tất và đang gắn với commit PR đang lỗi.
   Mỗi đề xuất AI được kiểm tra `head_sha`, `blob_sha` và nội dung
   file trên branch **PR**, không đọc nhầm file của `main`.
3. File gửi model nhỏ hơn 8500 ký tự UTF-8, chặn mẫu credential; người
   dùng xác nhận trước khi gửi source + báo cáo CI đến model.
4. Worker thực gọi model theo quota đã cấu hình; trả duy nhất JSON
   `search,replace,explanation,tests`. Chỉ chấp nhận một đoạn gốc
   khớp đúng một lần, không tự chạy mã do model viết.
5. Giao diện hiển thị diff; **không tự ghi GitHub**. Chỉ khi chủ máy bấm
   duyệt và có local `LHM_ADMIN_TOKEN` + server
   `LHM_GITHUB_WRITE_TOKEN` mới được cập nhật cùng branch.
6. Ngay trước khi ghi, backend xác minh lại PR: phải mở, vẫn là draft,
   còn lỗi CI đã xác minh, đúng SHA đang xem và file gốc không đổi.
7. Backend dùng **Git Data API**: lấy original commit/tree, bảo toàn
   mode file 100644/100755, tạo blob/tree/commit có đúng **parent SHA**,
   rồi PATCH nhánh với `force=false`. Nếu nhánh đã đi tiếp thì
   non-fast-forward update bị từ chối, không ghi đè các commit mới.
   Nếu lỗi remote không rõ đã ghi thành công chưa, trạng thái
   `needs_reconciliation` và **không gửi lại tự động**.
8. Thành công chỉ có nghĩa commit mới đã được GitHub xác nhận trên
   cùng branch PR. **Không đồng nghĩa CI của commit mới PASS**,
   hoặc PR đã merge/deploy.

### API

- POST `/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/revisions`
- GET `.../revisions`
- GET `.../revisions/{revision_id}`
- POST `.../revisions/{revision_id}/approve`
- POST `.../revisions/{revision_id}/reject`

### Nghiệm thu

FastAPI/SQLite/Worker thực, chỉ GitHub HTTP và lời đáp model được
mô phỏng ở boundary. Kiểm chứng SHA chính xác, diff duy nhất, quyền
người dùng, không token, AI JSON sai, CI đã xanh, PR không còn draft,
đường dẫn bí mật, HEAD/blob thay đổi, chống trùng và thử lại
khi GitHub không chắc chắn. CI Linux + Docker + Chrome + Computer
Runner và Windows NSIS/GUI đều phải PASS trước merge.

**Chưa triển khai:** chạy unit tests của repo đích trong sandbox riêng,
tự sửa đa file, tự merge và nghiệm thu production. Phải kiểm tra lại
PR CI trên commit mới sau mỗi lần sửa.
