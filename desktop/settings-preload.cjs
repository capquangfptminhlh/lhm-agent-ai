"use strict";
const {contextBridge,ipcRenderer} = require("electron");
// Settings-only bridge: never inject into localhost pages/3D/Agent sessions.
contextBridge.exposeInMainWorld("lhmSettings", Object.freeze({
  load:()=>ipcRenderer.invoke("settings:load"),
  readiness:()=>ipcRenderer.invoke("settings:readiness"),
  save:data=>ipcRenderer.invoke("settings:save",data),
  testOpenRouter:()=>ipcRenderer.invoke("settings:test-openrouter"),
  opencliStatus:()=>ipcRenderer.invoke("settings:opencli-status"),
  opencliExtension:()=>ipcRenderer.invoke("settings:opencli-extension")
}));
