"use strict";
/** Verify and stage bundled Node/OpenCLI before NSIS installer snapshots win-unpacked. */
const fs=require("node:fs");
const path=require("node:path");

module.exports=async function ensureOpenCLIBundle(context) {
  if(context.electronPlatformName!=="win32") return;
  const project=context.packager.projectDir;
  const source=path.join(project,"vendor","opencli-runtime");
  const target=path.join(context.appOutDir,"resources","opencli-runtime");
  const node=path.join(source,"node.exe");
  const packageDir=path.join(source,"node_modules","@jackwener","opencli");
  const entry=path.join(packageDir,"dist","src","main.js");
  const metadata=path.join(packageDir,"package.json");
  if(![node,entry,metadata].every(p=>fs.existsSync(p))){
    throw new Error("LHM installer build missing the Node/OpenCLI 2.0.2 vendor runtime");
  }
  const meta=JSON.parse(fs.readFileSync(metadata,"utf8"));
  if(meta.name!=="@jackwener/opencli" || meta.version!=="2.0.2"){
    throw new Error("LHM installer vendor OpenCLI version mismatch");
  }
  // extraResources is the primary path. A direct verified stage makes this
  // resilient to builder filtering of nested node_modules on Windows.
  const installedNode=path.join(target,"node.exe");
  const installedCLI=path.join(target,"node_modules","@jackwener","opencli","dist","src","main.js");
  if(!fs.existsSync(installedNode) || !fs.existsSync(installedCLI)){
    fs.mkdirSync(target,{recursive:true});
    fs.cpSync(source,target,{recursive:true,force:true});
  }
  if(!fs.existsSync(installedNode) || !fs.existsSync(installedCLI)){
    throw new Error("LHM installer would omit bundled OpenCLI (fail release before NSIS)");
  }
  process.stdout.write("LHM Windows afterPack: Node/OpenCLI 2.0.2 verified in win-unpacked resources\n");
};
