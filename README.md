## LHM Agent Computer — điều khiển máy tính kiểu Codex (local pilot)

**Mới:** mỗi Agent có vùng file và phiên Chrome lưu bền vững trong
dịch vụ **Computer Runner** tách khỏi backend. AI dùng OpenRouter/DeepSeek
lập một bước thao tác, worker gửi tới runner, lưu kết quả rồi xem xét
bước tiếp theo. Có file list/read/write/edit, terminal `argv`,
Chrome Playwright (navigate, snapshot, click, fill, scroll, screenshot),
phê duyệt từng lệnh nguy hiểm, chặn chạy lại sau gián đoạn, báo cáo
trạng thái thực và giới hạn lượt AI để không phát sinh vòng lặp vô hạn.

**Windows:** cài Docker Desktop, tạo `.env`, thiết lập
`LHM_COMPUTER_RUNNER_TOKEN` ngẫu nhiên và danh sách
`LHM_COMPUTER_ALLOWED_HOSTS`, rồi chạy **`START_COMPUTER_WINDOWS.bat`**.
Mở `http://127.0.0.1:7770/computer`. Linux/Mac:
`docker compose --profile computer up --build -d`.

**Cần hiểu đúng:** đây là nền tảng Computer Runtime ban đầu,
**chưa tương đương đầy đủ OpenAI Codex**. Chưa có VM riêng cho mỗi Agent,
screen-vision model loop, git PR tự động trong Computer Runtime,
desktop Windows native, bộ plugin IDE/CLI, hay SaaS đa khách an toàn.
Mỗi Agent có browser profile và thư mục riêng nhưng **chia sẻ một
sidecar**, không phải ranh giới bảo mật giữa các doanh nghiệp. Chức năng
AI thật cần API key, web browser cần allowlist; tool có tác động
vẫn chờ phê duyệt thủ công.

Xem **[COMPUTER_RUNTIME.md](COMPUTER_RUNTIME.md)** để thiết lập, tài liệu
luồng, quyền và danh sách hạng mục cần nghiệm thu trên máy thật.

---

### Gắn Tool với nhiệm vụ Agent thực tế

Màn hình `/agent-tools` cho phép chọn **nhiệm vụ Agent đã hoàn thành** ở
ô “Nhiệm vụ Agent nguồn” trước khi tạo lệnh. Backend có route
`POST /workspaces/{ws}/tool-jobs/from-specialist-agent` nhận
`source_task_id`, `action`, `payload`, `idempotency_key`.
Nhiệm vụ phải `completed` và có kết quả; LHM lưu digest SHA-256 của
kết quả gốc. Khi **duyệt hoặc thực thi**, nếu kết quả nhiệm vụ bị sửa,
hệ thống trả HTTP 409 và chặn gọi API.

Quyền công cụ mặc định: Research/Strategy/Quality/Compliance dùng
Crawl4AI; Lead được Crawl4AI và Twenty; Sales dùng Twenty; CRM dùng
Twenty và Chatwoot; Support dùng Chatwoot; Content/Ads dùng ComfyUI.
Các vai trò còn lại chưa có công cụ chuyên ngành được cài. Tác vụ phát
sinh từ Agent **không tự đăng/ghi hoặc tự chạy GPU**: vẫn phải duyệt
trước khi worker gửi API.

## 4 công cụ chuyên môn đã có adapter thật

Truy cập **`/agent-tools`** để giao việc cho bốn dịch vụ chuyên dụng:

| Công cụ | Action | Nhận đầu vào | Biên nhận |
|---|---|---|---|
| Crawl4AI | `crawl4ai.crawl` | URL HTTPS đúng danh sách cho phép | Markdown có URL nguồn (tối đa 12.000 ký tự) |
| Twenty CRM | `twenty.create_person` | Họ, tên, email đã có quyền sử dụng | ID Person được API trả về |
| Chatwoot | `chatwoot.create_contact` | Tên, email được phép sử dụng | ID Contact |
| ComfyUI | `comfyui.generate_image` | Prompt, kích thước/steps giới hạn | `prompt_id` **đã xếp hàng, chưa phải file ảnh** |

Agent OS lưu `tool_job` trong SQLite, chờ người quản trị duyệt, worker
gọi API, rồi lưu biên nhận. Không kết nối tài khoản thật thì không gọi API,
không báo thành công giả. Các phần mềm trên **là dịch vụ ngoài**, LHM chỉ
cài adapter, không tự tải/cài tất cả repo.

Cấu hình ở `.env` (xem `.env.example`):

