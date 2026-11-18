#!/usr/bin/env node
/**
 * Desktop dev: expects backend on :8765 and Vite on :5173.
 * Set OIRO_DEV_URL=http://127.0.0.1:5173 and OIRO_DISTRIBUTION=packaged to test install gate.
 */
const { spawn } = require("child_process");
const path = require("path");

process.env.OIRO_DEV_URL = process.env.OIRO_DEV_URL || "http://127.0.0.1:5173";
process.env.OIRO_DISTRIBUTION = process.env.OIRO_DISTRIBUTION || "source";

const electron = spawn(
  require("electron"),
  ["."],
  { cwd: path.join(__dirname, ".."), stdio: "inherit", env: process.env }
);

electron.on("exit", (code) => process.exit(code || 0));