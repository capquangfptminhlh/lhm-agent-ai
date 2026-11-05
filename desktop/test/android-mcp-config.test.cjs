'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {test}=require('node:test');
const {parseAndroidMcp,androidMcpEnv,nodePath,ROLES}=require('../android-mcp-config.cjs');

function mockFs(exists=true){
 return {
  lstatSync(name) {
   assert.equal(path.win32.basename(name),'lhm-android-mcp.js');
   if(!exists)throw Error('ENOENT');
   return {isFile:()=>true,isSymbolicLink:()=>false};
  },
  statSync(name) {
   assert(name.includes('opencli-runtime'));
   return {isFile:()=>true};
  }
 };
}
const cfg={
 androidMcpEnabled:true,
 androidMcpTapEnabled:true,
 androidMcpRepo:'C:\\LHM\\ggminh',
 androidMcpSerials:'emulator-5554,emulator-5556',
 androidMcpRoles:'research,quality'
};
test('off by default with no devices or roles',()=>{
 const c=parseAndroidMcp({}, {platform:'linux'});
 assert.equal(c.androidMcpEnabled,false);
 assert.deepEqual(c.androidMcpSerials,[]);
 const env=androidMcpEnv({},{platform:'linux'});
 assert.equal(env.LHM_ANDROID_MCP_ENABLED,'0');
 assert.equal(env.LHM_ANDROID_MCP_TAP_ENABLED,'0');
 assert.equal(env.LHM_ANDROID_MCP_ALLOWED_SERIALS,'');
});
test('explicit roles and devices yield exactly one safe source entry',()=>{
 const c=parseAndroidMcp(cfg,{platform:'win32',fs:mockFs()});
 assert.deepEqual(c.androidMcpSerials,['emulator-5554','emulator-5556']);
 assert.deepEqual(c.androidMcpRoles,['research','quality']);
 const env=androidMcpEnv(cfg,{platform:'win32',fs:mockFs(),
  packaged:true,resourcesPath:'C:\\Program Files\\LHM\\resources'});
 assert.equal(env.LHM_ANDROID_MCP_ENABLED,'1');
 assert.equal(env.LHM_ANDROID_MCP_TAP_ENABLED,'1');
 assert(env.LHM_ANDROID_MCP_ENTRY.endsWith('\\bin\\lhm-android-mcp.js'));
 assert(env.LHM_ANDROID_MCP_NODE.endsWith('node.exe'));
 assert.equal(env.LHM_ANDROID_MCP_ALLOWED_ROLES,'research,quality');
 assert.equal(env.LHM_ANDROID_MCP_ALLOWED_SERIALS,'emulator-5554,emulator-5556');
});
test('reject unsafe repo, missing bridge, wildcard serials and roles',()=>{
 const options={platform:'win32',fs:mockFs()};
 for(const bad of [
  {androidMcpRepo:'\\\\server\\share\\ggminh'},
  {androidMcpRepo:'../ggminh'},
  {androidMcpRepo:'C:\\LHM\\ggminh',androidMcpSerials:'emulator-*'},
  {androidMcpEnabled:false,androidMcpTapEnabled:true},
  {androidMcpEnabled:true,androidMcpTapEnabled:true,androidMcpSerials:'PHYSICAL123'},
  {androidMcpRepo:'C:\\LHM\\ggminh',androidMcpRoles:'all'},
  {androidMcpRepo:'C:\\LHM\\ggminh',androidMcpRoles:''},
  {androidMcpRepo:'C:\\LHM\\ggminh',androidMcpSerials:''}
 ])assert.throws(()=>parseAndroidMcp({...cfg,...bad},options));
 assert.throws(()=>parseAndroidMcp(cfg,{platform:'linux',fs:mockFs()}),/Windows/);
 assert.throws(()=>parseAndroidMcp(cfg,{platform:'win32',fs:mockFs(false)}),/Không thấy/);
 assert.equal(ROLES.length,16);
});
test('packaged mode never resolves node from arbitrary PATH',()=>{
 assert.equal(nodePath({packaged:true,resourcesPath:'C:\\fake',fs:{
  statSync:()=>{throw Error('nope')}
 }}),'');
});
