const APPEARANCE_KEY = "oiro_appearance";
const SIDEBAR_KEY = "oiro_sidebar_collapsed";
const NOTIFY_KEY = "oiro_notify";
const PROTECT_KEY = "oiro_autoprotect";

export const defaultAppearance = {
  theme: "light",
  density: "standard",
  language: "zh-CN",
};

export const defaultNotify = {
  rank_drop: true,
  price_inversion: true,
  conversion_drop: true,
  negative_review: true,
  competitor_cut: true,
  daily_digest: false,
  write_failed: true,
  reconcile_mismatch: true,
};

export const defaultProtect = {
  oversell: false,
  price: false,
  inventory_sync: false,
  parity: false,
};

function read(key, fallback) {
  try {
    return { ...fallback, ...JSON.parse(localStorage.getItem(key) || "{}") };
  } catch {
    return { ...fallback };
  }
}

export function loadAppearance() {
  return read(APPEARANCE_KEY, defaultAppearance);
}

export function saveAppearance(p) {
  const next = { ...defaultAppearance, ...p };
  localStorage.setItem(APPEARANCE_KEY, JSON.stringify(next));
  applyAppearance(next);
  return next;
}

export function applyAppearance(p = loadAppearance()) {
  const root = document.documentElement;
  let theme = p.theme || "light";
  if (theme === "auto") {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  root.dataset.theme = theme;
  root.dataset.density = p.density || "standard";
  root.lang = p.language || "zh-CN";
}

export function loadSidebarCollapsed() {
  return localStorage.getItem(SIDEBAR_KEY) === "1";
}

export function saveSidebarCollapsed(v) {
  localStorage.setItem(SIDEBAR_KEY, v ? "1" : "0");
}

export function loadNotify() {
  return read(NOTIFY_KEY, defaultNotify);
}

export function saveNotify(p) {
  const next = { ...defaultNotify, ...p };
  localStorage.setItem(NOTIFY_KEY, JSON.stringify(next));
  window.dispatchEvent(new CustomEvent("oiro-notify-changed", { detail: next }));
  return next;
}

export function loadProtect() {
  return read(PROTECT_KEY, defaultProtect);
}

export function saveProtect(p) {
  const next = { ...defaultProtect, ...p };
  localStorage.setItem(PROTECT_KEY, JSON.stringify(next));
  return next;
}
