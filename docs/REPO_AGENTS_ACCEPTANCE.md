# Nghiệm thu Repo Agent chuyên dụng (Windows, 28-10-2026)

## Phạm vi được triển khai

Hệ thống có **16 vai trò chuyên môn** được quản lý bởi `agent_work_orders.py`
và **9 Agent dự án** được ràng buộc mỗi Agent vào **một repository duy nhất**
trong `repo_agent_bindings.py`. Đây là **hai nhóm khác nhau**; không được
gộp thành lời tuyên bố rằng 16 Agent có quyền tự sửa 16 repository.

| Agent | Repository | Nhiệm vụ cố định |
|---|---|---|
| agent-platform | capquangfptminhlh/lhm-agent-ai | Độ ổn định CI nền tảng |
| agent-seo-crm | capquangfptminhlh/crmminh | Pipeline SEO CRM |
| agent-autotax | capquangfptminhlh/autotax | Quality gates AutoTax |
| agent-browser | capquangfptminhlh/ggminh | Build Windows LHM Browser |
| agent-ads | capquangfptminhlh/lhm-ads-ai | CI LHM Ads AI |
| agent-pickle | capquangfptminhlh/pickle | CI PickleUp |
| agent-cnlg | capquangfptminhlh/cnlg | CI CNLG |
| agent-english-game | capquangfptminhlh/tienganh | CI game tiếng Anh |
| agent-zalo-crm | capquangfptminhlh/zalo-crm-frontend | CI Zalo CRM |

Không nhận tên repository, GitHub URL, hoặc agent role do LLM tự chọn.
Các API GitHub là read-only; nguồn việc/nhật ký được lưu SQLite và Worker
của LHM thực thi công việc viết báo cáo.

## Thao tác thực tế

1. Trong LHM Robot mở `/repo-agents/ui`.
2. Chọn repo và **Kiểm tra CI thật** để xem các run trên nhánh `main`, job và step lỗi
   có được GitHub API trả về, cùng trạng thái độ đầy đủ của bằng chứng.
3. Chọn **Chẩn đoán bằng AI**: yêu cầu được xếp hàng, ràng buộc đúng repo,
   đúng run và dedupe theo workspace + agent + run ID.
4. Worker cần chạy, có API key model phù hợp và quota hợp lệ.
5. Chọn **Xem báo cáo**: xem `queued`/`completed`/`failed`, thông tin
   readiness, lỗi và bản nháp kết quả nếu Worker thực sự hoàn tất.
   Kết quả được đọc từ CSDL; không dựng trạng thái thành công giả.

Nếu repo private, backend cần `LHM_GITHUB_READ_TOKEN` có quyền đọc Actions
phù hợp. Không nhập token vào giao diện. Không có quyền đọc job thì
`job_details_verified=false`; >100 job hoặc kết quả phân trang chưa đủ
thì `job_details_complete=false`. Không suy diễn “toàn bộ CI PASS” từ
danh sách thiếu dữ liệu. Khi cần, mở link run GitHub để xác minh.

## Những gì KHÔNG được tuyên bố là hoàn thành

- Chưa có GitHub write adapter để tự sửa code, tạo PR, merge, release.
  Báo cáo AI chỉ đề xuất lỗi và bản vá; không tạo ra bất kỳ code commit nào.
- Chưa nghiệm thu từng tài khoản repo private nếu chủ máy chưa cấu hình
  read token tương ứng.
- Chưa có kết quả live đầy đủ cho 16 vai trò, kết nối các nhà cung cấp
  chuyên môn, hay quyền tenant isolation thương mại.
- Windows build + Chrome/Playwright CI không thay cho kiểm tra
  đăng nhập/click/fill Chrome thật của chủ máy.

## Kiểm thử bắt buộc

- `python -m unittest discover -s tests -v`
  (GitHub MockTransport, đúng nguồn repo, lỗi API, duyệt không gian làm việc,
  task Worker thật với LLM mock, báo cáo/độ sẵn sàng, JavaScript syntax).
- `LHM Agent OS Tests`: unit / Computer sidecar / Chrome-WebGL.
- `Windows Electron Robot Desktop`: đóng gói backend, OpenCLI và GUI.
- Trên Windows thật: đọc CI một repo public và một repo private đã cấp quyền;
  xem báo cáo từ Worker; kiểm tra request không ghi code/PR lên GitHub.

Mọi kết luận phát hành thương mại toàn bộ 16 Agent vẫn phải dựa trên
kiểm chứng nhà cung cấp và hiệu lực công việc, không chỉ PASS CI.
