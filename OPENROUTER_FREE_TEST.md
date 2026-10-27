# Test OpenRouter Free trong LHM Robot Desktop và GitHub

## 1. Test ngay trong ứng dụng Electron (khuyên dùng)

1. Cài bản `LHM Robot Desktop` build mới nhất từ GitHub Actions.
2. Mở **LHM Robot → Thiết lập Robot**; ở mục *Bộ não AI* chọn **OpenRouter**.
3. Nhập API key được tạo tại [OpenRouter Keys](https://openrouter.ai/settings/keys) vào ô **OpenRouter API key**.
4. Bấm **Lưu cấu hình**. Nếu chương trình hỏi, chọn khởi động lại; mở lại *Thiết lập Robot*.
5. Bấm **Thử gọi OpenRouter Free**. Phần mềm gửi đúng **một** request:
   - HTTPS: `POST https://openrouter.ai/api/v1/chat/completions`
   - Model: `openrouter/free` (chỉ chọn các model miễn phí)
   - Tin nhắn: `Reply with exactly the word: LHM_OK`
   - `max_tokens=48`, `stream=false`, `usage.include=true`
   - Không gửi API key, cookies hay dữ liệu người dùng về cửa sổ giao diện.
6. Xem kết quả thực tế: model được OpenRouter chọn, câu trả lời, token vào/ra và **chi phí do API báo cáo**.

Nếu thành công, API key/route Free hoạt động cho yêu cầu test. **Điều này chưa chứng minh đủ chất lượng cho 16 Agent, vision, tool calling hoặc quota cả ngày.**

Ứng dụng dùng **key đã được lưu/mã hóa trên máy anh**, không cần gửi key trong chat hay GitHub. Muốn dùng model Free cho Agent, phải bảo đảm model của Agent được cấu hình là `openrouter/free` hoặc một mã `:free` được nhà cung cấp hỗ trợ. Nút kiểm tra không thay đổi các lựa chọn model/budget của nhiệm vụ đã lưu.

Nếu OpenRouter trả HTTP 401/403, khóa có thể không hợp lệ. Với HTTP 429, quota/rate limit đã hết; **không fallback trả phí**. Nếu API không trả chi phí, phần mềm hiển thị `chưa xác minh`, không tự báo 0 USD; nếu báo phí khác 0, phần mềm cảnh báo và dừng kiểm thử tiếp. Yêu cầu đã gửi không thể hoàn phí/thu hồi.

## 2. Test trên GitHub Actions

Có **hai lớp test riêng**:

- **Mock, luôn chạy trên PR** trong workflow `Windows Electron Robot Desktop` qua `npm test`. Kiểm tra HTTPS host/path cố định, model Free, chi phí, sai API key, HTTP 429, không có fallback trả phí và không lộ key sang Electron renderer. **Không cần API key thật.**
- **Live API, chỉ chạy khi anh cho phép** trong workflow `OpenRouter Free API live smoke (manual only)`. GitHub Actions không thể đọc khóa đã lưu trong Electron Windows. Để test thực sự từ runner GitHub, cần tạo **một key riêng dành cho kiểm thử**, giới hạn ngân sách ở phía OpenRouter, rồi:
  1. Vào GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**.
  2. Tạo secret có tên **`OPENROUTER_TEST_API_KEY`** với key dùng riêng cho GitHub.
  3. Vào **Actions → OpenRouter Free API live smoke (manual only) → Run workflow**.
  4. Chọn `confirm_free_api_call=true` và chạy.
  5. Workflow sẽ chạy mock test rồi gửi **duy nhất một yêu cầu tới `openrouter/free`**; logs chỉ in tên model, token, chi phí báo cáo, không in key/response raw.

**Quan trọng:** Không thêm key vào source repo, file `.env` commit hoặc GitHub Actions input text. Repository secret chỉ dùng trong run có xác nhận thủ công. Nếu secret chưa có, bước live sẽ báo **LIVE NOT RUN**, không giả mạo kết quả.

## 3. Giới hạn bảo mật

- `openrouter/free` là router chọn model `:free`; **không dùng** `openrouter/auto` hoặc paid fallback.
- Key được Electron Main giải mã bằng Windows safeStorage, chỉ Main process gọi HTTPS. Settings renderer chỉ nhận phản hồi đã lọc, **không nhận khóa**.
- Nút test không đưa dữ liệu khách hàng vào prompt, chỉ gửi câu lệnh mẫu.
- Phí được đối chiếu tốt nhất tại [OpenRouter Activity](https://openrouter.ai/activity); `usage.cost` có thể không hiện trong mọi response.
- Kết quả CI mock không phải bằng chứng đã xác thực live OpenRouter với tài khoản người dùng.
