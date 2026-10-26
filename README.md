# LHM Agent Operating System — Open-source MVP

**License: MIT | Visibility target: Public | GitHub: capquangfptminhlh/lhm-agent-ai**

This is an experimental local MVP, not a production-ready SaaS. See [`SECURITY.md`](SECURITY.md), [`CONTRIBUTING.md`](CONTRIBUTING.md), and [`PUBLIC_REPOSITORY_SETUP.md`](PUBLIC_REPOSITORY_SETUP.md) before sharing, deploying, or modifying.

# Bản nâng cấp Tool Hub V2 — đọc trước

**Đã thêm:** `/tool-hub`, 66 đầu mục công việc Agent, 19 repo/công cụ được rà soát, 3 adapter Postiz/WordPress/Activepieces có phê duyệt, chống trùng và biên nhận. Xem `TOOL_HUB_README.md` và `AGENT_TOOL_AUDIT.md`. Adapter đã viết không có nghĩa tài khoản bên ngoài đã được kết nối hoặc bài viết đã đăng.

# LHM Agent Operating System — bản tích hợp phần mềm và văn phòng AI

**Trạng thái:** MVP chạy local, có backend thật, màn hình văn phòng 16 nhân sự AI và dashboard quản lý dùng chung một cơ sở dữ liệu. Có hỗ trợ giao thức `custom runtime` của Hermes3D; **chưa đóng gói chính repo 3D Hermes3D** vào ZIP, vì repo đó được tải lúc cài đặt trên máy có internet. Chưa phải SaaS thương mại.

## Đã tích hợp, có thể chạy ngay

- `/office` — giao diện văn phòng trực quan: 8 phòng ban, 16 avatar chibi gắn với nhiệm vụ thật từ cơ sở dữ liệu. Click vào nhân vật để xem vai trò, trạng thái, nhiệm vụ, lịch sử và giao việc mới.
- `/dashboard` — quản trị doanh nghiệp đa ngành, lập kế hoạch, 16 vai trò, 8 quy trình, xem đầu ra và quản lý ngân sách.
- Một backend FastAPI và cùng một SQLite: phòng ban, workflow, hàng đợi, kiểm duyệt, duyệt trước khi chạy, retry thủ công, sự kiện và định mức chi phí.
- `/api/office/snapshot?workspace_id=demo` — trạng thái thống nhất, cập nhật trên giao diện mỗi 5 giây; không tạo KPI/khách hàng giả.
- `worker.py` — làm việc độc lập ngay cả khi tắt giao diện (phải giữ tiến trình worker chạy); phát heartbeat để dashboard biết thực sự có worker hay không.
- DeepSeek chỉ được gọi khi task chạy, dùng chung một API key giữa các vai trò. Trạng thái `completed` chỉ khi có kết quả trả về và có `usage` hợp lệ.
- Chuyển đổi doanh nghiệp, giao việc cho nhân vật, duyệt, xem kết quả, tạo quy trình liên phòng, lead import có xác nhận nguồn, nhật ký và ngân sách.
- `/health`, `/state`, `/registry`, `/v1/chat/completions` theo interface HTTP Hermes3D. Thêm `/office` để không phải mở chương trình ngoài khi chỉ cần giám sát.

## Chạy trên Windows (dễ nhất)

1. Giải nén ZIP, chạy **`LAUNCH_ALL_WINDOWS.bat`**.
2. Mở `http://127.0.0.1:7770/office` để xem văn phòng AI tích hợp; `http://127.0.0.1:7770/dashboard` để quản trị.
3. Nếu muốn Agent sử dụng DeepSeek thật, mở file `.env` bằng Notepad và điền dòng `DEEPSEEK_API_KEY=...`, rồi **khởi động lại server và worker** (đóng cả hai cửa sổ, chạy `LAUNCH_ALL_WINDOWS.bat` lại). Không đưa key vào JS, GitHub hoặc trong cuộc chat.
4. Đến trang Văn phòng AI → bấm avatar nhân sự → **Giao việc** → nhập dữ liệu và mục tiêu. Nếu worker đã chạy và có key, nhiệm vụ sẽ được xử lý từ hàng đợi. Nếu không có key, nhiệm vụ sẽ ở trạng thái `queued` và báo rõ chưa xử lý.
5. Muốn tạo quy trình nhiều phòng ban: chọn **Quản lý công việc → + Quy trình liên phòng**. Những bước tiếp theo chỉ được chạy sau khi công đoạn trước hoàn thành.

