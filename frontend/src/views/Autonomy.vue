<template>
    <div class="page-head">
      <div>
        <h1>L4 收益运营官 - 自治管理</h1>
        <p>自动执行默认关闭。开启后仅低风险动作可自动走沙盒写入；中高风险和评价回复仍须人审。</p>
      </div>
      <div class="page-actions">
        <span class="chip">低风险自动执行
          <button class="toggle" type="button" :class="{ on: policy.auto_execute_enabled }" :disabled="savingPolicy || !can('update_policy')" :title="can('update_policy') ? (policy.auto_execute_enabled ? '点击关闭自动执行' : '点击开启低风险自动执行') : '仅酒店管理员可修改执行策略'" @click="toggleAuto(!policy.auto_execute_enabled)">
            <i></i>
          </button>
        </span>
        <button class="btn btn-orange" type="button" :disabled="savingPolicy || !policy.auto_execute_enabled || !can('update_policy')" @click="toggleAuto(false)">暂停自动执行</button>
      </div>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="saved" class="notice notice-ok">{{ saved }}</p>

    <div class="notice" style="margin-bottom:16px">
      默认关闭，行为与第一阶段相同。开启后，只有最终风险为低、入住窗口不含法定节假日、调价幅度低于阈值（默认 15%）的方案会自动执行；中高风险、活动报名和评价回复仍进入人审队列。默认写入是官方沙盒回执。酒店只需授权本店 OTA 账号和密码；官方契约和接口由运维端配置。只有渠道已授权、运维端已启用官方契约并打开写入开关时，live 才会出站。自动执行会留下 <code>system:auto_execute</code> 审计、执行前检查点，并可按原路径回滚。
    </div>

    <div class="grid-2">
      <div>
        <div class="card card-pad" style="margin-bottom:16px">
          <div class="section-title">收益目标对照 <span class="muted" style="font-weight:500">按近 7 日实际指标</span></div>
          <div class="subcard" v-if="kpis">
            <div class="row" style="justify-content:space-between"><span>近 7 日营收</span><b>{{ yen(kpis.revenue) }}</b></div>
            <div class="muted">对比前 7 日 {{ signed(kpis.delta?.revenue, "%") }}</div>
            <div class="bar-track green" style="margin-top:10px"><span :style="{ width: barW(kpis.delta?.revenue) }"></span></div>
          </div>
          <div class="grid-2 eq">
            <div class="subcard">
              <div class="muted">ADR</div>
              <b>{{ yen(kpis?.adr) }}</b>
              <div class="delta" :class="clsDelta(kpis?.delta?.adr)">{{ signed(kpis?.delta?.adr, "%") }}</div>
            </div>
            <div class="subcard">
              <div class="muted">出租率</div>
              <b>{{ pct(kpis?.occupancy) }}</b>
              <div class="delta" :class="clsDelta(kpis?.delta?.occupancy)">{{ signed(kpis?.delta?.occupancy, "%") }}</div>
            </div>
          </div>
          <div class="subcard" style="background:var(--warning-bg)">
            <div class="section-title" style="margin-bottom:8px">价格约束（来自房型档案）</div>
            <div v-for="r in rooms" :key="r.id" class="row" style="justify-content:space-between;margin:4px 0">
              <span>{{ r.name }}</span>
              <span>{{ yen(r.min_price) }} – {{ yen(r.max_price) }}</span>
            </div>
          </div>
        </div>
      </div>
      <div class="card card-pad">
        <div class="section-title">近 14 日实际出租率</div>
        <LineChart
          v-if="occLabels.length"
          :series="[{ name: '出租率%', values: occHist, color: '#155dfc', area: true }]"
          :labels="occLabels"
          :area="true"
          :height="200"
        />
        <div class="legend"><span><i style="background:#155dfc"></i>出租率</span></div>
        <div class="statline" v-if="windowOcc.length">
          <div>前一窗口均出租率<b>{{ windowOcc[0] }}%</b></div>
          <div>近 7 日均出租率<b>{{ windowOcc[1] }}%</b></div>
          <div>搜索排名<b>{{ kpis?.search_rank ?? "—" }}</b></div>
        </div>
        <p class="muted">按日展示近 14 日实际出租率，并对照前一窗口与近 7 日均值。搜索排名来自看板汇总，不是自动目标完成图。</p>
      </div>
    </div>

    <div class="space"></div>
    <div class="card card-pad">
      <div class="section-title">
        PDCA 执行循环
        <span class="muted" style="font-weight:500">人审为主，低风险自动执行为可选</span>
      </div>
      <div class="pdca">
        <div class="pdca-card active-plan">
          <div class="row" style="justify-content:space-between"><b>Plan</b><span class="tag success">策略 {{ counts.generated }}</span></div>
          <div class="muted">已生成策略</div>
          <div class="bar-track green"><span :style="{ width: '100%' }"></span></div>
        </div>
        <div class="pdca-card active-do">
          <div class="row" style="justify-content:space-between"><b>Do</b><span class="tag info">待审 {{ counts.pending }}</span></div>
          <div class="muted">进入人审队列</div>
          <div class="bar-track"><span :style="{ width: (counts.generated ? counts.pending / counts.generated * 100 : 0) + '%' }"></span></div>
        </div>
        <div class="pdca-card">
          <div class="row" style="justify-content:space-between"><b>Check</b><span class="tag">已执行 {{ counts.executed }}</span></div>
          <div class="muted">沙盒执行记录</div>
          <div class="bar-track"><span :style="{ width: (counts.generated ? counts.executed / counts.generated * 100 : 0) + '%' }"></span></div>
        </div>
        <div class="pdca-card">
          <div class="row" style="justify-content:space-between"><b>Act</b><span class="tag">回滚 {{ counts.rolled }}</span></div>
          <div class="muted">按检查点回滚</div>
          <div class="bar-track"><span :style="{ width: (counts.executed ? counts.rolled / Math.max(counts.executed, 1) * 100 : 0) + '%' }"></span></div>
        </div>
      </div>
      <div class="grid-2 eq" style="margin-top:14px">
        <div class="subcard">
          <b>Plan · 策略规划</b>
          <ul>
            <li>对照需求趋势与竞品价格</li>
            <li>生成定价与活动建议</li>
            <li>价格落在房型上下限内</li>
          </ul>
        </div>
        <div class="subcard">
          <b>Do · 人审执行</b>
          <ul>
            <li>待人审策略 {{ counts.pending }} 条</li>
            <li>待人审回复 {{ counts.pendingReplies }} 条</li>
            <li>已执行 {{ counts.executed }} 条（含自动执行）</li>
          </ul>
        </div>
      </div>
    </div>

    <div class="space"></div>
    <div class="card card-pad">
      <ChannelConnections :items="channels" compact @refresh="load" />
      <p class="muted" style="margin:8px 0 0">完整的本店 OTA 账号授权请到 <router-link class="link" to="/settings?section=ota">设置 · 渠道连接</router-link>。官方契约由运维端配置。</p>
    </div>
    <div class="space"></div>
    <WriteMonitor :items="writes" @refresh="load" />
    <p class="muted" style="margin:8px 0 0">也可在 <router-link class="link" to="/execution?tab=writes">执行中心 · 写入监控</router-link> 查看同一账本。</p>

    <div class="space"></div>
    <div class="card card-pad">
      <div class="section-title">
        决策与审计日志
        <span class="row">
          <button type="button" class="link" @click="exportLog">导出审计日志</button>
          <router-link class="link" to="/audit">查看全部</router-link>
        </span>
      </div>
      <div v-if="!logs.length" class="empty">暂无审计记录。生成并人审一条策略后会出现在这里。</div>
      <div v-for="r in logs.slice(0, 12)" :key="r.id" class="queue-item">
        <div class="meta">
          <h3>{{ formatDate(r.created_at) }} · {{ actionLabel(r.action) }}</h3>
          <p>操作人 {{ r.actor }} · {{ r.entity_type }} #{{ r.entity_id }} {{ r.reason || "" }}</p>
        </div>
        <button
          v-if="canRollback(r)"
          class="btn btn-sm"
          type="button"
          :disabled="busy"
          @click="rollbackFromLog(r)"
        >回滚决策</button>
        <span v-else class="muted">{{ r.after?.checkpoint_id ? "ck " + shortId(r.after.checkpoint_id) : "" }}</span>
      </div>
    </div>

    <div class="space"></div>
    <div class="grid-3">
      <div class="card card-pad" style="text-align:center">
        <div class="section-title" style="justify-content:center">未关闭预警</div>
        <div class="ring-wrap">
          <svg viewBox="0 0 120 120">
            <circle cx="60" cy="60" r="46" fill="none" stroke="#e5e7eb" stroke-width="10" />
            <circle cx="60" cy="60" r="46" fill="none" :stroke="openAlerts ? '#f54900' : '#00c950'" stroke-width="10" stroke-linecap="round"
              :stroke-dasharray="circumference" :stroke-dashoffset="circumference * (1 - Math.min(openAlerts, 8) / 8)"
              transform="rotate(-90 60 60)" />
          </svg>
          <div class="center"><b>{{ openAlerts }}</b>{{ openAlerts ? "条待处理" : "暂无未处理" }}</div>
        </div>
        <p class="muted">风险提示来自预警数量，不是自动风控评分。</p>
      </div>
      <div class="card card-pad">
        <div class="section-title">策略状态分布</div>
        <div v-for="row in statusRows" :key="row.label" style="margin-bottom:12px">
          <div class="row" style="justify-content:space-between"><span>{{ row.label }}</span><b>{{ row.n }}</b></div>
          <div class="bar-track" :class="row.cls"><span :style="{ width: row.pct + '%' }"></span></div>
        </div>
      </div>
      <div class="card card-pad">
        <div class="section-title">关注重点</div>
        <div v-for="row in weights" :key="row.label" style="margin-bottom:12px">
          <div class="row" style="justify-content:space-between"><span>{{ row.label }}</span><span>{{ row.desc }}</span></div>
          <div class="bar-track"><span :style="{ width: row.pct + '%', background: row.color }"></span></div>
        </div>
        <p class="muted">权重为说明性排布，便于对照演示故事，不是实时优化目标。</p>
      </div>
    </div>
