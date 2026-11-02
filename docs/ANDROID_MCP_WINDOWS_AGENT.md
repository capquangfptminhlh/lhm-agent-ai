# Android MCP trong LHM Robot Desktop (Windows)

Luồng tích hợp thật: `LHM Robot Desktop` (Electron cấu hình) → `computer_agent.py` (chọn công cụ theo role) → `computer_executor.py` (Windows native runner) → `android_mcp_bridge.py` (MCP stdio) → `ggminh/bin/lhm-android-mcp.js` → Android SDK/ADB.

## Cách bật

1. Cài phiên bản `ggminh` có PR #83 hoặc mới hơn trên ổ đĩa Windows. Cài Android SDK Platform Tools và tạo một AVD kiểm thử. Khởi chạy AVD, dùng `adb devices -l` để đọc serial chính xác.
2. Mở **LHM Robot → Thiết lập Robot → Tùy chỉnh nâng cao → Android MCP**. Bật tính năng; nhập thư mục `ggminh` trên ổ Windows (ví dụ `C:\LHM\ggminh`), serial như `emulator-5554`, và vai trò như `research,quality`.
3. Nhấn **Lưu cấu hình**, khởi động lại phần mềm, sau đó **Kiểm tra Android MCP**. Chỉ tiếp tục khi Runner xác nhận đã cấu hình. Trạng thái cấu hình không chứng minh emulator đang online: hãy thử một tác vụ Computer Agent có nhu cầu đọc danh sách Android.
4. Tại màn hình **Máy tính Agent**, chọn đúng vai trò được cấp và giao việc đọc máy Android. Agent có thể gọi `android_mcp_devices` để liệt kê máy đã cấp quyền, và `android_mcp_inspect` với serial hợp lệ để lấy nút bấm theo hình học.

## Chạm một nút trên Android ảo — thử nghiệm, phải duyệt

Từ Thiết lập Robot, bật thêm **Cho phép đề xuất chạm nút trên Android ảo kiểm thử** (mặc định TẮT). Anh vẫn phải chỉ định serial `emulator-xxxx` và vai trò Agent. Luồng:

1. Agent gọi `android_mcp_devices`, sau đó `android_mcp_inspect` trên AVD được phép. Windows Runner tạo một `observation_id` ngẫu nhiên dùng một lần, giới hạn 5 phút, ràng buộc vào Computer run + vai trò + serial + cấu trúc nút đã quan sát.
2. Agent có thể đề xuất `android_mcp_tap` với **serial, observation_id và index từ lần quét**, không nhận tọa độ tùy ý, không gọi ADB shell do AI tạo. Robot chỉ nhận đề xuất nếu có **bước `android_mcp_inspect` hoàn thành trước đó trong cùng Computer Run**, với serial, mã quan sát, index và hình học nút khớp chính xác. Không có bước quét hợp lệ thì không mở hộp thoại duyệt. Mã dùng một lần được đặt trước danh sách nút để không bị cắt khỏi lịch sử AI.
3. Robot dừng ở `awaiting_approval`. **Máy tính Agent / Computer Console** hiển thị riêng serial AVD, index nút, loại nút, gợi ý an toàn và tọa độ tâm từ bằng chứng quét trước. Nội dung chữ của nút được MCP chủ động ẩn, vì vậy **anh phải nhìn màn hình AVD thật** trước khi chọn Duyệt; không được suy đoán hành động chỉ từ tọa độ. Nếu thiếu bằng chứng quét, nút Duyệt bị vô hiệu và server cũng từ chối.
4. Khi đã duyệt, Windows Runner tiêu thụ mã một lần, **đọc lại toàn bộ nút tương tác và so sánh với lần quan sát**, sau đó chỉ gửi `adb -s SERIAL shell input tap X Y` với tọa độ lấy từ bản quét đáng tin. Bất kỳ thay đổi, hết hạn, mất kết nối đều từ chối, không gửi tap.
5. Sau khi gửi, Runner kiểm tra lại UI nhưng **không kết luận hành động trong ứng dụng đã thành công**. Computer Agent dừng ở `awaiting_reconciliation` và yêu cầu anh xem lại máy Android trước khi tiếp tục. Không tự gửi lại lệnh trong trường hợp kết quả không rõ.

Lệnh tap này chạy trong **native Windows Runner**, không mở thêm công cụ ghi vào MCP của `ggminh`. Chỉ chạm trên AVD kiểm thử, không cho phép điện thoại vật lý, nhập liệu, cài APK, thanh toán hoặc thao tác tự động nhiều bước.

**Chưa nghiệm thu:** AVD Windows chạy thật, UI thay đổi đồng thời, ứng dụng có nội dung riêng tư, hiệu năng đa máy. Test CI chỉ chứng minh logic và gói cài.

## Phạm vi quyền

- Mặc định Android MCP **TẮT** và không có serial/vai trò được cấp.
- Có thể chỉ định chính xác các vai trò trong 16 Agent, tối đa 8 serial, không wildcard. Mọi quyết định đều được kiểm tra lại trong Windows runner; AI không được đổi đường dẫn, executable hoặc danh sách serial qua tham số công cụ.
- Hai tool MCP đọc của Agent vẫn **chỉ đọc**; một tool tap riêng trong Windows Runner chỉ xuất hiện khi anh bật quyền thử nghiệm và phải duyệt mỗi lần. Không có click, tap, swipe, nhập chữ, screenshot, APK, tài khoản, shell hay gửi tin. Các hành động đó vẫn nằm trong LHM Browser và phải được anh duyệt riêng qua Electron.
- Dữ liệu công cụ trả về có thể đi vào ngữ cảnh mô hình AI: serial đã cấp quyền và loại nút, vai trò, tọa độ, gợi ý điều hướng. Không dùng với thiết bị có dữ liệu riêng tư dù bridge đã loại bỏ nhãn chữ, mật khẩu và ảnh.
- Bridge chạy trong tiến trình phụ Node.js từ bản `ggminh` trên máy. Không mở cổng HTTP/MCP công khai. Tiến trình vẫn dùng quyền Windows hiện tại, **không phải sandbox cách ly**.

