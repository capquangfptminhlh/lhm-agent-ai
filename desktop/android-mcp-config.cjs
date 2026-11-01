"use strict";
/**
 * Per-operator Android MCP configuration for Windows Robot Desktop.
 * Source checkout is explicitly picked by the operator; never model input.
 */
const fs = require("node:fs");
const path = require("node:path");

const ROLES = Object.freeze([
  "director","research","strategy","lead","content","ads","crm","sales",
  "quality","analytics","operations","finance","hr","procurement","support","compliance"
]);
const ROLE_SET=new Set(ROLES);
const SERIAL=/^[A-Za-z0-9._:-]{1,128}$/;

function csv(raw) {
  if(typeof raw!=="string" || raw.length>2048) throw Error("Danh sách Android quá dài.");
  if(!raw.trim())return [];
  const values=raw.split(",").map(x=>x.trim());
  if(values.some(x=>!x))throw Error("Không được để mục trống trong danh sách Android.");
  return [...new Set(values)];
}
function parseAndroidMcp(input,options={}) {
  const enabled=input?.androidMcpEnabled===true;
  const root=String(input?.androidMcpRepo||"").trim();
  const serials=csv(input?.androidMcpSerials||"");
  const roles=csv(input?.androidMcpRoles||"");
  if(serials.length>8 || serials.some(s=>!SERIAL.test(s)))
    throw Error("Android MCP chỉ chấp nhận tối đa 8 serial ADB chính xác, không wildcard.");
  if(roles.some(r=>!ROLE_SET.has(r)))
    throw Error("Vai trò Android MCP không nằm trong danh sách 16 Agent được hỗ trợ.");
  if(root.length>500)throw Error("Đường dẫn mã nguồn Android MCP quá dài.");
  if(enabled) {
    if((options.platform||process.platform)!=="win32")
      throw Error("Android MCP chỉ được bật trên Windows Desktop.");
    if(!serials.length||!roles.length)
      throw Error("Bật Android MCP phải chọn ít nhất một serial và một vai trò Agent.");
    // Local drive only: reject network UNC paths and device namespaces.
    if(!/^[A-Za-z]:[\\/]/.test(root)||!path.win32.isAbsolute(root))
      throw Error("Chọn thư mục ggminh trên ổ đĩa Windows, không dùng đường dẫn mạng.");
    const entry=path.win32.join(root,"bin","lhm-android-mcp.js");
    const fileSystem=options.fs||fs;
    try {
      const stat=fileSystem.lstatSync(entry);
      if(!stat.isFile()||stat.isSymbolicLink())
        throw Error("entry is not a regular file");
    } catch {
      throw Error("Không thấy ggminh/bin/lhm-android-mcp.js. Chọn đúng bản mã nguồn có MCP.");
    }
  }
  return {
    androidMcpEnabled:enabled,
    androidMcpRepo:root,
    androidMcpSerials:serials,
    androidMcpRoles:roles
  };
}
function nodePath(options={}) {
  const filesystem=options.fs||fs;
  const packaged=options.packaged===true;
  const candidate=packaged
    ?path.join(options.resourcesPath||"","opencli-runtime","node.exe")
    :path.join(options.projectRoot||"","desktop","vendor","opencli-runtime","node.exe");
  try{if(filesystem.statSync(candidate).isFile())return candidate;}catch{}
  // Source mode may use Node installed by the operator. Packaged mode
  // refuses fallback to an arbitrary PATH executable.
  return packaged?"":"node";
}
function androidMcpEnv(config,options={}) {
  const parsed=parseAndroidMcp(config,{
    ...options,platform:options.platform||process.platform
  });
  if(!parsed.androidMcpEnabled) return {
    LHM_ANDROID_MCP_ENABLED:"0",
    LHM_ANDROID_MCP_ENTRY:"",
    LHM_ANDROID_MCP_NODE:"",
    LHM_ANDROID_MCP_ALLOWED_SERIALS:"",
    LHM_ANDROID_MCP_ALLOWED_ROLES:""
  };
  const node=nodePath(options);
  if(!node)throw Error("Bản Windows thiếu Node runtime đóng gói cho Android MCP.");
  return {
    LHM_ANDROID_MCP_ENABLED:"1",
    LHM_ANDROID_MCP_ENTRY:path.win32.join(parsed.androidMcpRepo,"bin","lhm-android-mcp.js"),
    LHM_ANDROID_MCP_NODE:node,
    LHM_ANDROID_MCP_ALLOWED_SERIALS:parsed.androidMcpSerials.join(","),
    LHM_ANDROID_MCP_ALLOWED_ROLES:parsed.androidMcpRoles.join(",")
  };
}
module.exports={ROLES,csv,parseAndroidMcp,nodePath,androidMcpEnv};