*Yêu cầu*: Windows 10/11, Python 3.11+; internet cho lần đầu cài thư viện và DeepSeek API. Backend chỉ lắng nghe `127.0.0.1`, không mở trực tiếp lên internet.

### Chạy Linux/macOS

```bash
./START_LINUX_MAC.sh
# một terminal khác, cùng thư mục
.venv/bin/python worker.py
```

## Tùy chọn: Mở đúng văn phòng 3D Hermes3D

Repo nguồn MIT: https://github.com/iamlukethedev/Hermes3D

- Cài Git for Windows và Node.js 20+.
- Đảm bảo backend LHM đang chạy, sau đó chạy **`START_HERMES3D_WINDOWS.bat`**. Script sẽ clone Hermes3D vào thư mục con, chạy `npm install` rồi `npm run dev`.
- Trong Hermes3D `http://127.0.0.1:3000`, chọn **Custom backend** với URL `http://127.0.0.1:7770`.
- Mặc định Hermes3D dùng workspace cấu hình trong `.env`: `LHM_ACTIVE_WORKSPACE=demo` (đổi rồi khởi động lại để chọn doanh nghiệp khác).

**Giới hạn cần nói rõ:** Hermes3D hỗ trợ truy vấn vai trò và chat qua custom provider; upstream `custom` provider hiện chưa có luồng sự kiện runtime đầy đủ cho animation `working/error`. Vì vậy **giao diện `/office` của LHM là màn hình đã nối trạng thái thực**, còn phần 3D Hermes3D chưa được nghiệm thu đầu-cuối trong môi trường này. Đừng nhầm hiệu ứng di chuyển mặc định của Hermes3D với công việc đã thực hiện.

## 1 API cho nhiều phòng ban

8 phòng ban: Điều hành, Marketing, Kinh doanh & CRM, Vận hành & Mua hàng, Tài chính, Nhân sự, CSKH, Kiểm soát & QA. Tổng 16 vai trò AI, nhưng không phải 16 model/tiến trình riêng. Định tuyến vai trò lần đầu dùng quy tắc, không tốn token. Mỗi workflow được xử lý tuần tự và chỉ gọi AI cho các bước cần thiết.

## Bằng chứng kiểm thử

```bash
python -m unittest discover -s tests -v
```

**19/19 kiểm thử tự động PASS:** bao gồm 4 kiểm thử mới về web office, trạng thái từ task thật, cô lập dữ liệu doanh nghiệp và heartbeat worker; 15 kiểm thử lõi trước đây còn giữ nguyên.

**Kiểm thử trình duyệt Chromium qua API bridge:** 8 phòng ban, 16 agent, chọn avatar, mở form, tạo task lưu SQLite, không có pageerror; ảnh thật tại `office_preview.png`. Trình duyệt trong môi trường đóng này không được phép truy cập localhost trực tiếp nên thử qua FastAPI TestClient bridge, không phải chạy qua public URL.

**Chưa thực nghiệm:** API DeepSeek thật (không có key), trình duyệt Hermes3D 3D bên ngoài, cập nhật Ads hay đăng bài ngoài dịch vụ, đăng ký/SSO đa người dùng, chạy liên tục nhiều ngày.

## Điều chưa tích hợp vào hệ thống

Các Agent **mới tạo bản nháp và phân tích**. Chưa kết nối WordPress/Google Ads/Meta/Zalo OA để hành động bên ngoài, chưa có quyền tự tiêu tiền hay gửi tin, chưa tự tìm số điện thoại/cào dữ liệu. Không gắn nhãn “đã đăng/đã tìm khách” khi chưa có bằng chứng hoặc API được cấp phép. Không có model local yêu cầu GPU.

Production cần PostgreSQL, RBAC đa tenant, secrets manager, hard limit từ provider, HTTPS, backup, rate limiting, bảo mật event và quy trình nghiệm thu thực tế. Khuyến nghị chạy pilot một doanh nghiệp rồi mới mở dịch vụ SaaS.

## Tích hợp mã nguồn repo 3D

Tôn trọng giấy phép MIT của Hermes3D, giữ copyright nếu phân phối bản fork. Tham chiếu kỹ thuật: https://github.com/iamlukethedev/Hermes3D/blob/main/docs/integrations/custom-provider-reference.md .