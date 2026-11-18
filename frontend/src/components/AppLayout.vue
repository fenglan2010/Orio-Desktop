<template>
  <div class="shell" :class="{ 'is-collapsed': collapsed }">
    <aside class="sidebar shell-side">
      <div class="brand">
        <div class="brand-logo"><Icon name="logo" :size="18" /></div>
        <div class="brand-text">
          <div class="brand-name">OIRO</div>
          <div class="brand-sub">智能收益优化官</div>
        </div>
      </div>
      <button type="button" class="collapse-btn" :title="collapsed ? '展开导航' : '收起导航'" @click="toggleSide">
        <Icon :name="collapsed ? 'chevron-right' : 'chevron-left'" :size="14" />
      </button>
      <div class="sidebar-scroll">
        <template v-for="sec in visibleSections" :key="sec.level">
          <div class="nav-caption">
            <div class="nav-level"><b>{{ sec.level }}</b>{{ sec.role }}</div>
          </div>
          <router-link
            :to="sec.to"
            class="nav-link"
            :class="[isActive(sec.match) ? sec.activeClass : '']"
          >
            <Icon :name="sec.icon" class="ico" :size="18" />
            <span>{{ sec.label }}</span>
          </router-link>
        </template>
        <router-link to="/settings" class="nav-link" :class="{ 'active-settings': isActive(['/settings']) }">
          <Icon name="settings" class="ico" :size="18" />
          <span>系统设置</span>
        </router-link>
      </div>
      <div class="sidebar-links">
        <button type="button" @click="openHelp = true"><Icon name="help" class="ico" :size="16" /><span>帮助文档</span></button>
        <router-link v-if="can('view_audit')" to="/audit"><Icon name="log" class="ico" :size="16" /><span>日志查看</span></router-link>
        <button type="button" @click="openFeedback = true"><Icon name="feedback" class="ico" :size="16" /><span>反馈建议</span></button>
      </div>
    </aside>

    <div class="shell-body">
      <header class="topbar">
        <div class="search-wrap">
          <Icon name="search" class="ico-search" :size="16" />
          <input
            v-model="q"
            placeholder="搜索指标、策略、日志…"
            @focus="searchOpen = true"
            @input="searchOpen = true"
            @keydown.esc="searchOpen = false"
          />
          <div v-if="searchOpen" class="search-pop" @mousedown.prevent>
            <div v-if="!searchResults.length" class="hint">输入关键词，跳转到页面、预警或策略。</div>
            <button v-for="r in searchResults" :key="r.key" type="button" @click="goSearch(r)">
              <div>{{ r.title }}</div>
              <div class="muted" style="font-size:12px">{{ r.hint }}</div>
            </button>
          </div>
        </div>

        <div class="chip"><Icon name="calendar" :size="14" /> {{ today }}</div>

        <div class="rel">
          <button type="button" class="chip hotel-chip" @click="hotelOpen = !hotelOpen">
            <Icon name="building" :size="14" />
            {{ me.hotel_name || "当前酒店" }}
            <Icon name="chevron-down" class="caret" :size="14" />
          </button>
          <div v-if="hotelOpen" class="chip-menu" @mousedown.prevent>
            <div style="padding:6px 8px 10px">
              <b>{{ me.hotel_name || "当前酒店" }}</b>
              <div class="muted" style="font-size:12px;margin-top:4px">{{ me.city || "" }} · {{ me.platform === "ctrip" ? "携程" : (me.platform || "") }} · 模拟数据</div>
              <div class="muted" style="font-size:12px;margin-top:8px">当前显示为登录账号所属酒店。本轮演示账号各只属于一家酒店，因此无需切换。</div>
            </div>
          </div>
        </div>

        <div class="mini-kpis" v-if="mini.length">
          <div class="mk" v-for="k in mini" :key="k.key">
            <div class="lab">{{ k.label }}</div>
            <div class="val">{{ k.value }}</div>
            <div class="dlt delta" :class="k.cls">{{ k.delta }}</div>
          </div>
        </div>

        <div class="topbar-actions">
        <button v-if="can('view_chat')" type="button" class="icon-btn" title="决策对话" aria-label="决策对话" @click="openChat = true">
          <Icon name="chat" :size="18" />
        </button>

        <div class="rel">
          <button type="button" class="icon-btn" title="预警" aria-label="预警" @click="bellOpen = !bellOpen">
            <Icon name="bell" :size="18" />
            <span v-if="openAlertCount" class="dot"></span>
          </button>
          <div v-if="bellOpen" class="dropdown-panel" @mousedown.prevent>
            <h4>未处理预警（{{ openAlertCount }}）</h4>
            <div v-if="!openAlerts.length" class="empty" style="padding:16px">暂无未处理预警。</div>
            <button
              v-for="a in openAlerts.slice(0, 6)"
              :key="a.id"
              type="button"
              style="display:block;width:100%;text-align:left;background:none;border:0;padding:8px;border-radius:8px"
              @click="goAlert(a)"
            >
              <div style="font-weight:600;font-size:13px">{{ fixDirectionCopy(a.title) }}</div>
              <div class="muted" style="font-size:12px">{{ fixDirectionCopy(a.body) }}</div>
            </button>
            <router-link to="/dashboard?focus=alerts" style="display:block;padding:8px;color:var(--primary);font-weight:550" @click="bellOpen=false">查看全部</router-link>
          </div>
        </div>

        <button type="button" class="icon-btn" title="系统设置" aria-label="系统设置" @click="$router.push('/settings')">
          <Icon name="gear" :size="18" />
        </button>

        <div class="rel">
          <button type="button" class="avatar-btn" aria-label="用户菜单" @click="userOpen = !userOpen">
            <span class="avatar">{{ initials }}</span>
            <span class="avatar-name">{{ me.display_name || "收益经理" }}</span>
            <Icon name="chevron-down" :size="14" />
          </button>
          <div v-if="userOpen" class="chip-menu user-menu" @mousedown.prevent>
            <div class="who">
              <b>{{ me.display_name || "收益经理" }}</b>
              <span>{{ roleLabel(me.role) }}</span>
            </div>
            <router-link to="/settings?section=accounts" @click="userOpen=false">账户设置</router-link>
            <button type="button" @click="userOpen=false; openHelp=true">帮助文档</button>
            <button type="button" class="danger" @click="logout">退出登录</button>
          </div>
        </div>
        </div>
      </header>

      <div class="content">
        <p v-if="me.product_enabled === false" class="notice" style="margin:12px 20px 0">当前酒店尚未开通，待电子合同生效并确认付款后再使用分析与执行功能。设置页仍可查看开通状态。</p>
        <router-view />
      </div>
    </div>

    <footer class="app-footer shell-foot">
      <div class="left">
        <span v-if="healthOk" class="health-ok">系统运行正常</span>
        <span v-else-if="health" class="health-bad">系统状态异常</span>
        <span v-else class="muted">正在检查系统状态…</span>
        <span>{{ clock }}</span>
        <span class="prog">AI 进化进度：<b>80%</b> <span class="phase-note">演示</span></span>
      </div>
      <div class="right">
        <button type="button" class="foot-btn" @click="$router.push('/dashboard')"><Icon name="home" :size="13" /> 返回首页</button>
        <button type="button" class="foot-btn" @click="refreshAll"><Icon name="refresh" :size="13" /> 刷新数据</button>
        <span class="ver">v0.1</span>
      </div>
    </footer>
  </div>

  <ChatDrawer :open="openChat" @close="openChat = false" />

  <div v-if="openHelp" class="modal" @click.self="openHelp=false">
    <div class="panel help-body">
      <div class="row" style="justify-content:space-between">
        <h2 style="margin:0">帮助文档</h2>
        <button class="icon-btn" type="button" aria-label="关闭" title="关闭" @click="openHelp=false"><Icon name="close" /></button>
      </div>
      <p class="muted">这是应用内说明，不是外部文档站点。</p>
      <h3>本轮能做什么</h3>
      <p>看见经营指标与预警 → 生成可解释的定价/活动建议 → 人审通过后写入模拟执行记录 → 必要时按检查点回滚。数据来自携程模拟适配器，不会连接真实 OTA。</p>
      <h3>层级说明</h3>
      <p><b>L1 数据观察员</b>：看清近 7 日营收、出租率、ADR、转化、搜索排名和竞对价格带，并对预警做诊断。<br>
      <b>L2 策略分析师</b>：对照多维分析，生成并查看策略建议。<br>
      <b>L3 执行型助手</b>：把待人审策略放入执行队列，确认或拒绝；评价回复也在这里完成人审。<br>
      <b>L4 收益运营官</b>：本阶段仅展示自治管理界面，<b>不会自动调价</b>。决策日志接审计记录。<br>
      <b>L5 本店经验沉淀</b>：展示本店已执行或已否决的决策，并可引用匿名行业模式。跨店学习已开始，但是匿名模式，不展示其他酒店名称，不会训练模型，也不会自动改价。</p>
      <h3>演示账号</h3>
      <p>杭州西湖精品酒店（演示）：admin / admin123（酒店管理员）、manager / manager123（收益经理）、approver / approver123（调价审批人）、analyst / analyst123（运营观察）。成都春熙商务酒店（演示）：admin_b / admin123（仅该店管理员）。两个酒店的数据互相隔离。</p>
    </div>
  </div>

  <div v-if="openFeedback" class="modal" @click.self="openFeedback=false">
    <div class="panel">
      <div class="row" style="justify-content:space-between">
        <h2 style="margin:0">反馈建议</h2>
        <button class="icon-btn" type="button" aria-label="关闭" title="关闭" @click="openFeedback=false"><Icon name="close" /></button>
      </div>
      <p class="muted">本轮未接通外部工单系统。你可以把意见记在下方，仅保存在本机，便于演示时对照。</p>
      <div class="field">
        <label>建议内容</label>
        <textarea v-model="feedback" rows="5" placeholder="例如：希望预警支持按房型筛选…"></textarea>
      </div>
      <div class="row" style="justify-content:flex-end">
        <button class="btn" type="button" @click="openFeedback=false">取消</button>
        <button class="btn btn-primary" type="button" @click="saveFeedback">保存在本机</button>
      </div>
      <p v-if="feedbackSaved" class="notice-ok notice" style="margin-top:12px">已保存在本机浏览器，不会发送到服务器。</p>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, provide, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, setToken } from "../api/client";
