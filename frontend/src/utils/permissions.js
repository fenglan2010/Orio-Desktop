import { shellState } from "./shellState";

export function permissionsOf(me = shellState.me) {
  return (me && me.permissions) || {};
}

export function can(capability, me = shellState.me) {
  return !!permissionsOf(me)[capability];
}

export function firstAllowedPath(me = shellState.me) {
  const pages = (me && me.pages) || [];
  const map = { L1: "/dashboard", L2: "/analysis", L3: "/execution", L4: "/autonomy", L5: "/evolution" };
  for (const page of pages) {
    if (map[page]) return map[page];
  }
  return "/settings";
}

export function routeCapability(path) {
  if (path.startsWith("/analysis") || path === "/l2") return "view_l2";
  if (path.startsWith("/execution") || path === "/l3") return "view_l3";
  if (path.startsWith("/autonomy") || path === "/l4") return "view_l4";
  if (path.startsWith("/evolution") || path === "/l5") return "view_l5";
  if (path.startsWith("/audit")) return "view_audit";
  if (path.startsWith("/dashboard") || path === "/l1" || path === "/") return "view_l1";
  return "";
}
