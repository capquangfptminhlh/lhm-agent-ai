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
