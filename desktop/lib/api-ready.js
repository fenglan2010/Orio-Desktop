const http = require("http");

/**
 * Poll local API until health responds 200 or timeout.
 */
function waitForApiHealth({ host = "127.0.0.1", port, timeoutMs = 120000, intervalMs = 400 }) {
  const portNum = Number(port);
  return new Promise((resolve) => {
    const deadline = Date.now() + timeoutMs;
    let lastError = "";

    const attempt = () => {
      if (Date.now() > deadline) {
        resolve({
          ok: false,
          message:
            lastError ||
            `后端在 ${Math.round(timeoutMs / 1000)} 秒内未就绪。请关闭 OIRO 后重试，或重新安装最新安装包。`,
        });
        return;
      }

      const req = http.get(
        {
          hostname: host,
          port: portNum,
          path: "/api/health",
          timeout: 5000,
        },
        (res) => {
          res.resume();
          if (res.statusCode === 200) {
            resolve({ ok: true });
            return;
          }
          lastError = `健康检查返回 HTTP ${res.statusCode}`;
          setTimeout(attempt, intervalMs);
        }
      );
      req.on("timeout", () => {
        req.destroy();
        lastError = "连接后端超时";
        setTimeout(attempt, intervalMs);
      });
      req.on("error", (err) => {
        lastError = err.code === "ECONNREFUSED" ? "后端尚未监听端口" : String(err.message || err);
        setTimeout(attempt, intervalMs);
      });
    };

    attempt();
  });
}

/**
 * After health OK, confirm root URL serves HTML (activation or main shell).
 */
function waitForRootHtml({ host = "127.0.0.1", port, timeoutMs = 30000, intervalMs = 400 }) {
  const portNum = Number(port);
  return new Promise((resolve) => {
    const deadline = Date.now() + timeoutMs;

    const attempt = () => {
      if (Date.now() > deadline) {
        resolve({ ok: false, message: "前端页面未返回 HTML，请重新安装或联系支持。" });
        return;
      }

      const req = http.get(
        { hostname: host, port: portNum, path: "/", timeout: 5000 },
        (res) => {
          let body = "";
          res.on("data", (chunk) => {
            body += chunk;
            if (body.length > 65536) {
              res.destroy();
            }
          });
          res.on("end", () => {
            if (res.statusCode === 200 && /<html/i.test(body)) {
              resolve({ ok: true });
              return;
            }
            setTimeout(attempt, intervalMs);
          });
        }
      );
      req.on("error", () => setTimeout(attempt, intervalMs));
      req.on("timeout", () => {
        req.destroy();
        setTimeout(attempt, intervalMs);
      });
    };

    attempt();
  });
}

module.exports = { waitForApiHealth, waitForRootHtml };