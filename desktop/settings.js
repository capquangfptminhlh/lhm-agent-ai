"use strict";
const $=id=>document.getElementById(id);
async function load(){
 try {
  const x=await window.lhmSettings.load();
  $("provider").value=x.provider;
  $("allowedApps").value=x.allowedApps||"";
  $("allowedHosts").value=x.allowedHosts||"";
  $("visionEnabled").checked=x.visionEnabled===true;
  $("visionModel").value=x.visionModel||"";
  $("sandboxEnabled").checked=x.sandboxEnabled===true;
  $("agentParallelism").value=String(x.agentParallelism||1);
  $("openStatus").textContent=x.hasOpenrouterKey?"(đã lưu)": "(chưa có)";
  $("openTest").disabled=!x.hasOpenrouterKey || !x.secureStorage;
  $("deepStatus").textContent=x.hasDeepseekKey?"(đã lưu)": "(chưa có)";
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
    visionModel:$("visionModel").value,
    sandboxEnabled:$("sandboxEnabled").checked,
    agentParallelism:Number($("agentParallelism").value),
    openrouterKey:$("openKey").value,deepseekKey:$("deepKey").value,
    clearOpenrouter:$("clearOpenrouter").checked,
    clearDeepseek:$("clearDeepseek").checked
   });
   $("openKey").value="";$("deepKey").value="";
   $("status").textContent="Đã lưu. Cần khởi động lại ứng dụng để áp dụng.";
   await load();
 }catch(e){$("status").textContent=e.message;$("status").classList.add("error")}
 finally{$("save").disabled=false}
};
load();

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