- `LHM_CRAWL4AI_MODE=selfhost|cloud`, `LHM_CRAWL4AI_URL` (selfhost),
  `LHM_CRAWL4AI_TOKEN`, **`LHM_CRAWL_ALLOWED_HOSTS`** (bắt buộc, danh sách
  tên miền HTTPS đúng bằng tên được phép đọc).
- `LHM_TWENTY_URL`, `LHM_TWENTY_API_KEY`.
- `LHM_CHATWOOT_URL`, `LHM_CHATWOOT_API_TOKEN`,
  `LHM_CHATWOOT_ACCOUNT_ID`.
- `LHM_COMFYUI_URL`, `LHM_COMFYUI_CHECKPOINT`
  (tên file checkpoint đã cài trên ComfyUI).

`GET /tools/specialists/readiness` chỉ cho biết biến môi trường
có/không; **không xác nhận kết nối và quyền API live**.
`GET /tools/comfyui/jobs/{prompt_id}` kiểm tra trạng thái/ảnh từ
ComfyUI cho prompt đã được backend ghi nhận thuộc workspace pilot.

**Bảo mật và giới hạn:** Không nhận tùy ý Python/JavaScript, custom graph,
crawler hooks hoặc token qua JSON. URL trình thu thập phải có trong
allowlist do admin đặt; chặn IP literal, localhost, scheme ngoài HTTPS.
Cần đặt giới hạn egress và DNS/redirect **bên phía Crawl4AI** để chặn
SSRF qua redirect. Mỗi tool job phải được người dùng phê duyệt, kể cả
research; API key chỉ ở server. Đây là **pilot một workspace**, chưa có
OAuth/Vault đa doanh nghiệp. Các bài test CI là offline/mocked; việc
khai báo adapter **không chứng minh** bốn dịch vụ đã kết nối thật.

