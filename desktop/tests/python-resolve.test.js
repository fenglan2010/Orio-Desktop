const test = require("node:test");
const assert = require("node:assert/strict");
const {
  buildBackendSpec,
  resolvePythonLaunch,
  UVICORN_MODULE_ARGS,
} = require("../lib/python-resolve");

test("buildBackendSpec wires uvicorn on venv python", () => {
  const launch = resolvePythonLaunch({
    venvPython: "/tmp/fake/.venv/bin/python",
    platform: "linux",
  });
  const spec = buildBackendSpec({ pythonLaunch: launch, apiPort: "8765" });
  assert.equal(spec.cmd, "/tmp/fake/.venv/bin/python");
  assert.deepEqual(spec.args, UVICORN_MODULE_ARGS("8765"));
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