import { clsDelta, pct, rankDelta, roleLabel, signed, formatClock, yen, fixDirectionCopy } from "../utils/format";
import { shellState, clearShellMe } from "../utils/shellState";
import { can } from "../utils/permissions";
import { useEscape } from "../composables/useEscape";
import { loadNotify, loadSidebarCollapsed, saveSidebarCollapsed } from "../utils/prefs";
import Icon from "./Icon.vue";
import ChatDrawer from "./ChatDrawer.vue";

const sections = [
  { level: "L1", role: "数据观察员", label: "实时监控大屏", to: "/dashboard", match: ["/dashboard", "/l1"], icon: "monitor", activeClass: "active-l1", cap: "view_l1" },
  { level: "L2", role: "策略分析师", label: "多维分析", to: "/analysis", match: ["/analysis", "/l2"], icon: "trend", activeClass: "active-l2", cap: "view_l2" },
  { level: "L3", role: "执行型助手", label: "执行中心", to: "/execution", match: ["/execution", "/l3"], icon: "play", activeClass: "active-l3", cap: "view_l3" },
  { level: "L4", role: "收益运营官", label: "自治管理", to: "/autonomy", match: ["/autonomy", "/l4"], icon: "target", activeClass: "active-l4", cap: "view_l4" },
  { level: "L5", role: "自主收益系统", label: "经验沉淀", to: "/evolution", match: ["/evolution", "/l5"], icon: "spark", activeClass: "active-l5", cap: "view_l5" },
];
const visibleSections = computed(() => sections.filter((s) => can(s.cap, me)));

