# Nghiệm thu Android THẬT trên Windows — LHM Robot

## Tại sao cần bước này?

CI Windows thông thường build Electron/EXE và chạy kiểm thử với Android giả lập ở mức **mock**, không chứng minh Android Emulator đã khởi động hay Robot thực sự đọc được nút Android. Để đạt mốc runtime, workflow riêng này phải chạy thành công trên **Windows self-hosted runner có tăng tốc ảo hóa và AVD sạch**.

## Điều kiện bắt buộc

- Một PC Windows 10/11 dành riêng cho QA, bật VT-x/AMD-V và cài Android SDK (platform-tools, emulator, system image). Có AVD kiểm thử tạo sẵn với tên an toàn, ví dụ `LHM_Test_Pixel`. **Không đăng nhập tài khoản Google, ngân hàng, Zalo hay thông tin cá nhân trên AVD này.**
- GitHub Actions Runner của chính chủ trên PC đó có nhãn `self-hosted`, `Windows`, `X64` và nhãn bổ sung `android-sdk`. Không đăng ký nhãn này cho máy của người dùng cuối hoặc máy chủ dùng chung.
- Trước khi chạy, **đóng toàn bộ AVD và ngắt các điện thoại ADB** trên PC kiểm thử. Bài test từ chối chạy nếu đã có thiết bị ADB.
- Repo `capquangfptminhlh/ggminh` chứa `bin/lhm-android-mcp.js`; workflow checkout repo này ngay trong job để sử dụng giao thức MCP hiện tại.

## Chạy bằng tay

1. Vào GitHub → **Actions** → **LHM Robot Android REAL Windows AVD Acceptance** → **Run workflow**.
2. Nhập tên AVD hiện có tại ô `avd_name`.
3. Giữ `allow_safe_back_tap = NO` để chỉ nghiệm thu Android khởi động, chụp PNG và gọi hai công cụ thật `android_mcp_devices`, `android_mcp_inspect` từ Python Agent qua MCP stdio.
4. Khi muốn thử hành động chạm riêng, chủ sở hữu có thể chạy lại và chọn `allow_safe_back_tap = YES`. Bài test sẽ **chỉ** tìm một nút có vai trò `button` và hint `back` duy nhất trên màn Android Settings. Nếu không có, bài test thất bại chứ không chọn nút khác. Đây là **chấp thuận thử nghiệm của người chạy workflow**, không thay thế cơ chế duyệt từng bước trên giao diện Computer Console.
5. Kiểm tra kết quả GitHub Actions. Kết quả chỉ hiển thị trạng thái, kích thước màn hình, số nút và thông tin an toàn. Không upload ảnh, UI text, serial ADB hoặc dữ liệu ứng dụng. AVD được yêu cầu tắt và kiểm tra giải phóng trước khi in PASS.

Workflow: [android-agent-live-windows.yml](../.github/workflows/android-agent-live-windows.yml). Harness: [`scripts/android_agent_live_acceptance.py`](../scripts/android_agent_live_acceptance.py).

## Chặn thao tác sai/lặp trong lượt thử Back

- Trước khi phát lệnh chạm, harness đối chiếu nút Back duy nhất với kích thước ảnh PNG **chỉ đọc metadata** từ chính AVD; index bị trùng, tọa độ nằm ngoài khung nhìn hoặc hình học không hợp lệ đều khiến bài test FAIL mà không gọi tap.
- Native Windows Runner chỉ cho phép **một tap đang thực thi trên mỗi serial AVD** trong cùng tiến trình, bao gồm giai đoạn đọc lại UI, gửi ADB và đọc hậu kiểm. Lượt chạm đồng thời bị từ chối HTTP 409; observation token của lượt bị từ chối vẫn bị tiêu thụ, nên phải quét và xin duyệt lại thay vì retry tự động.
- Trước mọi tap, Runner vẫn so sánh toàn bộ hình học UI với observation đã cấp. Mã hết hạn, dùng lại, sai run/role/serial, hoặc UI thay đổi đều phải từ chối. Sau tap, mọi kết quả vẫn mang trạng thái **chưa xác minh tác động bên ngoài** và cần chủ sở hữu đối chiếu.
- Đây là khóa **trong một tiến trình Runner**, không phải khóa phân tán giữa nhiều máy hoặc ứng dụng ADB khác. Chưa thể bảo đảm AVD không đổi màn hình trong khoảng rất ngắn giữa lúc đọc lại và lúc ADB gửi tap. Chỉ thử trên AVD sạch, không tài khoản và không dữ liệu quan trọng.

Các kiểm thử hồi quy trong CI Windows: `tests/test_android_mcp_actions.py` (chạm đồng thời, mã dùng một lần, UI đổi/đọc lỗi) và `tests/test_android_agent_live_acceptance.py` (nút Back ngoài màn hình hoặc index trùng). CI thường **không** thay thế một lượt workflow AVD thật thành công.

## Cách đọc kết quả

| Kết quả | Ý nghĩa |
| --- | --- |
| `read=PASS`, `tap=NOT_REQUESTED` | Đã chạy **ADB + PNG + MCP thật** trên AVD kiểm thử Windows, nhưng không có hành động chạm |
| `read=PASS`, `tap=SENT_UNVERIFIED_NEEDS_OWNER_RECONCILIATION` | Đã gửi một thao tác Back trong thử nghiệm có lựa chọn YES. **Không chứng minh tác động ứng dụng thành công** |
| `FAIL` | Cấu hình SDK/AVD sai, AVD không sẵn sàng, giao thức MCP lỗi, dữ liệu chưa đủ an toàn, không tìm thấy một Back an toàn hoặc cleanup không thành công |
| Workflow chưa chạy / đang chờ runner | **Không có bằng chứng nghiệm thu Android thật** |

**Ghi chú:** Để nghiệm thu thương mại còn phải thử Computer Console end-to-end có người bấm duyệt trong GUI Electron, kiểm tra chuyển đổi ứng dụng/thông báo và đánh giá đa máy cùng lúc. Workflow này KHÔNG xác nhận Android chạy tự động trên tất cả máy Windows hoặc agent đã đủ an toàn cho thanh toán, đăng bài hay nhắn tin.

## Test tự động thông thường

```powershell
python -m unittest discover -s tests -p "test_android_agent_live_acceptance.py" -v
```

Đây là test mô phỏng quy trình, **không phải runtime AVD thật**.
