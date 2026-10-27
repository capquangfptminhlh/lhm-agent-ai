# LHM Agent OS — triển khai và nghiệm thu

## Chạy nội bộ bằng Docker

1. Sao chép `.env.example` thành `.env`, đặt `LHM_ADMIN_TOKEN` mạnh (ít nhất 24 ký tự ngẫu nhiên).
2. Chạy `docker compose up --build -d` — chỉ bind `127.0.0.1:7770`.
3. Mở `http://127.0.0.1:7770/office`: trình duyệt yêu cầu user **admin** và mật khẩu là giá trị `LHM_ADMIN_TOKEN`.
4. Thêm `DEEPSEEK_API_KEY` vào `.env` nếu muốn chạy Agent thật, rồi `docker compose up -d --force-recreate`.
5. Để thử thực thi, cấu hình Postiz/WordPress/Activepieces qua `.env` với tài khoản thử nghiệm.
6. Một Agent hoàn thành nội dung → `POST /workspaces/demo/tool-jobs/from-agent` → người vận hành duyệt → worker gửi một lần.
7. Xem receipt trong Tool Hub. Với WordPress, bấm **Xác minh URL** để đối chiếu trạng thái `publish` trên WordPress REST API và HTTP 200 của URL công khai, mới ghi `verified_published`. **accepted_unverified không có nghĩa bài đã đăng công khai.**

## Giới hạn và an toàn sản xuất

- Bản này dành cho **local pilot**. Chưa có đăng nhập theo doanh nghiệp, RBAC, Vault, OAuth, cách ly tenant ở backend.
- Không mở cổng 7770 ra Internet. Truy cập từ xa qua VPN/Tailscale và reverse proxy có HTTPS.
- Worker chỉ xử lý các tool job đã được con người duyệt. Gặp timeout hoặc mất điện sau lúc gửi, job vào `needs_reconciliation`; xác minh bên ngoài trước khi làm lại.
- Giữ `.env` ngoài Git, xoay khóa nếu lộ; sao lưu volume `lhm_data`.
- Khi tiến tới thương mại nhiều khách hàng: PostgreSQL, distributed lease, Vault, OAuth, RBAC, rate limit, CSRF chuyên sâu, kiểm soát truy cập và audit chống sửa đổi là yêu cầu bắt buộc.

## Kiểm thử

`python -m unittest discover -s tests -v`

Chưa nghiệm thu: đăng social thực với token khách, Postiz readback, media upload, thanh toán SaaS, Hermes3D render đủ và tenant isolation production. WordPress HTTP 200 không đồng nghĩa đã được Google lập chỉ mục.

## Workflow Builder — kiểm thử vận hành

1. Khởi động bằng Docker Compose, xác minh `GET /health` và mở `/workflow-builder`.
2. Tạo template Research → Content → Quality → Postiz/WordPress; lưu và bắt đầu chạy.
3. Kiểm tra worker tạo 3 task SQLite, không bỏ qua phụ thuộc và không xuất bản khi QA chưa có verdict=`pass`.
4. Sau khi QA đạt, kiểm tra Tool Hub có **một** request `awaiting_approval`.
5. Chỉ sau khi người quản trị duyệt mới gọi API đích; kiểm tra trạng thái `accepted_unverified` và đối soát URL bên ngoài.
6. Khởi động lại worker để xác nhận không tạo lại cùng một yêu cầu tool.
7. Nếu API trả timeout sau khi gửi, yêu cầu phải ở trạng thái `needs_reconciliation`, tuyệt đối không tự lặp lại.
8. Kiểm tra khóa workspace: chỉ `LHM_TOOLS_WORKSPACE` có quyền sử dụng token Postiz/WordPress trong pilot.

GitHub CI giờ còn kiểm tra **Docker image khởi động thật**, không chỉ unittest.
Đây vẫn là local pilot, không phải SaaS an toàn để mở thẳng ra internet.

## Specialist Agent Tools — phát hành chưa đủ điều kiện

- [ ] Xác minh trực tiếp Crawl4AI 0.9+ local/token hoặc Crawl4AI Cloud; chỉ
  dùng các domain HTTPS đã cho phép, kiểm tra redirect, DNS và egress phía crawler
  để tránh SSRF; chưa kết nối crawler vào internet public tùy ý.
- [ ] Twenty CRM: tạo thử Person trong workspace sandbox, kiểm tra id,
  phân quyền API key và chống trùng email theo chính sách CRM.
- [ ] Chatwoot: tạo Contact trong account thử, kiểm tra nguồn dữ liệu và
  quyền thao tác; không gửi hội thoại chưa được đồng ý.
- [ ] ComfyUI: kiểm thử checkpoint phù hợp với 7 node chuẩn, giới hạn GPU,
  `prompt_id` và xác minh file ảnh qua `/history/{prompt_id}`.
- [ ] Kiểm tra luồng Agent source `completed` → tool job → human approval →
  worker dispatch → receipt; chỉnh sửa source phải chặn.
- [ ] Thiết kế OAuth/Vault/RBAC riêng từng doanh nghiệp, egress policy, audit
  không ghi PII, backup và metrics trước khi chuyển khỏi workspace pilot.
- [ ] Không mở cổng ComfyUI/Crawl4AI tự do ngoài internet; qua proxy
  có xác thực và rate limiting nếu bắt buộc truy cập từ xa.

Các bài CI dùng mock provider và không được coi là live acceptance.
