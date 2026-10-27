"use strict";
const {contextBridge,ipcRenderer} = require("electron");
// Settings-only bridge: never inject into localhost pages/3D/Agent sessions.
contextBridge.exposeInMainWorld("lhmSettings", Object.freeze({
  load:()=>ipcRenderer.invoke("settings:load"),
  save:data=>ipcRenderer.invoke("settings:save",data)
}));
