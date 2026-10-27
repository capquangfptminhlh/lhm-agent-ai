"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const {readiness}=require("../readiness.cjs");
const root=path.resolve(__dirname,"..");
test("No silent ready status when keys or Windows permissions are missing",()=>{
 const s=readiness({apiStatus:200,nativeStatus:200,workerRunning:true,
                   keyStored:false,keyUnlocked:false,allowedApps:0,allowedHosts:0});
 assert.equal(s.agentReady,false);
 assert.equal(s.screenReady,false);
 assert.equal(s.browserReady,false);
 assert.match(s.services.find(x=>x.id==="key").detail,/API key/);
 assert.match(s.services.find(x=>x.id==="apps").detail,/chưa cấp quyền/i);
});
test("Health status exposes no secrets and distinguishes running from tested",()=>{
 const s=readiness({apiStatus:200,nativeStatus:200,workerRunning:true,
                   keyStored:true,keyUnlocked:true,allowedApps:3,allowedHosts:0});
 assert.equal(s.agentReady,true);
 assert.equal(s.screenReady,true);
 assert.equal(s.browserReady,false);
 assert.match(JSON.stringify(s),/chưa xác minh|hãy bấm thử/i);
 assert.doesNotMatch(JSON.stringify(s),/OPENROUTER_API_KEY|Authorization|Bearer/);
});
test("Settings dashboard auto-opens first run but only the trusted window gets health",()=>{
 const main=fs.readFileSync(path.join(root,"main.cjs"),"utf8");
 const bridge=fs.readFileSync(path.join(root,"settings-preload.cjs"),"utf8");
 assert.match(main,/if\(!readRawSettings\(\)\.openrouterKey\)createSettings\(\)/);
 assert.match(main,/ipcMain\.handle\("settings:readiness"/);
 assert.match(main,/event\.sender\.id!==settingsWindow\.webContents\.id/);
 assert.match(bridge,/readiness:\(\)=>ipcRenderer\.invoke\("settings:readiness"\)/);
 assert.doesNotMatch(bridge,/openrouterKey|LHM_ADMIN_TOKEN|runnerToken/);
});
test("Quick start grants 3 named desktop apps only after user click",()=>{
 const js=fs.readFileSync(path.join(root,"settings.js"),"utf8");
 const html=fs.readFileSync(path.join(root,"settings.html"),"utf8");
 assert.match(js,/\$\("quickStart"\)\.onclick=/);
 assert.match(js,/notepad\.exe, explorer\.exe, calc\.exe/);
 assert.match(js,/\$\("save"\)\.click\(\)/);
 assert.match(html,/id="quickStart"/);
 assert.match(html,/<details id="advanced">/);
 assert.match(html,/id="readiness"/);
 assert.match(html,/nhạy cảm/);
});
test("Versioned installer includes required readiness module",()=>{
 const pkg=JSON.parse(fs.readFileSync(path.join(root,"package.json"),"utf8"));
 assert.notEqual(pkg.version,"0.1.0");
 assert.ok(pkg.build.files.includes("readiness.cjs"));
});
