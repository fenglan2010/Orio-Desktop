const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const fs = require("fs");
const os = require("os");
const {
  findVenvPythonFrom,
  resolveBackendDir,
  resolveStaticRoot,
  defaultDistribution,
} = require("../lib/paths");

test("defaultDistribution uses source in dev and packaged when built", () => {
  assert.equal(defaultDistribution(false), "source");
  assert.equal(defaultDistribution(true), "packaged");
});

test("findVenvPythonFrom walks upward", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "oiro-venv-"));
  const nested = path.join(root, "a", "b", "c");
  fs.mkdirSync(nested, { recursive: true });
  const venvDir = path.join(root, ".venv", "bin");
  fs.mkdirSync(venvDir, { recursive: true });
  const pythonPath = path.join(venvDir, "python");
  fs.writeFileSync(pythonPath, "#!/bin/sh\n");

  const found = findVenvPythonFrom(nested);
  assert.equal(found, pythonPath);

  fs.rmSync(root, { recursive: true, force: true });
});

test("resolveBackendDir prefers bundled backend when packaged", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "oiro-backend-"));
  const resources = path.join(root, "resources");
  const backend = path.join(resources, "backend");
  fs.mkdirSync(backend, { recursive: true });
  fs.writeFileSync(path.join(backend, "marker"), "1");

  const resolved = resolveBackendDir({
    isPackaged: true,
    resourcesPath: resources,
    appDir: path.join(root, "app.asar"),
  });
  assert.equal(resolved, backend);

  fs.rmSync(root, { recursive: true, force: true });
});

test("resolveStaticRoot uses resources/renderer when packaged", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "oiro-static-"));
  const resources = path.join(root, "resources");
  const renderer = path.join(resources, "renderer");
  fs.mkdirSync(renderer, { recursive: true });
  fs.writeFileSync(path.join(renderer, "index.html"), "<html></html>");

  const resolved = resolveStaticRoot({
    isPackaged: true,
    resourcesPath: resources,
    appDir: path.join(root, "app.asar"),
  });
  assert.equal(resolved, renderer);

  fs.rmSync(root, { recursive: true, force: true });
});

test("resolveStaticRoot does not use asar renderer path when only asar has files", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "oiro-asar-"));
  const asarRenderer = path.join(root, "app.asar", "renderer");
  fs.mkdirSync(asarRenderer, { recursive: true });
  fs.writeFileSync(path.join(asarRenderer, "index.html"), "<html></html>");

  const resolved = resolveStaticRoot({
    isPackaged: true,
    resourcesPath: path.join(root, "resources-empty"),
    appDir: path.join(root, "app.asar"),
  });
  assert.equal(resolved, null);

  fs.rmSync(root, { recursive: true, force: true });
});

test("resolveBackendDir uses repo sibling backend in dev", () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "oiro-repo-"));
  const backend = path.join(repo, "backend");
  const desktop = path.join(repo, "desktop");
  fs.mkdirSync(backend, { recursive: true });
  fs.mkdirSync(desktop, { recursive: true });

  const resolved = resolveBackendDir({
    isPackaged: false,
    resourcesPath: "",
    appDir: desktop,
  });
  assert.equal(resolved, backend);

  fs.rmSync(repo, { recursive: true, force: true });
});