"use strict";
function readiness(input) {
  const services=[
    {id:"api",label:"Bộ não / Backend",ok:input.apiStatus===200,
      detail:input.apiStatus===200?"API nội bộ đã sẵn sàng":"Không kết nối được backend (cổng 7770)"},
    {id:"native",label:"Điều khiển Windows",ok:input.nativeStatus===200,
      detail:input.nativeStatus===200?"Windows Controller đang chạy":"Windows Controller chưa sẵn sàng (cổng 7782)"},
    {id:"worker",label:"Đội Agent",ok:input.workerRunning===true,
      detail:input.workerRunning?"Worker đang chạy, chưa xác minh kết quả":"Worker chưa chạy hoặc đã dừng"},
    {id:"key",label:"OpenRouter",ok:input.keyStored&&input.keyUnlocked,
      detail:!input.keyStored?"Chưa nhập API key":!input.keyUnlocked?
        "API key đã lưu nhưng không giải mã được":"Đã lưu key (hãy bấm thử gọi để kiểm tra)"},
    {id:"apps",label:"Ứng dụng Windows",ok:input.allowedApps>0,
      detail:input.allowedApps>0?"Đã cho phép "+input.allowedApps+" ứng dụng":
        "Chưa cấp quyền ứng dụng. Bấm Cho phép ứng dụng mẫu để bắt đầu"},
    {id:"web",label:"Website",ok:input.allowedHosts>0,
      detail:input.allowedHosts>0?"Đã cho phép "+input.allowedHosts+" website":
        "Chưa cho phép website nào. Website bên ngoài cần duyệt tên miền"}
  ];
  return {version:input.version||"unknown",services,
          backendReady:input.apiStatus===200,
          agentReady:input.apiStatus===200&&input.workerRunning&&input.keyStored&&input.keyUnlocked,
          screenReady:input.nativeStatus===200&&input.allowedApps>0,
          browserReady:input.nativeStatus===200&&input.allowedHosts>0};
}
module.exports={readiness};
