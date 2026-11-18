#!/usr/bin/env node
/**
 * Fail fast before electron-builder packs a blank UI.
 */
const fs = require("fs");
const path = require("path");

const distDir = path.join(__dirname, "..", "..", "frontend", "dist");
const indexHtml = path.join(distDir, "index.html");

if (!fs.existsSync(indexHtml)) {
  console.error(
    [
      "ERROR: frontend/dist/index.html is missing.",
      "Build the Vue app before packing:",
      "  cd frontend",
      "  npm ci",
      "  npm run build",
      "",
      "Then from desktop: npm run pack:dir",
    ].join("\n")
  );
  process.exit(1);
}

console.log(`OK: found ${indexHtml}`);