#!/usr/bin/env node
/**
 * Ensure frontend/dist exists before electron-builder runs.
 * With --build: run npm ci + npm run build in ../frontend when index.html is missing.
 */
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const distDir = path.join(__dirname, "..", "..", "frontend", "dist");
const indexHtml = path.join(distDir, "index.html");
const frontendDir = path.join(__dirname, "..", "..", "frontend");
const shouldBuild = process.argv.includes("--build");

function fail(message) {
  console.error(message);
  process.exit(1);
}

function buildFrontend() {
  console.log("Building frontend (npm ci && npm run build)...");

  const shell = process.platform === "win32";
  const npmCi = spawnSync("npm", ["ci"], {
    cwd: frontendDir,
    stdio: "inherit",
    shell,
  });
  if (npmCi.status !== 0) {
    fail("ERROR: frontend npm ci failed.");
  }

  const npmBuild = spawnSync("npm", ["run", "build"], {
    cwd: frontendDir,
    stdio: "inherit",
    shell,
  });
  if (npmBuild.status !== 0) {
    fail("ERROR: frontend npm run build failed.");
  }
}

if (!fs.existsSync(indexHtml)) {
  if (shouldBuild) {
    buildFrontend();
  } else {
    fail(
      [
        "ERROR: frontend/dist/index.html is missing.",
        "",
        "Build the Vue app before packing, for example:",
        "  cd frontend",
        "  npm ci",
        "  npm run build",
        "",
        "Or from desktop:",
        "  npm run pack:dir   (runs ensure-frontend-dist --build automatically)",
        "",
        "On Windows you can also run: .\\desktop\\scripts\\build-windows.ps1",
      ].join("\n")
    );
  }
}

if (!fs.existsSync(indexHtml)) {
  fail(
    [
      "ERROR: frontend/dist/index.html is still missing after build.",
      "Check Node.js version (recommend Node 20 LTS for Electron builds; Node 26+ may break tooling).",
    ].join("\n")
  );
}

console.log(`OK: found ${indexHtml}`);