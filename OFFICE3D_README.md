# LHM Agent AI — Văn phòng 3D Live

**Truy cập:** `http://127.0.0.1:7770/office3d`

Đây là một **giao diện 3D thực sự của chính LHM Agent AI**. Không cần cài
Hermes3D ở máy/cổng khác. Văn phòng 2D tại `/office` vẫn được giữ làm
chế độ nhẹ và phương án dự phòng.

## Nguồn ý tưởng

Tham khảo các nguyên tắc trực quan hóa Agent trong dự án
[lucwj/ai-agent-3d-prompts](https://github.com/lucwj/ai-agent-3d-prompts):

- Render không thay đổi trạng thái công việc.
- Nhân vật, bàn làm việc và màn hình hiển thị tình trạng từ backend.
- Không dựng sự kiện thành công giả hoặc sinh demo task ngoài dữ liệu thật.
- Lựa chọn chất lượng thấp, fallback và hỗ trợ màn hình di động.

**Không copy prompt/ảnh/mã nguồn từ repo tham khảo.** Tại thời điểm xem,
upstream chỉ công bố một prompt xây dựng client và ảnh minh họa, không
công bố mã nguồn ứng dụng/server hoặc giấy phép nguồn mở riêng cho phần
prompt. Mã nguồn dưới `static/office3d.*` và `office3d_api.py` được
xây dựng độc lập trong repo LHM.

## Kiến trúc đã triển khai

```
LHM Agent OS — SQLite task/worker/tool records
                |
        office_api.office_snapshot
                |
        office3d_api.build_world_snapshot
                |
        +-- GET /api/office3d/snapshot?workspace_id=demo
        |
        +-- GET /api/office3d/events?workspace_id=demo
                        |
             SSE: event=snapshot on actual change
                        |
                Three.js scene.js
                        |
             8 rooms / 16 chibi avatars
```

### Contract

- `GET /office3d`: giao diện HTML, responsive.
- `GET /office3d/scene.js`: module ES của Three.js.
- `GET /office3d/scene.css`: giao diện.
- `GET /api/office3d/snapshot`: trạng thái đọc từ SQLite.
  Payload có `schema_version=1`, `mode=live_backend`,
  `workspace`, `departments`, `agents`, `tasks`, `events`,
  `worker`, `tool_summary`, `statuses`, `financials`.
- `GET /api/office3d/events`: Server-Sent Events. Event `snapshot`
  chỉ phát khi nội dung trạng thái thay đổi; comment heartbeat
  không được xem là hoạt động AI. Trình duyệt dự phòng bằng cách
  đọc snapshot định kỳ khi SSE không hoạt động.

### Quy tắc hoạt ảnh

| Trạng thái thật | 3D hiển thị |
|---|---|
| `idle` | Nhân vật đứng ở khu vực chờ, màn hình bàn không sáng |
| `queued` | Chờ việc, dấu màu vàng |
| `awaiting_approval` | Chờ người duyệt, dấu vàng |
| `running` | Di chuyển tới bàn, thao tác gõ máy, màn hình sáng |
| `completed` | Hiển thị đã hoàn tất; động tác mừng ngắn chỉ sau chuyển trạng thái thật |
| `failed`, `blocked_*` | Dấu màu đỏ, không giả lập công việc |

Click hoặc chọn nhân viên trong danh sách để xem nhiệm vụ và **giao việc
bằng API thật**. Form gọi `POST /workspaces/{ws}/tasks`, có khóa chống
trùng, ngữ cảnh và tùy chọn yêu cầu phê duyệt.

## Chạy thử

1. Khởi động ứng dụng bằng script Windows/Linux hiện có hoặc
   `docker compose up --build -d`.
2. Mở `http://127.0.0.1:7770/office3d`; nếu đã bật
   `LHM_ADMIN_TOKEN` thì dùng tài khoản Basic Auth `admin` cùng token.
3. Chọn workspace, click nhân viên, giao nhiệm vụ.
4. Để worker thực thi AI, cấu hình `OPENROUTER_API_KEY` hoặc
   `DEEPSEEK_API_KEY` theo file `.env.example` rồi khởi động lại.
5. Nhấn `Văn phòng 2D` nếu WebGL không hoạt động.

**Yêu cầu hiển thị 3D:** trình duyệt hỗ trợ WebGL và có internet để tải
`three@0.186.0` cùng OrbitControls qua CDN jsDelivr. Backend,
dashboard, worker và giao nhiệm vụ qua API không phụ thuộc Three.js.
Không có API key thì các nhân viên không tự chạy AI; trang vẫn hiển thị
đúng trạng thái chờ.

## Kiểm thử & giới hạn

- GitHub CI kiểm tra backend, test dữ liệu trạng thái từ SQLite,
  quyền truy cập workspace và Basic Auth, cú pháp module JavaScript,
  Docker image và smoke test endpoint `/office3d`.
- **Chưa có kiểm thử visual WebGL bằng GPU trình duyệt thực** trong CI.
  Chưa thể cam kết mức 60 fps trên mọi máy. Hỗ trợ nút **⚡ Nhẹ**,
  bỏ shadow và giảm pixel ratio trên thiết bị yếu.
- Giao diện hiện tạo và đọc việc qua API, không tự cài phần mềm hay
  kết nối tài khoản social. Việc đăng bài vẫn do Tool Hub có phê duyệt.
- SSE hiện dùng SQLite và polling nội bộ mỗi 3 giây; chưa phù hợp hàng
  nghìn trình duyệt đồng thời. Production SaaS cần multi-tenant auth,
  event bus, connection limits, PostgreSQL và monitoring.
- Không có **đồ họa 3D của Hermes3D upstream** được sao chép vào dự án.
  Đây là bản Three.js native LHM với 16 nhân viên.