</template>

<script setup>
import { computed, inject, onMounted, ref, watch } from "vue";
import LineChart from "../components/LineChart.vue";
import { can } from "../utils/permissions";
import WriteMonitor from "../components/WriteMonitor.vue";
import ChannelConnections from "../components/ChannelConnections.vue";
import { api } from "../api/client";
import { actionLabel, clsDelta, downloadText, formatDate, pct, shortId, signed, toCsv, yen } from "../utils/format";

const summary = ref(null);
const rooms = ref([]);
const series = ref([]);
const strategies = ref([]);
const reviews = ref([]);
const logs = ref([]);
const writes = ref([]);
const channels = ref([]);
const alerts = ref([]);
const busy = ref(false);
const error = ref("");
const saved = ref("");
const savingPolicy = ref(false);
const policy = ref({
  auto_execute_enabled: false,
  holiday_requires_review: true,
  price_change_human_threshold_pct: 15,
  rollback_policy: "manual",
});
const shell = inject("appShell", null);
const circumference = 2 * Math.PI * 46;

const kpis = computed(() => summary.value?.kpis);
const openAlerts = computed(() => (alerts.value || []).filter((a) => a.status === "open").length);
const counts = computed(() => {
  const s = strategies.value;
  return {
    generated: s.length,
    pending: s.filter((x) => x.status === "pending_review").length,
    executed: s.filter((x) => x.status === "executed").length,
    rolled: s.filter((x) => x.status === "rolled_back").length,
    pendingReplies: reviews.value.filter((r) => r.reply_status === "pending_review").length,
  };
});
function barW(delta) {
  const v = Number(delta || 0);
  return Math.max(8, Math.min(100, 50 + v)) + "%";
}
const histSeries = computed(() => (series.value || []).filter((p) => !p.is_future));
const occLabels = computed(() => histSeries.value.map((p) => p.date));
const occHist = computed(() => histSeries.value.map((p) => Number(p.occupancy || 0) * 100));
const windowOcc = computed(() => {
  const hist = histSeries.value;
  if (!hist.length) return [];
  const mid = Math.ceil(hist.length / 2);
  const avg = (arr) => (arr.length ? arr.reduce((s, p) => s + Number(p.occupancy || 0) * 100, 0) / arr.length : 0);
  return [avg(hist.slice(0, mid)).toFixed(1), avg(hist.slice(mid)).toFixed(1)];
});
const statusRows = computed(() => {
  const total = strategies.value.length || 1;
  return [
    { label: "待人审", n: counts.value.pending, pct: counts.value.pending / total * 100, cls: "" },
    { label: "已执行", n: counts.value.executed, pct: counts.value.executed / total * 100, cls: "green" },
    { label: "已回滚", n: counts.value.rolled, pct: counts.value.rolled / total * 100, cls: "" },
  ];
});
const weights = [
  { label: "搜索排名", desc: "演示主线", pct: 40, color: "#155dfc" },
  { label: "价格倒挂", desc: "竞对对照", pct: 30, color: "#f59e0b" },
  { label: "差评回复", desc: "人审必过", pct: 20, color: "#22c55e" },
  { label: "转化走势", desc: "辅助观察", pct: 10, color: "#a78bfa" },
];