## Kiểm thử

- `python -m unittest tests.test_android_mcp_bridge` — phân quyền role/serial, dữ liệu nhạy cảm, truyền thông MCP mô phỏng và chặn cấu hình sai.
- `cd desktop && npm test` — cấu hình, validation Windows, nguồn Node cố định và kiểm tra khác của Electron.
- Kiểm thử trên GitHub chỉ chứng minh mã/đóng gói với mock. **Chưa được coi là Android thật hoạt động** khi workflow AVD tự-host `ggminh` chưa PASS.

## Kiểm tra ADB thực tế ngay trong giao diện LHM Robot

Mở **Thiết lập Robot → Android MCP → Kiểm tra ADB + MCP + Android thật**, sau khi lưu và khởi động lại Robot.

Nút này không chỉ nhìn xem file MCP có tồn tại. Windows native Runner thực thi **hai lần đọc MCP stdio thật**:

1. `android_mcp_devices` trả trạng thái ADB của các serial anh đã cấp quyền. Tài khoản, model điện thoại và thông tin ADB khác đều bị lọc.
2. Nếu có ít nhất một AVD đã cấp quyền và online, `android_mcp_inspect` đọc cấu trúc UI của AVD đó. UI chỉ hiển thị **số nút tương tác**, không xem hoặc lưu nội dung màn hình. Cả hai lần gọi đều có thời hạn giới hạn.

Chỉ khi MCP/ADB trả dữ liệu thành công, AVD đã kết nối và có nút tương tác thì mục **Kết nối đọc AVD thật đạt** mới được bật. Trạng thái rõ ràng cho biết lỗi do cấu hình, MCP/ADB, AVD chưa online hoặc cây UI chưa sẵn sàng. Phép kiểm tra này **không** chạm, vuốt, gửi ảnh cho AI hay xác minh hành động trong app.

Nếu anh vừa thay đổi thư mục `ggminh`, serial, role hoặc quyền chạm, hãy **lưu cấu hình và khởi động lại** Robot trước khi kiểm tra. Kết quả thực tế sẽ khác nhau tùy máy Windows và Android SDK. CI mặc định chỉ mô phỏng đường gọi MCP; vẫn cần chạy nút này trên AVD thật.

## Nghiệm thu Android thật từ LHM Robot qua ggminh MCP (mới)

Đây là **bài test Android runtime thật**, không phải ảnh demo hay mock. Cần một **máy Windows self-hosted** được gắn nhãn GitHub Actions `android-sdk`, cài Android SDK, ADB, Node 22 và có AVD **kiểm thử riêng, không đăng nhập tài khoản thật**, đang chạy sẵn. Quy trình này **không tạo AVD**, không chạm hay thay đổi nội dung Android; nó chỉ đọc:

- Xác minh ADB serial `emulator-XXXX` trùng đúng **tên AVD** được chọn.
- Xác minh AVD đã khởi động hoàn tất qua `sys.boot_completed`.
- Dùng **Python bridge thật của Robot → Node MCP thật của ggminh → ADB thật** để đọc danh sách thiết bị và cây UI đã xóa nội dung riêng tư.
- Xác nhận chỉ serial được phép xuất hiện, serial khác bị từ chối và kết quả UI không chứa text/resource IDs/password.
- Báo PASS chỉ khi có nút tương tác thật từ giao diện Android. Không chạy chạm Android, không chụp ảnh, không xuất raw UI XML và không upload dữ liệu thiết bị.

Trên GitHub: mở **Actions → Android MCP Real Windows Acceptance (manual test AVD) → Run workflow** và nhập `avd_name` và `serial` đúng AVD đang chạy. Workflow không tự chạy trên GitHub-hosted Windows vì không thể coi môi trường mock là thiết bị Android đã nghiệm thu. Nếu thiếu self-hosted runner có nhãn `android-sdk`, job sẽ nằm trong hàng chờ, **không phải PASS**.

Cũng có thể chạy thủ công trong bản clone LHM Robot Desktop (PowerShell):

```powershell
python -m pip install "fastapi>=0.110,<1"
python scripts/android_mcp_live_acceptance.py --serial emulator-5554 --avd-name LHM_CI_Test --ggminh "C:\LHM\ggminh"
```

`--avd-name` phải là tên AVD đã được tạo/chạy, không phải tên giả. Khi PASS, kết quả chỉ chứa cờ kiểm chứng, số nút và `permissions=read-only`. Tất cả kết quả mock trong CI Windows bình thường vẫn chỉ chứng minh **bộ kiểm tra chính xác**, không phải trạng thái AVD thật.

**Sau bài test này vẫn cần một lượt nghiệm thu thao tác tap có người duyệt** trên một ứng dụng kiểm thử chuyên biệt, và thử nhiều AVD song song. Chưa bật tự động điều khiển Android chỉ vì bài đọc MCP thành công.

## Giới hạn hiện tại

Đây là tích hợp **Computer Agent** cho các vai trò được chọn, không có nghĩa cả 16 Agent được cấp quyền mặc định hoặc mỗi Agent có một VM Android riêng. Không có AI semantic vision, luồng phê duyệt hành động MCP, tự cài Android SDK, test GPU đa máy hoặc kiểm chứng thương mại. Cần test thực tế trên Windows 10/11 sau khi cấp quyền.
