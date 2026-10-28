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
test("OpenCLI Node and package are embedded in Windows installer; fixed extension shortcut",()=>{
 const pkg=JSON.parse(fs.readFileSync(path.join(root,"package.json"),"utf8"));
 assert.equal(pkg.build.extraResources[1].to,"opencli-runtime");
 const main=fs.readFileSync(path.join(root,"main.cjs"),"utf8");
 const settings=fs.readFileSync(path.join(root,"settings.html"),"utf8");
 const preload=fs.readFileSync(path.join(root,"settings-preload.cjs"),"utf8");
 assert.match(main,/LHM_OPENCLI_BUNDLE_DIR/);
 assert.match(main,/settings:opencli-extension/);
 assert.match(main,/chromewebstore\.google\.com\/detail\/opencli/);
 assert.match(settings,/id="opencliExtension"/);
 assert.match(preload,/opencliExtension:.*settings:opencli-extension/);
 assert.match(preload,/opencliProfiles:.*settings:opencli-profiles/);
 assert.match(main,/settings:opencli-profiles/);
 assert.match(main,/NATIVE_ORIGIN\+"\/v1\/opencli\/profiles"/);
 assert.match(settings,/id="opencliFindProfiles"/);
 assert.match(settings,/id="opencliProfileSelect"/);

});
test("OpenCLI status is settings-only and authenticated on Windows native runner",()=>{
 const main=fs.readFileSync(path.join(root,"main.cjs"),"utf8");
 const preload=fs.readFileSync(path.join(root,"settings-preload.cjs"),"utf8");
 const html=fs.readFileSync(path.join(root,"settings.html"),"utf8");
 assert.match(main,/settings:opencli-status/);
 assert.match(main,/event.sender.id!==settingsWindow.webContents.id/);
 assert.match(main,/NATIVE_ORIGIN\+"\/v1\/opencli\/status"/);
 assert.match(preload,/opencliStatus:.*settings:opencli-status/);
 assert.match(html,/id="opencliCheck"/);
});
test("OpenCLI is explicit opt-in with dedicated Chrome profile and host scope",()=>{
 const main=fs.readFileSync(path.join(root,"main.cjs"),"utf8");
 const html=fs.readFileSync(path.join(root,"settings.html"),"utf8");
 const js=fs.readFileSync(path.join(root,"settings.js"),"utf8");
 assert.match(main,/LHM_OPENCLI_ENABLED/);
 assert.match(main,/LHM_OPENCLI_PROFILE/);
 assert.match(main,/LHM_OPENCLI_ALLOWED_HOSTS/);
 assert.match(main,/opencliHosts\.some\(x=>!allowedHosts\.includes\(x\)\)/);
 for(const field of ["opencliEnabled","opencliProfile","opencliHosts"]){
   assert.match(html,new RegExp('id="'+field+'"'));
   assert.match(js,new RegExp(field));
 }
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
 assert.match(native,/name not in _allowlist/);
 assert.match(native,/Windows denied foreground focus/);
 assert.match(native,/Window executable is not on the desktop allowlist/);
});
