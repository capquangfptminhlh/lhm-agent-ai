# Tích hợp & tham chiếu bên ngoài — LHM Agent AI

Nguyên tắc: chỉ tích hợp thứ có license rõ ràng, kiểm chứng được bằng test
trong repo này, và không phá 3 luật cứng — **free-only** (chỉ OpenRouter
Free đã xác minh), **local-first**, **thao tác nhạy cảm luôn có người duyệt**.
Repo được khảo sát ngày 2026-10-27 cho đợt Parity+ (P1/P2/Office3D).

## Đã tích hợp thật

| Thành phần | Nguồn | License | Cách tích hợp |
|---|---|---|---|
| Three.js r186 + OrbitControls | three.js (`three@0.186.0`, jsDelivr) | MIT (bản license nằm tại `static/vendor/three/LICENSE`) | **Vendor local** tại `static/vendor/three/`; importmap của Văn phòng 3D trỏ về `/office3d/vendor/...` do chính backend phục vụ. Hết phụ thuộc CDN — trước đây trang 3D chết khi CDN không tải được. Realtime giữ nguyên cơ chế SSE `/api/office3d/events` (snapshot khi trạng thái DB thật thay đổi + heartbeat, polling dự phòng). |
| Playwright | Microsoft Playwright | Apache-2.0 | Đã dùng từ trước cho browser sidecar (mở/đọc/click/fill trong Chrome riêng của từng Agent). |

## Đã khảo sát — tham chiếu thiết kế, KHÔNG nhúng code

| Repo | Vì sao đáng học | Vì sao không tích hợp trực tiếp đợt này |
|---|---|---|
| microsoft/UFO (MIT) | Agent GUI cho Windows dùng UI Automation để grounding theo control thật thay vì đoán tọa độ; kiến trúc đa agent trên 1 máy. | Nó tự mang vòng lặp LLM + giả định model riêng, trùng và xung đột với vòng lặp computer + cổng duyệt + free-only của LHM. LHM đã có UIA snapshot (`windows_snapshot`) và nay có thêm `vision_locate`; học pattern, không thêm một runtime thứ hai. |
| trycua/cua (computer-use driver/sandbox) | Driver điều khiển desktop đa HĐH + sandbox VM, giao diện MCP/CLI. | Thêm một driver service ngoài đổi toàn bộ biên bảo mật của native runner hiện tại và chưa có bằng chứng chạy trên máy chủ sở hữu. Để dành đánh giá riêng nếu cần điều khiển macOS/Linux hoặc VM sandbox thật. |
| Agent-S / các GUI grounding model | Cách grounding bằng tọa độ chuẩn hóa 0–1000 từ ảnh chụp. | Cần host model grounding riêng — xung đột free-only. `vision_locate` của LHM dùng đúng quy ước tọa độ 0–1000 nhưng chạy trên OpenRouter `:free` vision model do chủ chọn và bật, kết quả là quan sát *chưa kiểm chứng*, không tự click. |

## Ranh giới trung thực

- Không repo ngoài nào được quảng cáo là "đã tích hợp" nếu chưa có code +
  test trong repo này. Bảng trên phân biệt rõ *integrated* và *reference*.
- Mọi năng lực mới ở đợt này (kéo-thả, Alt+Tab, tọa độ desktop, grounding,
  chế độ Tin cậy) đều là code của LHM trong `windows_native.py`,
  `computer_agent.py`, `robot_vision.py` — không sao chép code từ các repo
  tham chiếu.
- GrokBot là sản phẩm đóng; LHM không sao chép code/tài sản của GrokBot.
  "Giống GrokBot" chỉ nghĩa là ngang trải nghiệm quan sát được, đo bằng
  bộ 10 bài nghiệm thu trên máy Windows thật (Issue #20).
