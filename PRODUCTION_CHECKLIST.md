# LHM Agent OS — triển khai và nghiệm thu

## Chạy nội bộ bằng Docker

1. Sao chép `.env.example` thành `.env`, đặt `LHM_ADMIN_TOKEN` mạnh (ít nhất 24 ký tự ngẫu nhiên).
2. Chạy `docker compose up --build -d` — chỉ bind `127.0.0.1:7770`.
3. Mở `http://127.0.0.1:7770/office`: trình duyệt yêu cầu user **admin** và mật khẩu là giá trị `LHM_ADMIN_TOKEN`.
4. Thêm `DEEPSEEK_API_KEY` vào `.env` nếu muốn chạy Agent thật, rồi `docker compose up -d --force-recreate`.
5. Để thử thực thi, cấu hình Postiz/WordPress/Activepieces qua `.env` với tài khoản thử nghiệm.
6. Một Agent hoàn thành nội dung → `POST /workspaces/demo/tool-jobs/from-agent` → người vận hành duyệt → worker gửi một lần.
7. Xem receipt trong Tool Hub. **accepted_unverified không có nghĩa bài đã đăng công khai.**

## Giới hạn và an toàn sản xuất

- Bản này dành cho **local pilot**. Chưa có đăng nhập theo doanh nghiệp, RBAC, Vault, OAuth, cách ly tenant ở backend.
- Không mở cổng 7770 ra Internet. Truy cập từ xa qua VPN/Tailscale và reverse proxy có HTTPS.
- Worker chỉ xử lý các tool job đã được con người duyệt. Gặp timeout hoặc mất điện sau lúc gửi, job vào `needs_reconciliation`; xác minh bên ngoài trước khi làm lại.
- Giữ `.env` ngoài Git, xoay khóa nếu lộ; sao lưu volume `lhm_data`.
- Khi tiến tới thương mại nhiều khách hàng: PostgreSQL, distributed lease, Vault, OAuth, RBAC, rate limit, CSRF chuyên sâu, kiểm soát truy cập và audit chống sửa đổi là yêu cầu bắt buộc.

## Kiểm thử

`python -m unittest discover -s tests -v`

Chưa nghiệm thu: đăng social thực với token khách, Postiz readback, media upload, thanh toán SaaS, Hermes3D render đủ và tenant isolation production.