const me = reactive({ ...shellState.me });
const summary = ref(shellState.summary);
const alerts = ref(shellState.alerts);
const strategies = ref(shellState.strategies);
const health = ref(shellState.health);
const refreshTick = ref(0);
const route = useRoute();
const router = useRouter();
const collapsed = ref(loadSidebarCollapsed());
const q = ref("");
const searchOpen = ref(false);
const hotelOpen = ref(false);
const bellOpen = ref(false);
const userOpen = ref(false);
const openChat = ref(false);
const openHelp = ref(false);
const openFeedback = ref(false);
const feedback = ref(localStorage.getItem("oiro_feedback") || "");
const feedbackSaved = ref(false);
const clock = ref(formatClock());
let timer = null;

const today = new Date().toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).replace(/\//g, "-");
const healthOk = computed(() => health.value?.status === "ok");
const notify = ref(loadNotify());
function onNotifyPref(e) {
  notify.value = e?.detail || loadNotify();
}
const openAlerts = computed(() =>
  (alerts.value || []).filter((a) => a.status === "open" && notify.value[a.alert_type] !== false)
);

const openAlertCount = computed(() => openAlerts.value.length);
const initials = computed(() => {
  const n = me.display_name || me.username || "经";
  return n.slice(0, 1);
});

const mini = computed(() => {
  const k = summary.value?.kpis;
  if (!k) return [];
  const d = k.delta || {};
  return [
    { key: "rev", label: "本周营收", value: yen(k.week_revenue), delta: signed(d.week_revenue, "%"), cls: clsDelta(d.week_revenue) },
    { key: "revpar", label: "RevPAR", value: yen(k.revpar, 1), delta: signed(d.revpar, "%"), cls: clsDelta(d.revpar) },
    { key: "occ", label: "OCC", value: pct(k.today_occupancy), delta: signed(d.today_occupancy, "pp"), cls: clsDelta(d.today_occupancy) },
    { key: "today_rev", label: "今日营收", value: yen(k.today_revenue), delta: signed(d.today_revenue, "%"), cls: clsDelta(d.today_revenue) },
  ];
});

const searchIndex = computed(() => {
  const rows = [
    { key: "p-l1", title: "L1 实时监控大屏", hint: "经营指标与预警", to: "/dashboard", cap: "view_l1" },
    { key: "p-al", title: "异常告警中心", hint: "预警与诊断", to: "/dashboard?focus=alerts", cap: "view_l1" },
    { key: "p-l2", title: "L2 多维分析 / 策略建议", hint: "生成策略并查看理由", to: "/analysis", cap: "view_l2" },
    { key: "p-l3", title: "L3 执行中心", hint: "人审队列、评价回复", to: "/execution", cap: "view_l3" },
    { key: "p-rev", title: "评价与回复", hint: "草稿、人审、回滚", to: "/execution?tab=reviews", cap: "view_l3" },
    { key: "p-l4", title: "L4 自治管理", hint: "决策日志与执行策略", to: "/autonomy", cap: "view_l4" },
    { key: "p-l5", title: "L5 本店经验沉淀", hint: "匿名跨店学习", to: "/evolution", cap: "view_l5" },
    { key: "p-set", title: "系统设置", hint: "账号、酒店、外观", to: "/settings" },
    { key: "p-aud", title: "日志查看", hint: "操作审计与模型调用", to: "/audit", cap: "view_audit" },
    { key: "p-chat", title: "决策对话", hint: "自然语言追问", action: "chat", cap: "view_chat" },
  ].filter((row) => !row.cap || can(row.cap, me));
  for (const a of alerts.value || []) {
    rows.push({ key: "a-" + a.id, title: fixDirectionCopy(a.title), hint: "预警", to: "/dashboard?focus=alerts&id=" + a.id });
  }
  for (const s of strategies.value || []) {
    const title = s.kind === "campaign" ? "活动参与建议" : (s.room_name || "房型") + " 定价建议";
    rows.push({ key: "s-" + s.id, title, hint: "策略 · " + (s.status || ""), to: "/execution?tab=pricing&sid=" + s.id });
  }
  return rows;
});

const searchResults = computed(() => {
  const s = q.value.trim();
  if (!s) return searchIndex.value.slice(0, 8);
  return searchIndex.value.filter((r) => (r.title + r.hint).includes(s)).slice(0, 12);
});

function isActive(match) {
  return match.some((p) => route.path === p || route.path.startsWith(p + "/"));
}
function toggleSide() {
  collapsed.value = !collapsed.value;
  saveSidebarCollapsed(collapsed.value);
}
function logout() {
  setToken("");
  clearShellMe();
  router.push("/login");
}
function goSearch(r) {
  searchOpen.value = false;
  q.value = "";
  if (r.action === "chat") openChat.value = true;
  else if (r.to) router.push(r.to);
}
function goAlert() {
  bellOpen.value = false;
  router.push("/dashboard?focus=alerts");
}
function saveFeedback() {
  localStorage.setItem("oiro_feedback", feedback.value);
  feedbackSaved.value = true;
}
async function refreshAll() {
  try {
    const [s, a, h, st] = await Promise.all([
      api.summary(),
      api.alerts(),
      api.health().catch(() => null),
      api.strategies().catch(() => ({ strategies: [] })),
    ]);
    summary.value = s;
    alerts.value = a.alerts || [];
    if (h) health.value = h;
    strategies.value = st.strategies || [];
    if (s?.hotel) {
      me.hotel_name = s.hotel.name || me.hotel_name;
      me.city = s.hotel.city || me.city;
    }
    shellState.summary = summary.value;
    shellState.alerts = alerts.value;
    shellState.health = health.value;
    shellState.strategies = strategies.value;
    Object.assign(shellState.me, me);
    refreshTick.value += 1;
  } catch {
    /* keep previous */
  }
}

provide("appShell", {
  me,
  summary,
  alerts,
  health,
  refreshTick,
  refreshAll,
  openChat: () => { openChat.value = true; },
  openHelp: () => { openHelp.value = true; },
});

function onDoc(e) {
  if (!e.target.closest(".rel, .search-wrap")) {
    searchOpen.value = false;
    hotelOpen.value = false;
    bellOpen.value = false;
    userOpen.value = false;
  }
}

watch(() => route.query.chat, (v) => { if (v) openChat.value = true; });
useEscape(openHelp, () => { openHelp.value = false; });
useEscape(openFeedback, () => { openFeedback.value = false; });
useEscape(openChat, () => { openChat.value = false; });

onMounted(async () => {
  window.addEventListener("oiro-notify-changed", onNotifyPref);
  document.addEventListener("click", onDoc);
  timer = setInterval(() => { clock.value = formatClock(); }, 1000);
  try {
    Object.assign(me, await api.me());
    Object.assign(shellState.me, me);
  } catch {
    logout();
    return;
  }
  await refreshAll();
  if (route.query.chat) openChat.value = true;
});
onUnmounted(() => {
  window.removeEventListener("oiro-notify-changed", onNotifyPref);
  document.removeEventListener("click", onDoc);
  if (timer) clearInterval(timer);
});
</script>