function canRollback(r) {
  return r.action === "review_approve" && r.entity_type === "strategy" && r.entity_id;
}
async function rollbackFromLog(r) {
  if (!confirm("将按检查点回滚该策略的沙盒执行，确定继续？")) return;
  busy.value = true;
  error.value = "";
  try {
    await api.rollbackStrategy(r.entity_id, "从决策日志回滚");
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
function exportLog() {
  const rows = [["时间", "操作人", "动作", "对象", "说明"], ...logs.value.map((r) => [r.created_at, r.actor, r.action, `${r.entity_type}#${r.entity_id}`, r.reason || ""])];
  downloadText("oiro-audit.csv", toCsv(rows), "text/csv;charset=utf-8");
}
async function toggleAuto(on) {
  savingPolicy.value = true;
  error.value = "";
  saved.value = "";
  try {
    const res = await api.updateSettings({ execution_policy: { auto_execute_enabled: !!on } });
    policy.value = { ...policy.value, ...(res.execution_policy || {}) };
    saved.value = on ? "已开启低风险自动执行。中高风险和评价回复仍须人审。" : "已关闭自动执行，所有动作重新回到人审。";
  } catch (e) {
    error.value = e.message;
  } finally {
    savingPolicy.value = false;
  }
}
async function load() {
  const [s, r, t, st, rv, a, au, cfg, wr] = await Promise.all([
    api.summary(), api.rooms(), api.timeseries(), api.strategies(), api.reviews(), api.alerts(), api.audit(), api.settings(), api.writes(),
  ]);
  summary.value = s;
  rooms.value = r.rooms || [];
  series.value = t.series || [];
  strategies.value = st.strategies || [];
  reviews.value = rv.reviews || [];
  alerts.value = a.alerts || [];
  logs.value = au.logs || [];
  writes.value = wr.writes || [];
  if (cfg.execution_policy) policy.value = { ...policy.value, ...cfg.execution_policy };
  channels.value = cfg.channels || [];
}
onMounted(async () => { try { await load(); } catch (e) { error.value = e.message; } });
if (shell?.refreshTick) watch(shell.refreshTick, () => load().catch((e) => { error.value = e.message; }));
</script>
