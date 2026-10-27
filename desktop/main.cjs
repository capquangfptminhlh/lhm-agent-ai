"use strict";
/**
 * LHM Robot Desktop: trusted Electron main process.
 * No web page receives Node, filesystem, OS command or Electron IPC powers.
 * The local Python runtime is a supervised, separate executable.
 */
const {app, BrowserWindow, Menu, session, ipcMain, dialog, safeStorage} = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const crypto = require("node:crypto");
const {spawn} = require("node:child_process");
const http = require("node:http");
const {requestFreeCompletion} = require("./openrouter-free-test.cjs");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const BACKEND_ORIGIN = "http://127.0.0.1:7770";
const NATIVE_ORIGIN = "http://127.0.0.1:7782";
const ROUTES = Object.freeze({
  "Văn phòng 3D":"/office3d",
  "Đội Agent trao đổi":"/team-chat",
  "Máy tính Agent":"/computer",
  "Công cụ chuyên môn":"/agent-tools",
  "Workflow Builder":"/workflow-builder",
  "Dashboard":"/dashboard"
});
let mainWindow;
let settingsWindow;
let settings;
let adminToken;
let runnerToken;
let shutdown = false;
let nativePaused = false;
const children = new Map();

function settingsPath() {
  return path.join(app.getPath("userData"), "robot-settings.json");
}
function readRawSettings() {
  try {return JSON.parse(fs.readFileSync(settingsPath(), "utf8"));}
  catch {return {};}
}
function isSecureSecret(value) {
  return typeof value === "string" && value.length > 0;
}
function unwrapSecret(encoded) {
  if (!encoded) return "";
  if (!safeStorage.isEncryptionAvailable()) return "";
  try {return safeStorage.decryptString(Buffer.from(encoded, "base64"));}
  catch {return "";}
}
function saveSettings(input) {
  const current = readRawSettings();
  const provider = input.provider === "deepseek" ? "deepseek" : "openrouter";
  const allowedHosts = String(input.allowedHosts || "").toLowerCase()
      .split(",").map(x=>x.trim()).filter(Boolean);
  const allowedApps = String(input.allowedApps || "").toLowerCase()
      .split(",").map(x=>x.trim()).filter(Boolean);
  if (allowedHosts.length > 30 || allowedHosts.some(h=>
      !/^(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/.test(h))) {
    throw new Error("Chỉ cho phép tối đa 30 tên miền HTTPS hợp lệ, không dùng IP hoặc wildcard.");
  }
  const blocked=new Set(["cmd.exe","powershell.exe","pwsh.exe","wt.exe",
    "regedit.exe","mmc.exe","taskmgr.exe","conhost.exe","wscript.exe",
    "cscript.exe","mshta.exe","rundll32.exe","python.exe","pythonw.exe","node.exe"]);
  if (allowedApps.length > 20 || allowedApps.some(a=>
      !/^[a-z0-9._-]{3,70}\.exe$/.test(a) || blocked.has(a))) {
    throw new Error("Chỉ cho phép EXE ứng dụng thông thường, không cho terminal hoặc công cụ hệ thống.");
  }
  const visionModel=String(input.visionModel||"").trim();
  if (visionModel && !/^[a-z0-9][a-z0-9_.-]*\/[a-z0-9][a-z0-9_.-]*:free$/i.test(visionModel)) {
    throw new Error("Vision model phải là tên model OpenRouter :free cụ thể, không dùng gói trả phí.");
  }
  const data = {
    provider, allowedHosts:[...new Set(allowedHosts)],
    allowedApps:[...new Set(allowedApps)],
    visionEnabled:input.visionEnabled===true,
    visionModel,
    sandboxEnabled:input.sandboxEnabled===true,
    agentParallelism:([1,2,3,4].includes(input.agentParallelism)?input.agentParallelism:1),
    openrouterKey:current.openrouterKey || "",
    deepseekKey:current.deepseekKey || ""
  };
  if ((input.clearOpenrouter === true)) data.openrouterKey="";
  if ((input.clearDeepseek === true)) data.deepseekKey="";
  for (const [key, field] of [["openrouterKey","openrouterKey"],["deepseekKey","deepseekKey"]]) {
    const raw=input[field];
    if (isSecureSecret(raw)) {
      if (raw.length > 600 || !safeStorage.isEncryptionAvailable()) {
        throw new Error("Windows secure key storage is unavailable or the key is too long.");
      }
      data[key]=safeStorage.encryptString(raw).toString("base64");
    }
  }
  fs.mkdirSync(path.dirname(settingsPath()),{recursive:true});
  const temp=settingsPath()+".tmp";
  fs.writeFileSync(temp,JSON.stringify(data,null,2),{encoding:"utf8",mode:0o600});
  fs.renameSync(temp,settingsPath());
  return data;
}
function settingsForUI() {
  const c=readRawSettings();
  return {
    provider:c.provider||"openrouter",
    allowedHosts:Array.isArray(c.allowedHosts)?c.allowedHosts.join(","):"",
    allowedApps:Array.isArray(c.allowedApps)?c.allowedApps.join(","):"",
    visionEnabled:c.visionEnabled===true,
    visionModel:c.visionModel||"",
    sandboxEnabled:c.sandboxEnabled===true,
    agentParallelism:[1,2,3,4].includes(c.agentParallelism)?c.agentParallelism:1,
    hasOpenrouterKey:!!c.openrouterKey,hasDeepseekKey:!!c.deepseekKey,
    secureStorage:safeStorage.isEncryptionAvailable()
  };
}
function envForRuntime() {
  const c=readRawSettings();
  const userData=app.getPath("userData");
  fs.mkdirSync(userData,{recursive:true});
  return {
    ...process.env,
    LHM_DB_PATH:path.join(userData,"lhm.sqlite3"),
    LHM_ADMIN_TOKEN:adminToken,
    LHM_COMPUTER_RUNNER_TOKEN:runnerToken,
    LHM_COMPUTER_URL:NATIVE_ORIGIN,
    LHM_COMPUTER_HOME:path.join(userData,"agent-data"),
    LHM_COMPUTER_BROWSER_CHANNEL:"msedge",
    LHM_DESKTOP_NATIVE:"1",
    LHM_DESKTOP_ALLOWED_APPS:(Array.isArray(c.allowedApps)?c.allowedApps:[]).join(","),
    LHM_COMPUTER_ALLOWED_HOSTS:(Array.isArray(c.allowedHosts)?c.allowedHosts:[]).join(","),
    LHM_COMPUTER_VISION_ENABLED:c.visionEnabled===true?"1":"0",
    LHM_COMPUTER_VISION_MODEL:typeof c.visionModel==="string"?c.visionModel:"",
    LHM_SANDBOX_ENABLED:c.sandboxEnabled===true?"1":"0",
    LHM_AGENT_PARALLELISM:String([1,2,3,4].includes(c.agentParallelism)?c.agentParallelism:1),
    LHM_LLM_PROVIDER:c.provider==="deepseek"?"deepseek":"openrouter",
    OPENROUTER_API_KEY:unwrapSecret(c.openrouterKey),
    DEEPSEEK_API_KEY:unwrapSecret(c.deepseekKey)
  };
}
function nativeEnv(runtimeEnv) {
  // Deliberately never pass AI provider credentials to the native runner.
  const safeKeys=["SYSTEMROOT","WINDIR","PATH","PATHEXT","TEMP","TMP",
    "APPDATA","LOCALAPPDATA","USERPROFILE","HOMEDRIVE","HOMEPATH",
    "PROGRAMFILES","PROGRAMFILES(X86)","COMMONPROGRAMFILES","COMSPEC"];
  const env={};
  for(const key of safeKeys) if(process.env[key]) env[key]=process.env[key];
  for(const key of ["LHM_COMPUTER_RUNNER_TOKEN","LHM_COMPUTER_HOME",
    "LHM_COMPUTER_BROWSER_CHANNEL","LHM_DESKTOP_NATIVE",
    "LHM_DESKTOP_ALLOWED_APPS","LHM_COMPUTER_ALLOWED_HOSTS",
    "LHM_SANDBOX_ENABLED"])
    env[key]=runtimeEnv[key];
  return env;
}
function runtimeCommand(mode) {
  if (app.isPackaged) {
    const file=path.join(process.resourcesPath,"runtime","lhm-runtime.exe");
    if (!fs.existsSync(file)) throw new Error("Missing packaged Python runtime: "+file);
    return {binary:file,args:[mode]};
  }
  const embedded=path.join(PROJECT_ROOT,".venv","Scripts","python.exe");
  const python=process.env.LHM_DESKTOP_PYTHON ||
    (fs.existsSync(embedded)?embedded:(process.platform==="win32"?"python":"python3"));
  return {binary:python,args:[path.join(PROJECT_ROOT,"desktop","runtime.py"),mode]};
}
function writeLog(name,line) {
  const folder=path.join(app.getPath("userData"),"logs");
  fs.mkdirSync(folder,{recursive:true});
  fs.appendFileSync(path.join(folder,name+".log"),line.slice(0,2000),{encoding:"utf8"});
}
function launchService(name,env) {
  const cmd=runtimeCommand(name);
  const child=spawn(cmd.binary,cmd.args,{
    cwd:app.isPackaged?app.getPath("userData"):PROJECT_ROOT,
    env,stdio:["ignore","pipe","pipe"],windowsHide:true,
    shell:false
  });
  children.set(name,child);
  child.stdout.on("data",buf=>writeLog(name,buf.toString()));
  child.stderr.on("data",buf=>writeLog(name,buf.toString()));
  child.on("error",error=>writeLog(name,"STARTUP ERROR "+error.message+"\n"));
  child.on("exit",(code,signal)=>{
    writeLog(name,"EXIT "+String(code)+" "+String(signal)+"\n");
    children.delete(name);
    if(!shutdown && mainWindow && !mainWindow.isDestroyed()) {
      dialog.showMessageBox(mainWindow,{type:"warning",title:"Dịch vụ Robot đã dừng",
        message:"Thành phần "+name+" đã dừng. Xem nhật ký để kiểm tra.",
        detail:path.join(app.getPath("userData"),"logs",name+".log")}).catch(()=>{});
    }
  });
  return child;
}
function terminate(name) {
  const child=children.get(name);
  if(child && child.exitCode===null) {
    try {child.kill();} catch {}
  }
}
function stopAll() {
  if(shutdown)return;
  shutdown=true;
  for(const k of ["worker","native","api"])terminate(k);
}
function backendFetch(url,token,method="GET",body=null) {
  return new Promise((resolve,reject)=>{
    const req=http.request(url,{method,timeout:1600,
      headers:{"Authorization":"Bearer "+token,
        ...(body?{"Content-Type":"application/json"}:{})}},res=>{
      const chunks=[];
      res.on("data",b=>chunks.push(b));
      res.on("end",()=>resolve({
        status:res.statusCode,
        text:Buffer.concat(chunks).toString("utf8").slice(0,5000)
      }));
    });
    req.on("error",reject);
    req.on("timeout",()=>req.destroy(new Error("Local service timeout")));
    if(body)req.write(JSON.stringify(body));
    req.end();
  });
}
async function waitBackend() {
  for(let i=0;i<90;i++){
    if(shutdown)throw new Error("Application is shutting down");
    try {
      const h=await backendFetch(BACKEND_ORIGIN+"/health",adminToken);
      if(h.status===200)return;
      if(h.status===401)throw new Error("Port 7770 belongs to a different server");
    }catch(e){
      if(String(e.message).includes("different server"))throw e;
    }
    await new Promise(resolve=>setTimeout(resolve,400));
  }
  throw new Error("LHM backend did not become healthy. See the API runtime log.");
}
function sameOrigin(raw) {
  try {return new URL(raw).origin===BACKEND_ORIGIN;}
  catch {return false;}
}
function openPage(route) {
  if(!mainWindow || mainWindow.isDestroyed())return;
  if(!Object.values(ROUTES).includes(route))return;
  mainWindow.loadURL(BACKEND_ORIGIN+route).catch(error=>
    writeLog("electron","Navigation failed "+error.message+"\n"));
}
function createMain() {
  mainWindow=new BrowserWindow({
    width:1440,height:920,minWidth:900,minHeight:620,show:false,
    backgroundColor:"#112339",autoHideMenuBar:true,
    webPreferences:{
      nodeIntegration:false,contextIsolation:true,sandbox:true,
      webSecurity:true,allowRunningInsecureContent:false,
      webviewTag:false,devTools:!app.isPackaged
    }
  });
  mainWindow.webContents.setWindowOpenHandler(()=>({action:"deny"}));
  mainWindow.webContents.on("will-navigate",(event,url)=>{
    if(!sameOrigin(url))event.preventDefault();
  });
  mainWindow.once("ready-to-show",()=>mainWindow.show());
  mainWindow.loadFile(path.join(__dirname,"splash.html"));
  mainWindow.on("closed",()=>{mainWindow=null;});
}
function createSettings() {
  if(settingsWindow&&!settingsWindow.isDestroyed()){settingsWindow.focus();return;}
  settingsWindow=new BrowserWindow({
    width:640,height:710,parent:mainWindow,modal:false,
    backgroundColor:"#f4f7fb",autoHideMenuBar:true,
    webPreferences:{
      nodeIntegration:false,contextIsolation:true,sandbox:true,
      webSecurity:true,webviewTag:false,
      preload:path.join(__dirname,"settings-preload.cjs")
    }
  });
  settingsWindow.webContents.setWindowOpenHandler(()=>({action:"deny"}));
  settingsWindow.webContents.on("will-navigate",(event,url)=>event.preventDefault());
  settingsWindow.loadFile(path.join(__dirname,"settings.html"));
  settingsWindow.on("closed",()=>settingsWindow=null);
}
function makeMenu() {
  const go=Object.entries(ROUTES).map(([label,route])=>({
    label,click:()=>openPage(route)
  }));
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {label:"LHM Robot",submenu:[
      ...go,{type:"separator"},
      {label:"Thiết lập Robot…",click:createSettings},
      {label:"Tạm dừng khẩn cấp",click:async()=>{
        try {
          const result=await backendFetch(NATIVE_ORIGIN+"/v1/pause",runnerToken,
                                         "POST",{paused:true});
          nativePaused=result.status===200;
          terminate("worker");
          dialog.showMessageBoxSync(mainWindow,{
            type:"warning",title:"Dừng khẩn cấp",
            message:"Worker đã dừng, native runner nhận yêu cầu tạm dừng.",
            detail:"Thao tác đã bắt đầu có thể đã thực hiện xong. Trạng thái native: "+result.status
          });
        }catch(e){
          terminate("worker");
          dialog.showErrorBox("Dừng khẩn cấp","Đã dừng worker; không thể xác minh native controller: "+e.message);
        }
      }},
      {label:"Khởi chạy lại ứng dụng",click:()=>{app.relaunch();app.quit();}},
      {type:"separator"},{role:"quit"}
    ]},
    {label:"View",submenu:[{role:"reload"},{role:"togglefullscreen"}]}
  ]));
}
function secureSession() {
  const s=session.defaultSession;
  s.setPermissionRequestHandler((_wc,_permission,callback)=>callback(false));
  s.setPermissionCheckHandler(()=>false);
  s.webRequest.onBeforeSendHeaders({urls:[BACKEND_ORIGIN+"/*"]},(details,callback)=>{
    details.requestHeaders.Authorization="Bearer "+adminToken;
    callback({requestHeaders:details.requestHeaders});
  });
  s.webRequest.onHeadersReceived({urls:[BACKEND_ORIGIN+"/*"]},(details,callback)=>{
    // Existing LHM templates use inline JS; migration to nonces is required
    // before tightening this further.
    const csp="default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "+
      "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "+
      "font-src 'self' data:; connect-src 'self'; object-src 'none'; "+
      "base-uri 'none'; frame-src 'none'; form-action 'self'";
    callback({responseHeaders:{...details.responseHeaders,
      "Content-Security-Policy":[csp]}});
  });
}
function settingsIPC(){
  ipcMain.handle("settings:test-openrouter",async event=>{
    if(!settingsWindow || event.sender.id!==settingsWindow.webContents.id)
      throw Error("Not settings window");
    const encrypted=readRawSettings().openrouterKey;
    const key=unwrapSecret(encrypted);
    if(!key)
      throw Error("Chưa có OpenRouter API key đã lưu. Lưu khóa và khởi động lại phần mềm trước khi kiểm tra.");
    // Read only from Windows safeStorage, never renderer IPC.
    // Fixed model openrouter/free; NEVER inject a paid fallback.
    return await requestFreeCompletion(key);
  });
  ipcMain.handle("settings:load",event=>{
    if(!settingsWindow || event.sender.id!==settingsWindow.webContents.id)throw Error("Not settings window");
    return settingsForUI();
  });
  ipcMain.handle("settings:save",async(event,data)=>{
    if(!settingsWindow || event.sender.id!==settingsWindow.webContents.id)throw Error("Not settings window");
    saveSettings(data);
    if(mainWindow) await dialog.showMessageBox(mainWindow,{
      type:"info",message:"Đã lưu. Khởi động lại ứng dụng để kích hoạt thay đổi.",
      buttons:["Khởi động lại","Để sau"],defaultId:0
    }).then(r=>{if(r.response===0){app.relaunch();app.quit();}});
    return {saved:true};
  });
}
app.on("before-quit",stopAll);
app.on("window-all-closed",()=>{if(process.platform!=="darwin")app.quit();});
app.on("activate",()=>{if(!mainWindow)createMain();});
app.whenReady().then(async()=>{
  app.setAppUserModelId("vn.lhm.robot.desktop");
  if(!app.requestSingleInstanceLock()){app.quit();return;}
  app.on("second-instance",()=>{if(mainWindow){mainWindow.restore();mainWindow.focus();}});
  adminToken=crypto.randomBytes(32).toString("base64url");
  runnerToken=crypto.randomBytes(32).toString("base64url");
  secureSession();settingsIPC();makeMenu();createMain();
  if(process.platform!=="win32"){
    dialog.showErrorBox("Chỉ hỗ trợ Windows","LHM Robot Desktop Native hiện yêu cầu Windows 10/11.");
    return;
  }
  try{
    const env=envForRuntime();
    launchService("native",nativeEnv(env));
    launchService("api",env);
    await waitBackend();
    launchService("worker",env);
    openPage("/office3d");
  }catch(e){
    writeLog("electron","Startup "+e.message+"\n");
    dialog.showMessageBox(mainWindow,{
      type:"error",title:"Không khởi động được LHM Robot",
      message:e.message,
      detail:"Mở Nhật ký tại "+path.join(app.getPath("userData"),"logs")
    }).catch(()=>{});
  }
}).catch(error=>dialog.showErrorBox("Startup error",error.message));
