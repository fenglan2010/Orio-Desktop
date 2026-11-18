export function money(n, digits = 0) {
  return Number(n || 0).toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function yen(n, digits = 0) {
  return "¥" + money(n, digits);
}

export function pct(n, digits = 1) {
  return (Number(n || 0) * 100).toFixed(digits) + "%";
}

export function signed(n, suffix = "", digits = 1) {
  const v = Number(n || 0);
  const body = Number.isInteger(Number(digits)) ? v.toFixed(digits) : String(v);
  return (v > 0 ? "+" : "") + body + suffix;
}

export function clsDelta(n, invert = false) {
  const v = Number(n || 0);
  if (v === 0) return "flat";
  const up = invert ? v < 0 : v > 0;
  return up ? "up" : "down";
}

export function rankDelta(n) {
  const v = Number(n || 0);
  if (v > 0) return `后移 ${v} 位`;
  if (v < 0) return `前移 ${Math.abs(v)} 位`;
  return "持平";
}


export function changeVerb(from, to, { invert = false, up = "升至", down = "降至", flat = "持平" } = {}) {
  const a = Number(from);
  const b = Number(to);
  if (!Number.isFinite(a) || !Number.isFinite(b) || a === b) return flat;
  const rose = b > a;
  return (invert ? !rose : rose) ? up : down;
}

/** Fix "由 A 降至 B" / rank "从第A降至第B" when the verb disagrees with the numbers. */
export function fixDirectionCopy(text) {
  if (text == null) return "";
  let s = String(text);
  s = s.replace(
    /从第\s*([+-]?\d+(?:\.\d+)?)\s*(?:降至|升至|下滑至|上升至|后移至|前移至)\s*第\s*([+-]?\d+(?:\.\d+)?)/g,
    (_, a, b) => {
      const verb = changeVerb(a, b, { invert: true, up: "前移至", down: "后移至", flat: "持平于" });
      return `从第${a}${verb}第${b}`;
    }
  );
  s = s.replace(
    /由\s*([+-]?\d+(?:\.\d+)?)\s*(%?)\s*(?:降至|升至|下降至|上升至)\s*([+-]?\d+(?:\.\d+)?)\s*(%?)/g,
    (_, a, u1, b, u2) => {
      const u = u1 || u2 || "";
      const verb = changeVerb(a, b, { up: "升至", down: "降至", flat: "持平于" });
      if (verb === "持平于") return `由 ${a}${u} 持平`;
      return `由 ${a}${u} ${verb} ${b}${u}`;
    }
  );
  return s;
}


export function formatDate(ts, opts = {}) {
  if (!ts) return "—";
  const raw = String(ts);
  const d = raw.includes("T")
    ? new Date(raw.endsWith("Z") || /[+-]\d\d:\d\d$/.test(raw) ? raw : `${raw}Z`)
    : new Date(raw + (raw.length <= 10 ? "T00:00:00" : ""));
  if (Number.isNaN(d.getTime())) return raw.replace("T", " ").slice(0, 16);
  return d.toLocaleString("zh-CN", {
    hour12: false,
    year: opts.year ? "numeric" : undefined,
    month: "2-digit",
    day: "2-digit",
    hour: opts.time === false ? undefined : "2-digit",
    minute: opts.time === false ? undefined : "2-digit",
    weekday: opts.weekday || undefined,
    ...opts.extra,
  });
}

export function formatClock(d = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function todayISO() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function addDays(iso, days) {
  const d = new Date(iso + "T00:00:00");
  d.setDate(d.getDate() + days);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function shortId(id) {
  return id ? String(id).slice(0, 8) : "";
}

export function roleLabel(role) {
  return (
    {
      revenue_manager: "收益经理",
      admin: "酒店管理员",
      approver: "调价审批人",
      analyst: "运营观察",
      owner: "收益总监",
    }[role] || role || "—"
  );
}

export function actionLabel(a) {
  return (
    {
      review_approve: "人审通过",
      review_reject: "人审拒绝",
      rollback: "回滚",
      execute: "沙盒执行",
      auto_execute: "自动执行",
      execution_refused: "写入被拒绝",
      diagnose_alert: "诊断预警",
      ack_alert: "确认预警",
      update_settings: "更新设置",
      update_execution_policy: "更新执行策略",
      update_price_constraints: "更新价格约束",
      user_create: "创建账号",
      user_update: "更新账号",
      user_disable: "停用账号",
      user_enable: "启用账号",
      contract_install: "安装官方契约",
      contract_uninstall: "卸载官方契约",
      generate_strategy: "生成策略",
      write_attempt: "写入尝试",
      write_retry: "写入重试",
      write_reconcile: "写入对账",
      write_partial: "部分写入",
      write_auto_rollback: "写入失败自动回滚",
      execution_refused: "写入被拒绝",
    }[a] || a || "—"
  );
}

export function downloadText(filename, text, mime = "text/plain;charset=utf-8") {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function toCsv(rows) {
  return rows
    .map((row) =>
      row
        .map((cell) => {
          const s = cell == null ? "" : String(cell);
          return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
        })
        .join(",")
    )
    .join("\n");
}
