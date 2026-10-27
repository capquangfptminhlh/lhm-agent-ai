"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const root=path.resolve(__dirname,"..");

test("Electron uses hardened renderer and does not expose Node to web pages",()=>{
 const main=fs.readFileSync(path.join(root,"main.cjs"),"utf8");
 assert.match(main,/nodeIntegration:false/);
 assert.match(main,/contextIsolation:true/);
 assert.match(main,/sandbox:true/);
 assert.match(main,/webSecurity:true/);
 assert.match(main,/webviewTag:false/);
 assert.match(main,/setWindowOpenHandler\(\(\)=>\(\{action:"deny"\}\)\)/);
 assert.match(main,/will-navigate/);
 assert.match(main,/sameOrigin\(url\)/);
 assert.match(main,/setPermissionRequestHandler/);
});
test("Native runner receives no cloud secrets and no main env",()=>{
 const main=fs.readFileSync(path.join(root,"main.cjs"),"utf8");
 const native=main.split("function nativeEnv(runtimeEnv)")[1].split("function runtimeCommand(")[0];
 for(const secret of ["OPENROUTER_API_KEY","DEEPSEEK_API_KEY","LHM_ADMIN_TOKEN"]){
   assert.doesNotMatch(native,new RegExp(secret));
 }
 assert.match(native,/LHM_COMPUTER_RUNNER_TOKEN/);
 assert.match(main,/nativeEnv\(env\)/);
});
test("Only settings window can access the settings IPC",()=>{
 const main=fs.readFileSync(path.join(root,"main.cjs"),"utf8");
 assert.match(main,/event\.sender\.id!==settingsWindow\.webContents\.id/g);
 const preload=fs.readFileSync(path.join(root,"settings-preload.cjs"),"utf8");
 assert.match(preload,/contextBridge\.exposeInMainWorld\("lhmSettings"/);
 assert.doesNotMatch(preload,/execute|shell\.openExternal|spawn|eval/);
});
test("EXE runtime, Electron installer and UI assets are packaged",()=>{
 const json=JSON.parse(fs.readFileSync(path.join(root,"package.json"),"utf8"));
 assert.equal(json.main,"main.cjs");
 assert.equal(json.devDependencies.electron,"45.0.0");
 assert.equal(json.build.win.target[0].target,"nsis");
 assert.deepEqual(json.build.extraResources[0].to,"runtime");
 for(const item of ["settings.js","settings.html","splash.html","main.cjs","settings-preload.cjs"]){
   assert.ok(json.build.files.includes(item),"missing packaged "+item);
   assert.ok(fs.existsSync(path.join(root,item)));
 }
});
test("Windows controller accepts only explicitly allowlisted HWND and is separately paused",()=>{
 const runner=fs.readFileSync(path.join(root,"..","computer_executor.py"),"utf8");
 const native=fs.readFileSync(path.join(root,"..","windows_native.py"),"utf8");
 assert.match(runner,/WINDOWS_TOOLS/);
 assert.match(runner,/PAUSED and tool in MUTATIONS/);
 assert.match(runner,/native.*execute|from windows_native import execute/);
 // Allowlist check (exact exe, or the fixed UWP alias calculatorapp.exe -> calc.exe).
 assert.match(native,/if not _allowed_process\(name,_allowlist\(\)\):/);
 assert.match(native,/return name in allowlist or PROCESS_ALIASES\.get\(name\) in allowlist/);
 assert.match(native,/Windows denied foreground focus/);
 assert.match(native,/Window executable is not on the desktop allowlist/);
});
test("First run needs only the key: menu visible, Settings auto-opens, no browser in default apps",()=>{
 const main=fs.readFileSync(path.join(root,"main.cjs"),"utf8");
 const mainWindow=main.split("function createMain()")[1].split("function createSettings()")[0];
 assert.match(mainWindow,/autoHideMenuBar:false/);
 assert.match(main,/if\(!readRawSettings\(\)\.openrouterKey\)createSettings\(\)/);
 const defaults=main.match(/DEFAULT_ALLOWED_APPS=Object\.freeze\((\[[^\]]*\])\)/);
 assert.ok(defaults,"default app allowlist declared");
 const apps=JSON.parse(defaults[1]);
 assert.deepEqual(apps,["notepad.exe","explorer.exe","calc.exe"]);
 for(const browser of ["msedge.exe","chrome.exe","firefox.exe"])assert.ok(!apps.includes(browser));
 // Opt-in safety switches keep their off-by-default behaviour.
 assert.match(main,/LHM_COMPUTER_TRUSTED_ENABLED:c\.trustedEnabled===true\?"1":"0"/);
 assert.match(main,/LHM_DESKTOP_FULL_SCREEN_ENABLED:c\.fullScreenEnabled===true\?"1":"0"/);
});
