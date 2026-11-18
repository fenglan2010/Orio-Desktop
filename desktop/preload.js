const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("oiroDesktop", {
  mode: "desktop",
  apiBase: process.env.OIRO_API_BASE || "",
});