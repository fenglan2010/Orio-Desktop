import { createRouter, createWebHashHistory } from "vue-router";
import { getToken, getScope, setToken, api } from "../api/client";
import { shellState, clearShellMe } from "../utils/shellState";
import { can, firstAllowedPath, routeCapability } from "../utils/permissions";
import Login from "../views/Login.vue";
import AppLayout from "../components/AppLayout.vue";
import Dashboard from "../views/Dashboard.vue";
import Analysis from "../views/Analysis.vue";
import Execution from "../views/Execution.vue";
import Autonomy from "../views/Autonomy.vue";
import Evolution from "../views/Evolution.vue";
import Audit from "../views/Audit.vue";
import Settings from "../views/Settings.vue";
import OpsLogin from "../views/OpsLogin.vue";
import OpsConsole from "../views/OpsConsole.vue";
import InstallActivate from "../views/InstallActivate.vue";

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/install", component: InstallActivate, meta: { public: true } },
    { path: "/login", component: Login, meta: { public: true } },
    { path: "/ops/login", component: OpsLogin, meta: { public: true, ops: true } },
    { path: "/ops", component: OpsConsole, meta: { ops: true, title: "运维端" } },
    { path: "/alerts", redirect: { path: "/dashboard", query: { focus: "alerts" } } },
    { path: "/strategies", redirect: "/analysis" },
    { path: "/reviews", redirect: { path: "/execution", query: { tab: "reviews" } } },
    { path: "/chat", redirect: { path: "/dashboard", query: { chat: "1" } } },
    {
      path: "/",
      component: AppLayout,
      children: [
        { path: "", redirect: "/dashboard" },
        { path: "dashboard", component: Dashboard, meta: { title: "实时监控大屏", capability: "view_l1" } },
        { path: "l1", redirect: "/dashboard" },
        { path: "analysis", component: Analysis, meta: { title: "多维分析", capability: "view_l2" } },
        { path: "l2", redirect: "/analysis" },
        { path: "execution", component: Execution, meta: { title: "执行中心", capability: "view_l3" } },
        { path: "l3", redirect: "/execution" },
        { path: "autonomy", component: Autonomy, meta: { title: "自治管理", capability: "view_l4" } },
        { path: "l4", redirect: "/autonomy" },
        { path: "evolution", component: Evolution, meta: { title: "本店经验沉淀", capability: "view_l5" } },
        { path: "l5", redirect: "/evolution" },
        { path: "settings", component: Settings, meta: { title: "系统设置" } },
        { path: "audit", component: Audit, meta: { title: "日志查看", capability: "view_audit" } },
      ],
    },
  ],
});

async function ensureMe() {
  if (shellState.me && shellState.me.permissions && Object.keys(shellState.me.permissions).length) {
    return shellState.me;
  }
  const data = await api.me();
  Object.assign(shellState.me, data);
  return data;
}

router.beforeEach(async (to) => {
  if (to.path !== "/install") {
    try {
      const st = await api.installStatus();
      if (st.blocked) return "/install";
    } catch {
      /* offline dev */
    }
  }
  if (to.meta.ops) {
    if (to.path === "/ops/login") {
      if (getToken() && getScope() === "ops") return "/ops";
      return true;
    }
    if (!getToken() || getScope() !== "ops") return "/ops/login";
    return true;
  }
  if (to.meta.public) {
    if (to.path === "/login" && getToken()) {
      if (getScope() === "ops") return "/ops";
      return "/dashboard";
    }
    return true;
  }
  if (!getToken()) return "/login";
  if (getScope() === "ops") return "/ops";
  try {
    const me = await ensureMe();
    const cap = to.meta.capability || routeCapability(to.path);
    if (cap && !can(cap, me)) {
      return firstAllowedPath(me);
    }
    return true;
  } catch {
    setToken("");
    clearShellMe();
    return "/login";
  }
});

export default router;
