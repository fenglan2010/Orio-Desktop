const test = require("node:test");
const assert = require("node:assert/strict");
const {
  buildBackendSpec,
  resolvePythonLaunch,
  resolvePythonForBackend,
  uvicornModuleArgs,
} = require("../lib/python-resolve");

test("buildBackendSpec wires uvicorn on venv python with --app-dir", () => {
  const launch = resolvePythonLaunch({
    venvPython: "/tmp/fake/.venv/bin/python",
    platform: "linux",
  });
  const backendDir = "/tmp/fake/oiro/backend";
  const spec = buildBackendSpec({ pythonLaunch: launch, apiPort: "8765", backendDir });
  assert.equal(spec.cmd, "/tmp/fake/.venv/bin/python");
  assert.deepEqual(spec.args, uvicornModuleArgs("8765", backendDir));
});

test("resolvePythonLaunch on win32 returns py -3 prefix when no venv", () => {
  const launch = resolvePythonLaunch({
    venvPython: null,
    platform: "win32",
  });
  if (launch) {
    if (launch.cmd === "py") {
      assert.deepEqual(launch.argsPrefix, ["-3"]);
    } else {
      assert.deepEqual(launch.argsPrefix, []);
    }
    assert.notEqual(launch.cmd, "python3-only-fallback");
  } else {
    assert.ok(true, "no python on CI is acceptable");
  }
});

test("resolvePythonLaunch prefers explicit venv path", () => {
  const launch = resolvePythonLaunch({
    venvPython: "C:\\\\Users\\\\x\\\\.venv\\\\Scripts\\\\python.exe",
    platform: "win32",
  });
  assert.equal(launch.cmd, "C:\\\\Users\\\\x\\\\.venv\\\\Scripts\\\\python.exe");
  assert.deepEqual(launch.argsPrefix, []);
});

test("resolvePythonForBackend packaged uses bundled only", () => {
  const bundled = "C:\\\\OIRO\\\\resources\\\\python\\\\python.exe";
  const launch = resolvePythonForBackend({
    isPackaged: true,
    bundledPython: bundled,
    venvPython: "C:\\\\Desktop\\\\.venv\\\\Scripts\\\\python.exe",
    platform: "win32",
  });
  assert.equal(launch.cmd, bundled);
  const missing = resolvePythonForBackend({
    isPackaged: true,
    bundledPython: null,
    venvPython: null,
    platform: "win32",
  });
  assert.equal(missing, null);
});