Tài liệu API:
- [Crawl4AI self-hosting](https://docs.crawl4ai.com/core/self-hosting/)
- [Twenty REST](https://docs.twenty.com/developers/extend/api)
- [Chatwoot Application API](https://www.chatwoot.com/developers/api/)
- [ComfyUI Server](https://docs.comfy.org/development/comfyui-server/comms_messages)

---

## Văn phòng 3D Live — đã có giao diện native Three.js

**Mở `/office3d`**: 16 nhân vật chibi trong 8 phòng ban, camera 3D
tương tác, màn hình bàn chỉ sáng khi Agent thật đang chạy, click nhân viên
để tạo nhiệm vụ, theo dõi worker/Tool Hub và sự kiện SQLite bằng SSE.
Không phải demo 3D mô phỏng. Chế độ `/office` 2D vẫn hoạt động.

Các module 3D tự phát triển, chỉ tham khảo ý tưởng event-driven của
`lucwj/ai-agent-3d-prompts`; repo nguồn được dẫn link nhưng **không chứa
mã nguồn ứng dụng 3D để tích hợp trực tiếp**. Trình duyệt phải có WebGL
và tải Three.js qua CDN; thiếu WebGL/CDN sẽ hướng người dùng về `/office`.
**Xem [OFFICE3D_README.md](OFFICE3D_README.md)** để biết contract API,
giới hạn, nghiệm thu trên Windows/mobile và lưu ý bảo mật.

---

## Upload ảnh/video trực tiếp lên Postiz (pilot)

Trong `/tool-hub`, tab **Đăng bài đa kênh**, chọn ảnh/video rồi bấm **Upload vào Postiz**.
LHM gửi file qua API chính thức `POST /public/v1/upload`, không lưu media vào
GitHub và không coi upload là bài đăng. Khi Postiz trả về `id` và `path` HTTPS,
giao diện thêm chúng vào trường `media` của yêu cầu xuất bản.

- Chỉ cho phép JPEG, PNG, WEBP, GIF, MP4, MOV; tối đa 25 MB; kiểm tra phần mở
  rộng, MIME type và các byte đầu định dạng file.
- Chỉ hoạt động ở `LHM_TOOLS_WORKSPACE` với `LHM_POSTIZ_API_KEY` đặt trong
  `.env`, không gửi khóa qua trình duyệt.
- Sau khi có media ID, vẫn phải tạo yêu cầu đăng, qua QA nếu nguồn là Agent,
  rồi **duyệt riêng** trước khi worker gửi bài đến social.
- Tệp lớn (video dài), cài đặt YouTube/TikTok và các nền tảng yêu cầu đặc thù
  cần hoàn thiện theo quyền API của tài khoản; không có cam kết đăng thành công.
- Adapter có kiểm thử offline; **chưa nghiệm thu với tài khoản Postiz thật**.

## Workflow Builder kéo thả (Pilot)

Truy cập **`/workflow-builder`** từ Dashboard hoặc Tool Hub. Đây là
một công cụ lập quy trình *chạy thật qua backend*, không phải sơ đồ demo.

1. Chọn doanh nghiệp, tạo các node Agent; kéo thả hoặc dùng nút ↑/↓ để đổi thứ tự.
2. Lưu workflow. Mỗi lần chạy sẽ snapshot cấu hình và tạo các tác vụ SQLite
   nối tiếp (chỉ chạy bước sau khi bước trước hoàn tất).
3. Chạy worker riêng (`python worker.py`) để AI nhận việc trong khi đóng trình duyệt.
   Giao diện theo dõi trạng thái, lỗi và kết quả từ các task thực tế.
4. Tùy chọn bước cuối **Tool Postiz hoặc WordPress** để chuẩn bị xuất bản.
   Cấu trúc bắt buộc là **Content → Quality → Tool**.
5. Quality Agent phải trả về JSON chứa `{"verdict":"pass"}`, và hệ thống
   phải ghi được review khớp SHA-256 của đầu ra Content. Nếu QA fail hoặc
   không hiểu verdict, workflow dừng ở `review_required`.
6. Sau QA đạt, hệ thống **chỉ tạo yêu cầu Tool Hub ở trạng thái
   `awaiting_approval`**. Con người phải duyệt bằng nút trong Workflow
   Builder/Tool Hub; worker mới được gọi API bên ngoài. Yêu cầu có khóa
   idempotency và biên nhận; API nhận yêu cầu không được xem là bài đã công khai.

**Phạm vi hiện tại:** 2–8 Agent tuần tự, tối đa một Tool ở cuối; chưa hỗ trợ
DAG rẽ nhánh, trigger lịch tự động, media uploader, tài khoản OAuth nhiều doanh
nghiệp hoặc luồng thao tác social không cần Postiz. Publisher chỉ dùng workspace
pilot cấu hình bằng `LHM_TOOLS_WORKSPACE`. Nhớ cấu hình API key riêng cho
model và tool. Không có key thì task giữ trạng thái chờ, không sinh kết quả giả.

Chạy `python -m unittest discover -s tests -v` để kiểm tra luồng
Agent → QA → chờ duyệt Tool qua provider mock. **Không có bài thử live
xuất bản ra tài khoản xã hội thật trong CI.**

---

# LHM Agent Operating System — Open-source MVP

**License: MIT | Public repository | GitHub: capquangfptminhlh/lhm-agent-ai**

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

**31/31 kiểm thử tự động PASS (local):** bao gồm 4 kiểm thử mới về web office, trạng thái từ task thật, cô lập dữ liệu doanh nghiệp và heartbeat worker; 15 kiểm thử lõi trước đây còn giữ nguyên.

**Kiểm thử trình duyệt Chromium qua API bridge:** 8 phòng ban, 16 agent, chọn avatar, mở form, tạo task lưu SQLite, không có pageerror; ảnh kiểm thử được cung cấp trong bản đóng gói demo riêng (không commit tệp ảnh vào mã nguồn). Trình duyệt trong môi trường đóng này không được phép truy cập localhost trực tiếp nên thử qua FastAPI TestClient bridge, không phải chạy qua public URL.

**Chưa thực nghiệm:** API DeepSeek thật (không có key), trình duyệt Hermes3D 3D bên ngoài, cập nhật Ads hay đăng bài ngoài dịch vụ, đăng ký/SSO đa người dùng, chạy liên tục nhiều ngày.

## Điều chưa tích hợp vào hệ thống

Các Agent **mới tạo bản nháp và phân tích**. Chưa kết nối WordPress/Google Ads/Meta/Zalo OA để hành động bên ngoài, chưa có quyền tự tiêu tiền hay gửi tin, chưa tự tìm số điện thoại/cào dữ liệu. Không gắn nhãn “đã đăng/đã tìm khách” khi chưa có bằng chứng hoặc API được cấp phép. Không có model local yêu cầu GPU.

Production cần PostgreSQL, RBAC đa tenant, secrets manager, hard limit từ provider, HTTPS, backup, rate limiting, bảo mật event và quy trình nghiệm thu thực tế. Khuyến nghị chạy pilot một doanh nghiệp rồi mới mở dịch vụ SaaS.

## Tích hợp mã nguồn repo 3D

Tôn trọng giấy phép MIT của Hermes3D, giữ copyright nếu phân phối bản fork. Tham chiếu kỹ thuật: https://github.com/iamlukethedev/Hermes3D/blob/main/docs/integrations/custom-provider-reference.md .
## Nâng cấp vận hành Tool Hub (2026-10-26)

Bản thử nghiệm hỗ trợ **trình duyệt quản trị có Basic Auth**, vẫn chấp nhận Bearer token cho API client. Khi đặt `LHM_ADMIN_TOKEN`, mở `/office` hoặc `/dashboard`, đăng nhập user **admin** với mật khẩu là token; **chỉ sử dụng HTTP trên localhost** hoặc HTTPS khi ở sau proxy/VPN.

Luồng vận hành: Agent tạo nội dung → xem nhiệm vụ hoàn thành → `POST /workspaces/demo/tool-jobs/from-agent` → **con người duyệt** → worker tự thực hiện yêu cầu đã duyệt qua Postiz / WordPress / Activepieces (nếu có credentials). Tool job không bao giờ tự động retry khi phản hồi nhà cung cấp không chắc chắn. `accepted_unverified` không phải là bằng chứng bài đã công khai.

Có thêm cấu hình [Docker Compose và hướng dẫn nghiệm thu](PRODUCTION_CHECKLIST.md). Chỉ phục vụ **local pilot**; **chưa** có OAuth/credential vault đa doanh nghiệp, xác minh post URL tự động, media uploader hay lớp xác thực SaaS production.


## OpenRouter Free-first (tùy chọn, không tự tính phí)

LHM hiện có hai đường AI: DeepSeek gốc và OpenRouter free qua API tương thích chat.
**Mặc định vẫn là DeepSeek** để không làm thay đổi các tác vụ cũ. Muốn dùng
OpenRouter cho các tác vụ *mới*, tạo `.env` từ `.env.example` và đặt:

```dotenv
LHM_LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=YOUR_OWN_KEY_HERE
OPENROUTER_MODEL=openrouter/free
LHM_OPENROUTER_24H_LIMIT=40
LHM_OPENROUTER_1M_LIMIT=15
```

Khởi động lại server và worker. Đọc `GET /health` và `GET /llm/providers`
để kiểm tra loại model, key đã cấu hình (chỉ có boolean), số lượt gọi *local*
trong 24 giờ và một phút. `GET /registry` trả về model đang dùng cho Hermes3D.
Có thể thay `OPENROUTER_MODEL` bằng **slug chính xác kết thúc `:free`**
mà OpenRouter đang cung cấp; không dùng model trả phí qua adapter này.

- **An toàn ngân sách:** không chuyển sang trả phí nếu free hết quota/429.
  Task được hẹn lại khi bộ đếm local chạm trần; HTTP 429 từ upstream sẽ
  báo lỗi rõ ràng để người quản trị xử lý. Các lượt gọi *thất bại* cũng được
  tính trong bộ đếm local. Adapter ghi chi phí token **$0** cho model free,
  không dùng đơn giá DeepSeek để tính sai.
- **Không phải lời hứa 24/7 miễn phí:** OpenRouter có giới hạn theo tài khoản
  và toàn bộ ứng dụng chung API key. Bộ đếm local không biết tổng số lần
  gọi từ các ứng dụng khác; sự sẵn có/model free thay đổi theo nhà cung cấp.
- **Dữ liệu:** free providers có chính sách lưu trữ/huấn luyện khác nhau;
  không gửi dữ liệu khách, email, số điện thoại, chứng từ hay bí mật doanh
  nghiệp tới model miễn phí. Bộ lọc mặc định chặn một số mẫu số điện thoại/
  email, **không đảm bảo phát hiện hết PII**. Chỉ đặt
  `LHM_OPENROUTER_ALLOW_CONTACT_DATA=1` sau khi đã đánh giá rủi ro và
  được phép xử lý dữ liệu.
- **Tác vụ quan trọng:** chọn DeepSeek hoặc nhà cung cấp có cam kết dữ liệu
  thích hợp, kiểm tra đầu ra bằng QA và người duyệt. Chế độ
  `complexity=high` hiện vẫn cần cấu hình DeepSeek và `LHM_ALLOW_PRO=1`.
- **Thiết lập không ảnh hưởng tác vụ cũ:** model lưu trên từng task khi tạo.
  Các task DeepSeek đã xếp hàng sẽ tiếp tục yêu cầu DeepSeek API key;
  không chuyển chúng ngầm sang một model free bất kỳ.

Tài liệu nhà cung cấp: https://openrouter.ai/collections/free-models ,
https://openrouter.ai/openrouter/free và https://openrouter.ai/pricing .
Đây là adapter đã kiểm thử offline với phản hồi mô phỏng, **chưa gọi thử
một tài khoản OpenRouter thật** và chưa chạy production tải lớn.
