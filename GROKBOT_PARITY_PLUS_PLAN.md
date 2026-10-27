# LHM Agent AI — GrokBot Parity+ (kế hoạch và quyết định chuẩn)

Nguồn chuẩn và nơi cập nhật tiến độ: [GitHub Issue #20](https://github.com/capquangfptminhlh/lhm-agent-ai/issues/20).

## Quyết định sản phẩm — chủ chốt ngày 2026-10-27
**Mục tiêu**: LHM Robot Desktop phải đạt trải nghiệm quan sát được ngang GrokBot rồi vượt trội; không coi “pilot điều khiển được máy” là nghiệm thu. Đo bằng kết quả chạy thực tế, không đoán kiến trúc riêng của GrokBot.

### 9 khoảng cách cần đóng
- [ ] **G1 Vision:** toàn desktop + cửa sổ có sự đồng ý rõ; kết hợp ảnh/UIA, grounding tọa độ click; kiểm chứng khả năng hiểu ảnh của model :free.
- [ ] **G2 Chuột:** trái/phải/double, kéo-thả, cuộn theo tọa độ, di chuyển mượt; kiểm soát desktop coordinate và foreground.
- [ ] **G3 Bàn phím:** Unicode, phím tắt Ctrl+S, Alt+Tab, Enter, Esc, mũi tên, giữ/xóa/sửa.
- [ ] **G4 Ứng dụng:** launcher riêng kiểm soát; cho phép chọn chế độ allowlist; vẫn chặn vùng nhạy cảm.
- [ ] **G5 Tự chủ:** Giám sát / Tin cậy / Tự động theo việc; phê duyệt thủ công bắt buộc với hành động nhạy cảm; checkpoint cho >25 bước.
- [ ] **G6 Vòng lặp:** observe(ảnh+UIA) → decide → act → verify ảnh sau thao tác; retry có giới hạn; không công bố hoàn thành thiếu bằng chứng.
- [ ] **G7 Mượt:** 1 hàng đợi desktop/chuột, 1–4 Agent suy luận song song; hiển thị người giữ chuột, trạng thái từng bước, latency.
- [ ] **G8 Model:** chỉ dùng OpenRouter :free/ router free verified; live HTTP 200, actual model, token, cost==0, vision benchmark thực.
- [ ] **G9 Release:** PR #19 Windows CI (6 GUI screens) PASS + merge; xác minh installer trên máy Windows thực của chủ.

### Khác biệt bắt buộc để đạt Parity+
- Chỉ Free được xác minh qua OpenRouter; không fallback trả phí, hết quota dừng; mỗi phiên báo model/token/phí thực tế (nếu API không báo phí phải ghi *chưa xác minh*, không tự nhận 0).
- Evidence per step: ảnh/log trước–sau, audit phát lại có kiểm soát quyền riêng tư.
- Emergency stop + vùng cấm: mật khẩu, thanh toán, xóa dữ liệu, gửi/đăng bên ngoài **luôn cần duyệt tay** ở mọi chế độ.
- 1–4 agent suy luận song song với **một** desktop action queue có người giữ chuột.
- Local-first, status tiếng Việt, ảnh chỉ gửi tới model vision được chủ bật cho phiên phù hợp.

### 10 bài nghiệm thu bắt buộc trên Windows đăng nhập của chủ — chế độ Tin cậy
| # | Việc thật |
|---|---|
| 1 | Mở Notepad, gõ tiếng Việt có dấu, lưu chỉ định, đóng |
| 2 | File Explorer: tạo thư mục, đổi tên, chuyển file |
| 3 | Browser: mở trang test, tìm kiếm, mở kết quả đầu |
| 4 | Form test: điền tên/email/nội dung, chụp bằng chứng **trước** khi gửi |
| 5 | Chuyển qua hai ứng dụng rồi quay lại việc dở |
| 6 | Calculator: tính bằng bàn phím + chuột |
| 7 | Trang dài: tìm mục và cuộn đúng chỗ |
| 8 | Kéo-thả file/icon từ A đến B |
| 9 | Tác vụ dài >=25 bước, có checkpoint, không mất trạng thái |
| 10 | Đóng app đích bất ngờ, phát hiện, thông báo, không click mò |

**Mỗi bài ghi:** PASS/FAIL, số lần can thiệp tay, tổng thời gian, số bước và link audit phát lại. Đạt Parity khi >=9/10 bài hoàn tất **và** bài #10 ứng xử đúng, can thiệp chỉ ở điểm nhạy cảm đã định, toàn bộ phiên có audit replay. Parity+ cần thêm cả 5 đặc tính trên. CI mock/headless không thay thế nghiệm thu máy chủ.

### Thứ tự thực hiện — không nhảy bước
- [x] **P0**: PR #19 Windows GUI 6 màn hình PASS và merge vào main; OpenRouter Live thật (`openrouter/free`) HTTP 200, model `dots-studio/dots-3-note-preview:free`, 22 input + 30 output tokens, cost reported USD 0. [Windows CI](https://github.com/capquangfptminhlh/lhm-agent-ai/actions/runs/40493477296) · [Live CI](https://github.com/capquangfptminhlh/lhm-agent-ai/actions/runs/40494293478). Bản Windows của chủ và 10 tác vụ vẫn CHƯA nghiệm thu.
- [ ] **P1**: G2 + G3 + G1 trong chế độ Giám sát. **Đang thực hiện:** [PR #21](https://github.com/capquangfptminhlh/lhm-agent-ai/pull/21) đã merge (3cad5fd), thêm right/double-click, hotkey allowlist và ảnh toàn desktop chỉ khi bật và duyệt tay; Ubuntu+Windows CI PASS. **Còn thiếu:** drag-drop, mouse movement/scroll tọa độ desktop, Alt+Tab, grounding ảnh và kiểm thử click thật trên Windows của chủ.
- [ ] **P2**: G5 chế độ Tin cậy và approved-plan; điểm nhạy cảm luôn được duyệt riêng.
- [ ] **P3**: G6 xác minh ảnh sau action, retry có giới hạn.
- [ ] **P4**: chạy 10 việc trên máy của chủ và báo đúng kết quả.

### Quyết định vận hành chính thức — 2026-10-27

**Đã chốt phương án mặc định:** `Tin cậy` cho tác vụ thông thường **sau khi P2 được triển khai và kiểm thử đạt đầy đủ các điều kiện an toàn**. Trước thời điểm đó, tiếp tục giữ `Giám sát` mặc định; không bật Tin cậy sớm và không bỏ khâu duyệt từng bước ở bản hiện tại.

- **Tin cậy (sau khi P2 PASS):** tự xử lý liên tục các thao tác rủi ro thấp trong phạm vi công việc, có hàng đợi desktop duy nhất và dấu vết thực thi. Người dùng luôn có thể chuyển lại Giám sát.
- **Luôn duyệt tay ở mọi chế độ:** nhập/đọc bí mật, mật khẩu, thanh toán, xóa dữ liệu, gửi tin nhắn/email, đăng nội dung hoặc gửi dữ liệu ra ngoài và mọi thao tác nhạy cảm tương đương; không tự phê duyệt bằng suy đoán của Agent.
- **Emergency Stop hoạt động ở mọi chế độ**, không thực hiện mutation mới sau khi dừng; hành động đang thực thi phải được ghi rõ tình trạng và báo nếu kết quả không chắc chắn.
- **Chỉ OpenRouter Free đã xác minh:** model hết quota/không còn miễn phí hoặc không xác minh được chi phí thì dừng hoặc chỉ thử model khác cũng đã xác minh Free; không fallback trả phí.
- **Ảnh màn hình và bằng chứng:** tuân thủ quyền bật/chọn phạm vi của chủ, local-first, audit trước/sau mỗi hành động với che thông tin nhạy cảm và quyền truy cập phù hợp; không báo hoàn thành nếu chưa verify.
- **Tự động theo việc** là chế độ riêng, không làm mặc định; vẫn cần nghiệm thu và có các điểm chặn nhạy cảm.
- **Cổng chấp nhận P2:** có test chứng minh tác vụ thường chạy liên tục, mọi hành động nhạy cảm luôn dừng xin phép, Emergency Stop chặn thao tác mới, log/audit đúng, restart không thực thi lặp hành động có tác dụng phụ. Chỉ sau khi cổng này PASS mới đổi default trong cấu hình sản phẩm.
- **Không đồng nghĩa đạt Parity+**: vẫn phải vượt 10 bài nghiệm thu trên máy Windows thật theo P4.

Quyết định này có hiệu lực cho triển khai tương lai; việc chốt yêu cầu **không** tự thay đổi cài đặt của bản phát hành hiện tại.

### Hiện trạng tại thời điểm mở issue
- Main mới chỉ có kiểm thử 50 kịch bản backend/policy và các PR OpenRouter (#16–#18).
- PR #19 đã merge vào main (e4b383f), Windows GUI CI đã PASS; đây là P0 về CI, **không** công nhận Parity trên máy chủ.
- OpenRouter đã có 1 request Live PASS với cost API báo cáo 0 USD; không thay thế phép thử thị giác, thao tác và 10 tác vụ thật.

