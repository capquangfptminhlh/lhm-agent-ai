# LHM Agent OS — Tool Hub V2 (Local Pilot)

## Tóm tắt tình trạng

Bản nâng cấp bổ sung màn hình `/tool-hub`, 66 đầu việc cho 16 Agent và 19 repo/công cụ đối chiếu. Có 3 **adapter HTTP đã viết**: Postiz, WordPress REST, Activepieces Webhook. Đây là mã giao tiếp API do LHM viết; **KHÔNG** phải đã cài tất cả mã nguồn của 19 dự án.

**Đã kiểm thử offline:** 31/31 bài kiểm thử Python; trình duyệt Chromium hiển thị danh mục 66 đầu mục, 19 card repo, form đăng bài khi nạp dữ liệu catalogue qua nội bộ. **Chưa** nghiệm thu thực tế với token và tài khoản các nền tảng; chưa xác minh một bài nào được xuất bản ngoài internet.

## Bắt đầu trên Windows

1. Giải nén ZIP, chạy `LAUNCH_ALL_WINDOWS.bat` để mở backend và AI worker. Cần Python 3.11+, máy có internet khi cài Python deps.
2. Mở `http://127.0.0.1:7770/tool-hub` hoặc vào **Văn phòng AI → Kho công cụ Agent**.
3. Xem **Danh mục công việc** để chọn vai trò và đầu mục; xem **Kho repo & kết nối** để biết adapter đã có và dịch vụ cần triển khai.
4. Mở `.env`, cấu hình key kết nối. **Không** gửi token cho người khác hoặc đưa token vào GitHub hay giao diện JavaScript.
5. Trong Tool Hub chọn **Đăng bài đa kênh** → **Kiểm tra kênh kết nối** → nhập caption và chọn kênh → tạo yêu cầu → duyệt → thực thi. Lưu ý lệnh thực thi ghi ra hệ thống bên ngoài nên cần kiểm tra kỹ nội dung trước khi bấm.
6. Kiểm tra **Lịch sử thực thi**. `accepted_unverified` nghĩa là phía API đã trả lời, **không có nghĩa bài đã hiện công khai**. Mở Postiz/WordPress để đối soát.

## Biến môi trường

Xem file `.env.example`:

```dotenv
# DeepSeek: dùng chung 16 vai trò AI
DEEPSEEK_API_KEY=

# Chỉ cho phép 1 doanh nghiệp dùng bộ token ngoài trong pilot này.
LHM_TOOLS_WORKSPACE=demo

# Postiz
LHM_POSTIZ_API_KEY=
LHM_POSTIZ_BASE_URL=https://api.postiz.com/public/v1

# WordPress: mật khẩu ứng dụng (Application Passwords), dùng HTTPS
LHM_WP_URL=
LHM_WP_USER=
LHM_WP_APP_PASSWORD=

# Activepieces: chỉ một flow có webhook hợp lệ và HTTPS
LHM_ACTIVEPIECES_WEBHOOK_URL=
```

**Postiz API:** `GET /integrations`, `POST /posts`, Authorization header mang API key. API hỗ trợ nháp (`draft`), gửi ngay (`now`), hoặc lịch (`schedule`). Tùy nền tảng có yêu cầu settings/media khác nhau. Với Instagram phải upload ảnh/video qua Postiz trước và điền `id` + `path` vào JSON media; với TikTok/YouTube còn yêu cầu cấu hình nâng cao `settings_by_channel` qua API. UI hiện hỗ trợ tốt nhất các bài văn bản Facebook Page, LinkedIn, Threads... chưa có giao diện upload media trực tiếp.

**WordPress:** tạo bản ghi qua `POST /wp-json/wp/v2/posts` (REST API). Nhà cung cấp trả ID, URL và status; pilot lưu `provider_record_created` chứ không khẳng định URL truy cập công khai ngay lúc nhận phản hồi.

**Activepieces:** chỉ gọi URL webhook đã cấu hình tại server, không nhận URL tuỳ ý từ prompt của Agent. HTTP 200 có nghĩa flow đã nhận trigger, không đồng nghĩa bước cuối hoàn thành.

## Workflow nối Agent ra công cụ

