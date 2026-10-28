"use strict";
const $=id=>document.getElementById(id);
async function load(){
 try {
  const x=await window.lhmSettings.load();
  $("provider").value=x.provider;
  $("allowedApps").value=x.allowedApps||"";
  $("allowedHosts").value=x.allowedHosts||"";
  $("visionEnabled").checked=x.visionEnabled===true;
  $("trustedEnabled").checked=x.trustedEnabled===true;
  $("fullScreenEnabled").checked=x.fullScreenEnabled===true;
  $("visionModel").value=x.visionModel||"";
  $("sandboxEnabled").checked=x.sandboxEnabled===true;
  $("reachEnabled").checked=x.reachEnabled===true;
  $("opencliEnabled").checked=x.opencliEnabled===true;
  $("opencliProfile").value=x.opencliProfile||"";
  $("opencliHosts").value=x.opencliHosts||"";
  $("agentParallelism").value=String(x.agentParallelism||1);
  $("openStatus").textContent=x.hasOpenrouterKey?"(đã lưu)": "(chưa có)";
  $("openTest").disabled=!x.hasOpenrouterKey || !x.secureStorage;
  if(!x.secureStorage)$("status").textContent="Windows secureStorage không khả dụng; không thể lưu API key.";
 }catch(e){$("status").textContent=e.message;$("status").classList.add("error")}
}
$("save").onclick=async()=>{
 $("save").disabled=true;$("status").textContent="";
 try{
   await window.lhmSettings.save({
    provider:$("provider").value,allowedApps:$("allowedApps").value,
    allowedHosts:$("allowedHosts").value,
    visionEnabled:$("visionEnabled").checked,
    trustedEnabled:$("trustedEnabled").checked,
    fullScreenEnabled:$("fullScreenEnabled").checked,
    visionModel:$("visionModel").value,
    sandboxEnabled:$("sandboxEnabled").checked,
    reachEnabled:$("reachEnabled").checked,
    opencliEnabled:$("opencliEnabled").checked,
    opencliProfile:$("opencliProfile").value,
    opencliHosts:$("opencliHosts").value,
    agentParallelism:Number($("agentParallelism").value),
    openrouterKey:$("openKey").value,
    clearOpenrouter:$("clearOpenrouter").checked
   });
   $("openKey").value="";
   $("status").textContent="Đã lưu. Cần khởi động lại ứng dụng để áp dụng.";
   await load();
 }catch(e){$("status").textContent=e.message;$("status").classList.add("error")}
 finally{$("save").disabled=false}
};
async function checkReadiness(){
 const area=$("readiness"),refresh=$("refreshReadiness");
 area.textContent="Đang kiểm tra...";
 refresh.disabled=true;
 try{
   const state=await window.lhmSettings.readiness();
   area.textContent="Phiên bản: "+state.version+"\n"+
     state.services.map(x=>(x.ok?"✓ ":"✕ ")+x.label+": "+x.detail).join("\n");
   area.style.color=state.backendReady?"#365d78":"#b14353";
 }catch(error){
   area.textContent="Không đọc được trạng thái dịch vụ: "+(error?.message||"lỗi không xác định");
   area.style.color="#b14353";
 }finally{refresh.disabled=false}
}
$("refreshReadiness").onclick=checkReadiness;
$("opencliFindProfiles").onclick=async()=>{
 const button=$("opencliFindProfiles"),list=$("opencliProfileSelect"),panel=$("opencliCheckResult");
 button.disabled=true;
 try{
   const found=await window.lhmSettings.opencliProfiles();
   list.replaceChildren();
   const blank=document.createElement("option");
   blank.value="";blank.textContent="Chọn Chrome profile riêng…";
   list.append(blank);
   for(const item of found.profiles||[]){
     if(!item || typeof item.id!=="string" || !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/.test(item.id))continue;
     const option=document.createElement("option");
     option.value=item.id;option.textContent=String(item.label||item.id).slice(0,80);
     list.append(option);
   }
   panel.textContent=list.options.length>1?"Chọn profile Chrome riêng trong danh sách rồi Lưu cấu hình.":String(found.detail||"Chưa thấy Chrome profile kết nối");
 }catch(e){panel.textContent="Không tìm được Chrome profile: "+(e.message||"lỗi kết nối");}
 finally{button.disabled=false;}
};
$("opencliProfileSelect").onchange=()=>{if($("opencliProfileSelect").value)$("opencliProfile").value=$("opencliProfileSelect").value;};
$("opencliExtension").onclick=async()=>{
 const button=$("opencliExtension"),panel=$("opencliCheckResult");
 button.disabled=true;
 try {await window.lhmSettings.opencliExtension();panel.textContent="Đã mở Chrome Web Store. Cài extension trong Chrome profile riêng của LHM, sau đó quay lại kiểm tra kết nối.";}
 catch(e){panel.textContent="Không mở được trang extension: "+(e.message||"lỗi hệ thống");}
 finally {button.disabled=false;}
};
$("opencliCheck").onclick=async()=>{
 const button=$("opencliCheck"), panel=$("opencliCheckResult");
 button.disabled=true;panel.textContent="Đang kiểm tra Windows Controller và Chrome Bridge...";
 try{
  const s=await window.lhmSettings.opencliStatus();
  panel.textContent=[
    (s.enabled?"✓":"✕")+" OpenCLI đã bật",
    (s.cli_installed?"✓":"✕")+" Node.js / OpenCLI 2.0.2 đã cài",
    (s.bridge_running?"✓":"✕")+" Browser Bridge đang hoạt động",
    (s.profile_connected?"✓":"✕")+" Profile Chrome '"+(s.profile_alias||"chưa chọn")+"' đã kết nối",
    s.ready?"Sẵn sàng thao tác theo quyền được cấp":"Chưa sẵn sàng: "+(s.detail||"kiểm tra cấu hình")
  ].join("\n");
 }catch(err){panel.textContent="Không kiểm tra được: "+(err.message||"lỗi kết nối");}
 finally{button.disabled=false;}
};
$("quickStart").onclick=()=>{
 if(!$("openKey").value.trim() && $("openStatus").textContent.includes("chưa có")){
   $("status").textContent="Nhập OpenRouter API key ở trên để Agent có thể trả lời.";
   $("status").classList.add("error");
   $("openKey").focus();
   return;
 }
 // Explicit click grants ONLY three demo apps; any unrelated app stays denied.
 if(!$("allowedApps").value.trim()) $("allowedApps").value="notepad.exe, explorer.exe, calc.exe";
 $("save").click();
};
load().then(checkReadiness);

$("openTest").onclick=async()=>{
  const button=$("openTest"),panel=$("openTestResult");
  button.disabled=true;
  panel.style.display="block";
  panel.textContent="Đang gửi một yêu cầu kiểm tra đến OpenRouter Free...";
  try{
    const result=await window.lhmSettings.testOpenRouter();
    if(!result || result.ok!==true)throw Error("Kết quả kiểm tra không hợp lệ.");
    const cost=result.reported_cost_usd===null ?
      "Chi phí API không được báo cáo (cần đối chiếu Activity)" :
      "Chi phí do API báo: $"+result.reported_cost_usd;
    panel.textContent=[
      "✓ OpenRouter Free kết nối thành công",
      "Model thực tế: "+result.actual_model,
      "Phản hồi: "+result.response_preview,
      "Tokens: "+(result.prompt_tokens??"?")+" đầu vào / "+(result.completion_tokens??"?")+" đầu ra",
      cost,
      result.note
    ].join("\n");
  }catch(error){
    panel.textContent="✕ Thử OpenRouter Free không thành công: "+(error?.message||"Lỗi không xác định");
  }finally{button.disabled=false}
};
