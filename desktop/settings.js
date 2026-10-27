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
  $("openStatus").textContent=x.hasOpenrouterKey?"(đã lưu)": "(chưa có)";
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
    openrouterKey:$("openKey").value,deepseekKey:$("deepKey").value,
    clearOpenrouter:$("clearOpenrouter").checked,
    clearDeepseek:$("clearDeepseek").checked
   });
   $("openKey").value="";$("deepKey").value="";
   $("status").textContent="Đã lưu. Cần khởi động lại ứng dụng để áp dụng.";
 }catch(e){$("status").textContent=e.message;$("status").classList.add("error")}
 finally{$("save").disabled=false}
};
load();