1. Trong **Văn phòng AI** giao việc cho Agent `content` để tạo bản nháp nội dung.
2. Bật AI worker và DeepSeek key; Agent phải đạt `completed` thật.
3. Mở Tool Hub → Đăng bài → **Tải kết quả AI** → chọn bản nháp.
4. Kiểm tra và biên tập lại nội dung cho kênh muốn đăng.
5. Tạo `tool_job`, trạng thái mặc định `awaiting_approval`.
6. Chủ doanh nghiệp duyệt và bấm `Thực thi qua API`.
7. Kiểm tra biên nhận. Nếu timeout, trạng thái `needs_reconciliation` và không được tự gửi lại.

## An toàn/giới hạn sản phẩm

- Chỉ chạy local `127.0.0.1`, không được mở công khai lên internet để làm SaaS ngay. Đã hạn chế token ngoài trong đúng `LHM_TOOLS_WORKSPACE`, mặc định `demo`.
- Chưa có OAuth/credential vault riêng theo khách hàng, RBAC đa người dùng, CSRF/SSO, encrypted secrets at rest, workflow ký/duyệt nhiều cấp, webhook callback xác nhận xuất bản cuối cùng.
- Không tự cào số điện thoại cá nhân, quét nhóm kín, tự nhắn Zalo ngoài quyền OA hoặc chạy Ads khi chưa có quyền hợp lệ.
- Các repo AGPL/GPL có nghĩa vụ giấy phép. Nếu thương mại, đánh giá từng repo và triển khai độc lập qua API khi phù hợp, không tùy tiện copy mã nguồn vào phần mềm đóng.
- DeepSeek là bộ não duy nhất trong MVP, nhưng **công cụ ngoài vẫn cần tài khoản và API/OAuth tương ứng**. Một DeepSeek API key không thể thay thế token mạng xã hội hoặc tài khoản quảng cáo.

## API mới

| Endpoint | Chức năng |
|---|---|
| `GET /tools/catalog` | 66 đầu việc, 19 repo, trạng thái cấu hình |
| `GET /tools/postiz/channels?workspace_id=demo` | Lấy kênh live từ Postiz |
| `GET /workspaces/demo/tools/ready-tasks` | Đầu ra Agent đã thực sự hoàn thành |
| `POST /workspaces/demo/tool-jobs` | Tạo yêu cầu bên ngoài; chống trùng theo idempotency key |
| `GET /workspaces/demo/tool-jobs` | Lịch sử và biên nhận |
| `POST /tool-jobs/{id}/approve` | Chủ doanh nghiệp duyệt |
| `POST /tool-jobs/{id}/execute` | Thực thi đúng một lần, không retry mù |
| `GET /api/office/snapshot` | Cả trạng thái AI task và số tool jobs chờ duyệt |

## Kiểm thử

```bash
python -m unittest discover -s tests -v
```

31/31 PASS trong môi trường test offline, provider request được giả lập bằng mock; không có lệnh ghi bên ngoài thật khi chạy tests. Kết quả có trong `TEST_REPORT_TOOL_HUB.txt`.

## Lộ trình tiếp theo

P1: thêm Twenty CRM, Chatwoot, Crawl4AI, Google Ads read-only, Meta Ads read-only, Google Search Console, Langfuse. P2: ERPNext, HRMS, Mautic, ComfyUI, Metabase. Chỉ cài service theo ngành dùng thực tế để giảm RAM, CPU và bảo trì. Để đưa lên thương mại, thay SQLite bằng PostgreSQL, dùng credential vault multi-tenant, HTTPS, RBAC, backup, rate limit và CI E2E với tài khoản sandbox của từng nhà cung cấp.

## Nguồn chính

- Postiz API: https://docs.postiz.com/public-api/introduction
- Postiz Create Post: https://docs.postiz.com/public-api/posts/create
- Activepieces: https://github.com/activepieces/activepieces
- Crawl4AI: https://github.com/unclecode/crawl4ai
- Hermes3D custom runtime: https://github.com/iamlukethedev/Hermes3D/blob/main/docs/runtime-profiles.md
- Toàn bộ ma trận: `AGENT_TOOL_AUDIT.md`