const { execFileSync } = require("child_process");

const UVICORN_MODULE_ARGS = (port) => [
  "-m",
  "uvicorn",
  "app.main:app",
  "--host",
  "127.0.0.1",
  "--port",
  String(port),
];

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

function buildBackendSpec({ pythonLaunch, apiPort }) {
  if (!pythonLaunch) {
    return null;
  }
  return {
    cmd: pythonLaunch.cmd,
    args: [...pythonLaunch.argsPrefix, ...UVICORN_MODULE_ARGS(apiPort)],
  };
}

const PYTHON_MISSING_MESSAGE_ZH =
  "未找到可用的 Python 环境。\n\n" +
  "请先安装 Python 3.11 或更高版本，并在 OIRO 项目根目录创建虚拟环境：\n" +
  "  python -m venv .venv\n" +
  "  .venv\\Scripts\\pip install -r backend\\requirements.txt\n\n" +
  "若已创建 .venv，请将其放在与 OIRO.exe 同级的项目目录中（向上查找 .venv）。";

module.exports = {
  resolvePythonLaunch,
  buildBackendSpec,
  UVICORN_MODULE_ARGS,
  PYTHON_MISSING_MESSAGE_ZH,
  probeCommand,
};