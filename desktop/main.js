const { app, BrowserWindow, shell, dialog } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const fs = require("fs");
const {
  findVenvPython,
  resolveBackendDir,
  resolveStaticRoot,
  resolveBundledPython,
  pathAnchorsForVenv,
  defaultDistribution,
  STATIC_MISSING_MESSAGE_ZH,
} = require("./lib/paths");
const {
  resolvePythonForBackend,
  buildBackendSpec,
  PYTHON_MISSING_MESSAGE_ZH,
  PYTHON_MISSING_PACKAGED_ZH,
} = require("./lib/python-resolve");
const { waitForApiHealth, waitForRootHtml } = require("./lib/api-ready");

const API_PORT = process.env.OIRO_API_PORT || "8765";
let apiProcess = null;
let mainWindow = null;
let backendExitCode = null;
let backendSpawnError = null;

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
}

function userDataRoot() {
  return path.join(app.getPath("userData"), "oiro");
}

function ensureBackendEnv() {
  const dataDir = path.join(userDataRoot(), "data");
  fs.mkdirSync(dataDir, { recursive: true });
  process.env.OIRO_DATA_DIR = dataDir;
  process.env.OIRO_DISTRIBUTION =
    process.env.OIRO_DISTRIBUTION || defaultDistribution(app.isPackaged);
  process.env.DATABASE_URL = `sqlite:///${path.join(dataDir, "oiro.db")}`;
  const staticRoot = resolveStaticRoot({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    appDir: __dirname,
  });
  if (staticRoot) {
    process.env.OIRO_STATIC_ROOT = staticRoot;
  } else if (app.isPackaged && !process.env.OIRO_DEV_URL) {
    process.env.OIRO_STATIC_ROOT = "";
  }
}

function backendCmd() {
  const backendDir = resolveBackendDir({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    appDir: __dirname,
  });
  const bundledPython = resolveBundledPython({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
  });
  const venvPython = app.isPackaged
    ? null
    : findVenvPython(
        pathAnchorsForVenv({
          isPackaged: app.isPackaged,
          resourcesPath: process.resourcesPath,
          execPath: process.execPath,
          appDir: __dirname,
        })
      );
  const pythonLaunch = resolvePythonForBackend({
    isPackaged: app.isPackaged,
    bundledPython,
    venvPython,
  });
  const spec = buildBackendSpec({ pythonLaunch, apiPort: API_PORT });
  if (!spec) {
    const message = app.isPackaged ? PYTHON_MISSING_PACKAGED_ZH : PYTHON_MISSING_MESSAGE_ZH;
    return { error: message };
  }
  return {
    cmd: spec.cmd,
    args: spec.args,
    cwd: backendDir,
  };
}

function showFatalBackendError(message) {
  dialog.showErrorBox("OIRO 无法启动后端", message);
}

function errorHtml(message) {
  const escaped = String(message)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\n/g, "<br/>");
  return `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"/><title>OIRO</title>
<style>body{font-family:"Segoe UI",sans-serif;padding:2rem;max-width:640px;margin:auto;line-height:1.6;color:#222;}
h1{font-size:1.25rem;color:#b00020;}</style></head><body>
<h1>OIRO 无法启动</h1><p>${escaped}</p>
<p>请关闭本窗口后重试。若问题持续，请安装最新版 OIRO 桌面安装包。</p></body></html>`;
}

function showMessageWindow(message) {
  const win = new BrowserWindow({
    width: 720,
    height: 480,
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true },
  });
  win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(errorHtml(message))}`);
  return win;
}

function startBackend(spec) {
  const env = { ...process.env };
  const sep = path.delimiter;
  env.PYTHONPATH = env.PYTHONPATH
    ? `${spec.cwd}${sep}${env.PYTHONPATH}`
    : spec.cwd;

  backendExitCode = null;
  backendSpawnError = null;

  const stdio = app.isPackaged ? "pipe" : "inherit";
  apiProcess = spawn(spec.cmd, spec.args, {
    cwd: spec.cwd,
    env,
    stdio,
    windowsHide: true,
  });

  let stderrTail = "";
  if (apiProcess.stdout) {
    apiProcess.stdout.on("data", (chunk) => process.stdout.write(chunk));
  }
  if (apiProcess.stderr) {
    apiProcess.stderr.on("data", (chunk) => {
      stderrTail = (stderrTail + chunk.toString()).slice(-4000);
      process.stderr.write(chunk);
    });
  }
  apiProcess.on("error", (err) => {
    console.error("OIRO API spawn failed:", err);
    backendSpawnError = err;
    const detail =
      err && err.code === "ENOENT"
        ? app.isPackaged
          ? PYTHON_MISSING_PACKAGED_ZH
          : PYTHON_MISSING_MESSAGE_ZH
        : String(err.message || err);
    showFatalBackendError(detail);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(errorHtml(detail))}`);
    } else {
      showMessageWindow(detail);
    }
  });
  apiProcess.on("exit", (code) => {
    backendExitCode = code;
    console.log(`OIRO API exited with code ${code}`);
    if (code !== 0 && code !== null && mainWindow && !mainWindow.isDestroyed()) {
      const tail = stderrTail ? `\n\n最近日志：\n${stderrTail.slice(-1200)}` : "";
      const msg = `后端进程已退出（代码 ${code}）。${tail}`;
      showFatalBackendError(msg);
      mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(errorHtml(msg))}`);
    }
  });
  apiProcess.stderrTail = () => stderrTail;
  return true;
}

function createWindow() {
  if (
    app.isPackaged &&
    !process.env.OIRO_DEV_URL &&
    !process.env.OIRO_STATIC_ROOT
  ) {
    showFatalBackendError(STATIC_MISSING_MESSAGE_ZH);
    return showMessageWindow(STATIC_MISSING_MESSAGE_ZH);
  }
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
    },
  });
  const devUrl = process.env.OIRO_DEV_URL;
  if (devUrl) {
    mainWindow.loadURL(devUrl);
  } else {
    mainWindow.loadURL(`http://127.0.0.1:${API_PORT}/`);
  }
  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
  return mainWindow;
}

async function bootstrap() {
  ensureBackendEnv();
  const spec = backendCmd();
  if (spec.error) {
    showFatalBackendError(spec.error);
    showMessageWindow(spec.error);
    return;
  }
  startBackend(spec);

  const health = await waitForApiHealth({ port: API_PORT });
  if (!health.ok) {
    if (backendSpawnError || (backendExitCode !== null && backendExitCode !== 0)) {
      const tail = apiProcess?.stderrTail?.() || "";
      health.message = [health.message, tail ? `日志：\n${tail.slice(-1200)}` : ""]
        .filter(Boolean)
        .join("\n\n");
    }
    showFatalBackendError(health.message);
    showMessageWindow(health.message);
    return;
  }

  const htmlReady = await waitForRootHtml({ port: API_PORT });
  if (!htmlReady.ok) {
    showFatalBackendError(htmlReady.message);
    showMessageWindow(htmlReady.message);
    return;
  }

  createWindow();
}

app.whenReady().then(() => bootstrap());

app.on("second-instance", () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

app.on("window-all-closed", () => {
  if (apiProcess) apiProcess.kill();
  if (process.platform !== "darwin") app.quit();
});