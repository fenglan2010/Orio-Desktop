const { app, BrowserWindow, shell, dialog } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const fs = require("fs");
const {
  findVenvPython,
  resolveBackendDir,
  resolveStaticRoot,
  pathAnchorsForVenv,
  defaultDistribution,
  STATIC_MISSING_MESSAGE_ZH,
} = require("./lib/paths");
const {
  resolvePythonLaunch,
  buildBackendSpec,
  PYTHON_MISSING_MESSAGE_ZH,
} = require("./lib/python-resolve");

const API_PORT = process.env.OIRO_API_PORT || "8765";
let apiProcess = null;

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
  const venvPython = findVenvPython(
    pathAnchorsForVenv({
      isPackaged: app.isPackaged,
      resourcesPath: process.resourcesPath,
      execPath: process.execPath,
      appDir: __dirname,
    })
  );
  const pythonLaunch = resolvePythonLaunch({ venvPython });
  const spec = buildBackendSpec({ pythonLaunch, apiPort: API_PORT });
  if (!spec) {
    return { error: PYTHON_MISSING_MESSAGE_ZH };
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

function startBackend() {
  const spec = backendCmd();
  if (spec.error) {
    showFatalBackendError(spec.error);
    return;
  }
  const env = { ...process.env };
  const sep = path.delimiter;
  env.PYTHONPATH = env.PYTHONPATH
    ? `${spec.cwd}${sep}${env.PYTHONPATH}`
    : spec.cwd;

  const stdio = app.isPackaged ? "pipe" : "inherit";
  apiProcess = spawn(spec.cmd, spec.args, {
    cwd: spec.cwd,
    env,
    stdio,
  });
  if (apiProcess.stdout) {
    apiProcess.stdout.on("data", (chunk) => process.stdout.write(chunk));
  }
  if (apiProcess.stderr) {
    apiProcess.stderr.on("data", (chunk) => process.stderr.write(chunk));
  }
  apiProcess.on("error", (err) => {
    console.error("OIRO API spawn failed:", err);
    const detail =
      err && err.code === "ENOENT"
        ? PYTHON_MISSING_MESSAGE_ZH
        : String(err.message || err);
    showFatalBackendError(detail);
  });
  apiProcess.on("exit", (code) => {
    console.log(`OIRO API exited with code ${code}`);
  });
}

function createWindow() {
  if (
    app.isPackaged &&
    !process.env.OIRO_DEV_URL &&
    !process.env.OIRO_STATIC_ROOT
  ) {
    showFatalBackendError(STATIC_MISSING_MESSAGE_ZH);
  }
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
    },
  });
  const devUrl = process.env.OIRO_DEV_URL;
  if (devUrl) {
    win.loadURL(devUrl);
  } else {
    win.loadURL(`http://127.0.0.1:${API_PORT}/`);
  }
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
}

app.whenReady().then(() => {
  ensureBackendEnv();
  startBackend();
  createWindow();
});

app.on("window-all-closed", () => {
  if (apiProcess) apiProcess.kill();
  if (process.platform !== "darwin") app.quit();
});