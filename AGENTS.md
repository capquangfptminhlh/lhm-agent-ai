# AGENTS.md — Luật làm việc bắt buộc cho mọi AI Agent trong repo

> **Đọc trước khi sửa code, mở PR, viết test hoặc báo cáo hoàn thành.**
> Áp dụng cho toàn repository. Đây là checklist sản phẩm của chủ sở hữu, không phải lời gợi ý.
> Nguồn quyết định: [Issue #20](https://github.com/capquangfptminhlh/lhm-agent-ai/issues/20) · [Kế hoạch chủ duyệt](GROKBOT_PARITY_PLUS_PLAN.md) · [Bảng nghiệm thu](docs/PARITY_PLUS_SCORECARD.md).
> Nếu tài liệu mâu thuẫn, **quyết định của chủ trong Issue #20 + 10 việc gốc** là chuẩn; không tự hạ tiêu chí. Báo mâu thuẫn trong PR.

## 1. Định nghĩa hoàn thành

**Mục tiêu:** LHM Robot Desktop đạt trải nghiệm quan sát được ngang GrokBot và vượt hơn về Free-only, audit, an toàn, nhiều Agent, local-first. Trải nghiệm phải là: nhìn màn hình → suy luận → bấm/gõ/cuộn/kéo → quan sát lại → tự sửa khi thất bại → báo trạng thái liên tục.

**Tuyệt đối không đánh đồng**: có code, unit test PASS, CI headless PASS, 6 trang Electron mở được, một lượt API Free PASS **≠** GrokBot Parity hoặc Parity+.

Các mức bằng chứng bắt buộc báo riêng:
1. `CODED` — tính năng đã có mã nguồn trên nhánh/commit xác định.
2. `CI_PASS` — kiểm thử thật của tính năng trên workflow được liên kết (nêu mock hay real GUI).
3. `OWNER_WINDOWS_PASS` — thao tác trên máy Windows thật của chủ, bằng installer đã chỉ rõ SHA, có log/audit và bằng chứng.
4. `PARITY` — >= 9/10 việc P4 hoàn thành, **bài #10 bắt buộc xử lý đúng**, can thiệp tay chỉ ở điểm nhạy cảm, tất cả phiên có audit phát lại.
5. `PARITY_PLUS` — PARITY + đủ 5 điểm vượt trội ở mục 3.

Không báo “xong 100%”, “vượt GrokBot”, “thương mại sẵn sàng” nếu chưa có bằng chứng tương ứng.

## 2. Bảy điều KHÔNG được phá (security/product invariants)

1. **OpenRouter Free-only:** chỉ `openrouter/free` với kiểm chứng model thực tế hoặc model `:free` được xác thực trong catalog giá 0; hết quota, không xác minh được giá, lỗi API → dừng có thông báo, **không** fallback model trả phí. Mỗi phiên ghi model, token, chi phí API báo cáo; nếu không có cost thì ghi **chưa xác minh**.
2. **Hành động nhạy cảm luôn duyệt tay ở MỌI chế độ:** mật khẩu/secret, thanh toán, xóa dữ liệu, gửi email/tin nhắn, bấm Submit/Publish/Post, gửi thông tin ra ngoài, thay đổi bảo mật. Giao diện browser và mọi cửa sổ có thể gửi/đăng phải fail-closed: không được lách bằng `windows_click` / `windows_type` / hotkey thay cho `browser_click`.
3. **Emergency Stop thực:** chặn mọi mutation mới; kết quả hành động đang chạy/không chắc chắn phải báo rõ; không tự retry một hành động gây tác dụng phụ khi không rõ nó đã chạy hay chưa.
4. **Chế độ mặc định đã chốt:** hiện giữ `Giám sát`. Chỉ chuyển default sang **Tin cậy** sau khi P2 qua cổng an toàn và có bằng chứng; `Tự động theo việc` không mặc định.
5. **Một desktop action queue:** 1–4 Agent có thể suy luận đồng thời, nhưng chỉ một hàng đợi chuột/bàn phím trên Windows, hiện rõ Agent đang thao tác; có checkpoint cho việc dài.
6. **Local-first + quyền riêng tư:** trạng thái, nhật ký, file cục bộ; ảnh chỉ chuyển tới model vision Free được chủ bật và duyệt phạm vi, không log ảnh/secret bừa bãi. Audit trước/sau có redaction + quyền truy cập thích hợp.
7. **Không tự tuyên bố hoàn thành:** tách `completed_unverified` khỏi `verified`; so sánh kết quả với mục tiêu bằng bằng chứng; khi app mất foreground/đóng bất ngờ → dừng, thông báo, không click mò.

## 3. Thứ tự bắt buộc

| Gate | Nội dung | Khi nào được coi là đạt |
|---|---|---|
| P0 | EXE/installer + 6 màn hình Windows CI + OpenRouter Free Live HTTP 200, cost 0 | **Đạt ở tầng GitHub** (không thay thế máy chủ) |
| P1 | Chuột trái/phải/double, kéo-thả, cuộn/tọa độ desktop, phím tắt (Alt+Tab), capture desktop + UIA, grounding model Free | Đủ chức năng + test Win32 thực + quyền chụp/điều khiển an toàn |
| P2 | Giám sát / Tin cậy / Tự động theo việc; low-risk auto, high-risk manual; 25+ bước checkpoint; restart đúng | Tests cố tình thử lách duyệt, dừng khẩn cấp, khởi động lại không lặp mutation |
| P3 | Observe → Act → Verify với ảnh/UIA, trước/sau, retry có giới hạn, audit phát lại | Ảnh/log có bằng chứng thật; `ok: null` KHÔNG được tính như kiểm chứng thành công |
| P4 | 10 việc trên **máy Windows thật của chủ** | >=9/10 + bài #10 đạt + audit phát lại + số bước/thời gian/can thiệp |

Chi tiết 9 khoảng cách G1–G9 và 10 việc **không được tự đổi** ở [bảng nghiệm thu](docs/PARITY_PLUS_SCORECARD.md).

## 4. Quy trình làm việc của từng Agent

Trước khi làm: đọc Issue #20, bảng scorecard và `docs/P4-ACCEPTANCE.md`; xác định 1–2 gap đang làm, kiểm tra PR mở để tránh xung đột. Viết trong PR: *gap, bất biến liên quan, thay đổi mã, test chạy thật, log/run, còn thiếu*.

Trước khi merge: tối thiểu unit/integration + Windows build GUI CI phải xanh với HEAD mới nhất nếu đụng Windows/Agent/vision; test việc thật phải FAIL khi chức năng hỏng; không xóa/bỏ/đổi tên bài test để làm CI xanh. Chỉ ghi `OWNER_WINDOWS_PASS` sau khi thực sự có bằng chứng từ máy chủ.

Sau khi làm: cập nhật [scorecard](docs/PARITY_PLUS_SCORECARD.md) và [Issue #20](https://github.com/capquangfptminhlh/lhm-agent-ai/issues/20) bằng commit/run ID, trạng thái chính xác, blocker và bước tiếp theo. **Không nâng phần trăm dựa vào cảm giác.**

## 5. Trạng thái ban đầu ghi nhận (27/10/2026)

Main tại lúc lập bảng: `01e8a707` (PR #22 đã merge). P0 đã PASS tầng GitHub; P1/P2/P3 đã có code nhưng chưa nghiệm thu đủ các cổng an toàn; P4 **chưa có kết quả từ máy chủ**. PR #23 đang kiểm thử thêm GUI thực và chặn cửa gửi/đăng, chưa merge. Dữ liệu này là snapshot, không phải trạng thái động — kiểm tra GitHub mới nhất trước khi báo.
