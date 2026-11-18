const { execFileSync } = require("child_process");
const path = require("path");

/**
 * Uvicorn CLI args. Use `backendDir` so embed/isolated Python finds `app` without PYTHONPATH.
 */
function uvicornModuleArgs(apiPort, backendDir) {
  const args = [
    "-m",
    "uvicorn",
    "app.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    String(apiPort),
  ];
  if (backendDir) {
    args.push("--app-dir", path.resolve(backendDir));
  }
  return args;
}

/** @deprecated use uvicornModuleArgs(port, backendDir) */
const UVICORN_MODULE_ARGS = (port) => uvicornModuleArgs(port);

function probeCommand(cmd, args) {
  try {
    execFileSync(cmd, args, { stdio: "ignore", windowsHide: true });
    return true;
  } catch {
    return false;
  }
}

/**
 * Resolve a Python launcher for the backend. On Windows never returns bare `python3` alone.
 */
function resolvePythonLaunch({ venvPython, platform = process.platform }) {
  if (venvPython) {
    return { cmd: venvPython, argsPrefix: [] };
  }

  if (platform === "win32") {
    const candidates = [
      { cmd: "python", probe: ["--version"] },
      { cmd: "py", probe: ["-3", "--version"] },
      { cmd: "python3", probe: ["--version"] },
    ];
    for (const { cmd, probe } of candidates) {
      if (probeCommand(cmd, probe)) {
        const argsPrefix = cmd === "py" ? ["-3"] : [];
        return { cmd, argsPrefix };
      }
    }
    return null;
  }

  if (probeCommand("python3", ["--version"])) {
    return { cmd: "python3", argsPrefix: [] };
  }
  if (probeCommand("python", ["--version"])) {
    return { cmd: "python", argsPrefix: [] };
  }
  return null;
}

function buildBackendSpec({ pythonLaunch, apiPort, backendDir }) {
  if (!pythonLaunch) {
    return null;
  }
  return {
    cmd: pythonLaunch.cmd,
    args: [...pythonLaunch.argsPrefix, ...uvicornModuleArgs(apiPort, backendDir)],
  };
}

/**
 * Packaged: bundled embed python only (no PATH / conda / source-tree venv required).
 * Dev: repo .venv first, then system python.
 */
function resolvePythonForBackend({
  isPackaged,
  bundledPython,
  venvPython,
  platform = process.platform,
}) {
  if (isPackaged) {
    if (bundledPython) {
      return { cmd: bundledPython, argsPrefix: [] };
    }
    return null;
  }
  if (venvPython) {
    return { cmd: venvPython, argsPrefix: [] };
  }
  return resolvePythonLaunch({ venvPython: null, platform });
}

const PYTHON_MISSING_MESSAGE_ZH =
  "未找到可用的 Python 环境。\n\n" +
  "请先安装 Python 3.11 或更高版本，并在 OIRO 项目根目录创建虚拟环境：\n" +
  "  python -m venv .venv\n" +
  "  .venv\\Scripts\\pip install -r backend\\requirements.txt\n\n" +
  "若已创建 .venv，请将其放在与 OIRO.exe 同级的项目目录中（向上查找 .venv）。";

const PYTHON_MISSING_PACKAGED_ZH =
  "正式安装包未能启动内置后端。\n\n" +
  "无需在本机单独安装 Python 或 conda。请关闭所有 OIRO 窗口后，从开始菜单重新打开；" +
  "若仍失败，请卸载并安装最新版 OIRO 安装包（应包含 resources\\python\\python.exe）。";

module.exports = {
  resolvePythonLaunch,
  resolvePythonForBackend,
  buildBackendSpec,
  uvicornModuleArgs,
  UVICORN_MODULE_ARGS,
  PYTHON_MISSING_MESSAGE_ZH,
  PYTHON_MISSING_PACKAGED_ZH,
  probeCommand,
};