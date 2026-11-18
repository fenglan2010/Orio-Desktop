<template>
    <div class="page-head">
      <div>
        <h1>L1 数据观察员 - 实时监控大屏</h1>
        <p>客房收入口径。本周营收为周一至今日，对比上周同期；RevPAR、ADR、OCC、今日订单、今日营收、浏览量、转化率为今日营业日指标，对比上周同日。数据来自携程模拟适配器。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="roomFilter = roomFilter ? '' : 'all'">
          <Icon name="filter" :size="14" /> {{ roomFilter ? "清除筛选" : "筛选房型" }}
        </button>
        <span class="chip"><Icon name="calendar" :size="14" /> {{ summary?.flash_window || "本周 vs 上周同期 · 今日 vs 上周同日" }}</span>
        <button class="btn btn-primary" type="button" @click="exportReport"><Icon name="download" :size="14" /> 导出报告</button>
      </div>
    </div>

    <div v-if="error" class="error">{{ error }}</div>
    <div v-if="!summary" class="empty">正在加载看板…</div>
    <template v-else>
      <div class="grid-kpi">
        <div class="card card-pad kpi" v-for="k in kpis" :key="k.key">
          <div class="label">{{ k.label }}</div>
          <div class="value">{{ k.value }}</div>
          <div class="delta" :class="k.cls">{{ k.delta }}</div>
          <div class="kpi-ico" :class="k.ico">{{ k.mark }}</div>
        </div>
      </div>

      <div class="space"></div>
      <div class="grid-2 eq" style="margin-bottom:16px">
        <div class="card card-pad kpi" v-for="k in extraKpis" :key="k.key">
          <div class="label">{{ k.label }}</div>
          <div class="value" style="font-size:22px">{{ k.value }}</div>
          <div class="delta" :class="k.cls">{{ k.delta }}</div>
        </div>
      </div>

      <div class="grid-2">
        <div class="card card-pad">
          <div class="section-title">总体经营趋势 <span class="muted" style="font-weight:500;font-size:12px">近 14 日实际</span></div>
          <LineChart :series="trendSeries" :labels="histLabels" :area="false" :height="220" />
          <div class="legend">
            <span><i style="background:#155dfc"></i>营收</span>
            <span><i style="background:#22c55e"></i>ADR</span>
            <span><i style="background:#f59e0b"></i>出租率%</span>
          </div>
        </div>
        <div class="card card-pad">
          <div class="section-title">
            预计节奏对比
            <span class="muted" style="font-weight:500;font-size:12px">历史出租率 vs 未来预估（模拟）</span>
          </div>
          <LineChart :series="paceSeries" :labels="allLabels" :area="true" :height="220" />
          <div class="legend">
            <span><i style="background:#155dfc"></i>近14日实际</span>
            <span><i style="background:#93c5fd"></i>未来14日预估</span>
          </div>
        </div>
      </div>

      <div class="space"></div>
      <div class="grid-2">
        <div class="card card-pad">
          <div class="section-title">
            房型表现（近 7 日）
            <button type="button" class="link" @click="exportRooms">导出 CSV</button>
          </div>
          <table class="data">
            <thead>
              <tr><th>房型</th><th>营收</th><th>出租率</th><th>ADR</th><th>排名</th><th>转化</th></tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in shownRooms" :key="r.id">
                <td><span class="dot-ch" :style="{ background: pieColors[i % pieColors.length] }"></span>{{ r.name }}</td>
                <td class="mono">{{ yen(r.revenue) }}</td>
                <td class="mono">{{ pct(r.occupancy) }}</td>
                <td class="mono">{{ yen(r.adr) }}</td>
                <td class="mono">{{ r.search_rank }}</td>
                <td class="mono">{{ pct(r.conversion_rate) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="card card-pad">
          <div class="section-title">房型营收占比</div>
          <PieChart :items="pieItems" />
          <div class="space"></div>
          <div class="section-title" style="margin-top:8px">竞对价格带（近 7 日）</div>
          <div class="statline">
            <div>我方均价<b>{{ yen(band.our_avg_list) }}</b></div>
            <div>竞对中位<b>{{ yen(band.competitor_median) }}</b></div>
            <div>竞对区间<b>{{ yen(band.competitor_low) }}–{{ money(band.competitor_high) }}</b></div>
          </div>
          <p class="muted">若我方挂牌价持续高于竞对中位，系统会记为价格倒挂信号。本页没有渠道分账数据，故以房型表现替代原型中的「渠道绩效」。</p>
        </div>
      </div>

      <div class="space"></div>
      <div class="card card-pad" id="alerts">
        <div class="section-title">
          <span>异常告警中心</span>
          <span class="muted" style="font-weight:500">{{ openCount }} 条未处理</span>
        </div>
        <p class="muted" v-if="hiddenOpenCount">已按通知设置隐藏 {{ hiddenOpenCount }} 条未关闭预警，可在系统设置里重新打开。</p>

        <div v-if="!visibleAlerts.length" class="empty">{{ alerts.length ? "按当前通知设置，没有需要提醒的预警。" : "暂无预警。" }}</div>
        <div
          v-for="a in visibleAlerts"
          :key="a.id"
          class="alert-row"
          :class="'sev-' + (a.severity || 'medium')"
        >
          <span class="bullet" :class="a.severity || 'medium'"></span>
          <div class="body">
            <h3>{{ fixDirectionCopy(a.title) }}</h3>
            <p>
              {{ a.metric_date || "—" }}
              · <StatusBadge :value="a.alert_type" />
              · <StatusBadge :value="a.status" />
            </p>
          </div>
          <div class="alert-actions">
            <button type="button" @click="openAlert(a)">查看</button>
            <button type="button" class="mute" v-if="a.status === 'open'" @click="ack(a)">忽略</button>
          </div>
        </div>
      </div>
    </template>

    <div v-if="current" class="modal" @click.self="current = null">
      <div class="panel">
        <div class="row" style="justify-content:space-between;align-items:flex-start">
          <div>
            <div class="row"><StatusBadge :value="current.alert_type" /><StatusBadge :value="current.severity" /><StatusBadge :value="current.status" /></div>
            <h2 style="margin:8px 0 4px">{{ fixDirectionCopy(current.title) }}</h2>
            <p>{{ fixDirectionCopy(current.body) }}</p>
            <p class="muted">发生日期 {{ current.metric_date || "—" }}</p>
          </div>
          <button class="icon-btn" type="button" aria-label="关闭" title="关闭" @click="current=null"><Icon name="close" /></button>
        </div>
        <div class="row" style="margin: 12px 0">
          <button class="btn btn-primary" :disabled="loading" @click="diagnose">{{ loading ? "诊断中…" : "生成诊断" }}</button>
          <button class="btn" v-if="current.status === 'open'" @click="ack(current)">标记已确认</button>
        </div>
        <div v-if="current.diagnosis">
          <div class="section-title">诊断结果</div>
          <p>
            <StatusBadge :value="current.diagnosis.classification" />
            <span class="muted" style="margin-left:8px">{{ current.diagnosis.classification_label }}</span>
          </p>
          <p>{{ fixDirectionCopy(current.diagnosis.narrative) }}</p>
          <ul>
            <li v-for="(c, i) in current.diagnosis.primary_causes || []" :key="i">
              <strong>{{ c.factor }}</strong> · {{ fixDirectionCopy(c.detail) }}
            </li>
          </ul>
          <p class="muted" v-if="current.diagnosis.not_causes">排除：{{ (current.diagnosis.not_causes || []).join("；") }}</p>
          <p v-if="current.diagnosis.degraded" class="notice">{{ current.diagnosis.degrade_notice }}</p>
        </div>
        <p v-if="error" class="error">{{ error }}</p>
      </div>
    </div>
</template>

<script setup>
import { computed, inject, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import LineChart from "../components/LineChart.vue";
import PieChart from "../components/charts/PieChart.vue";
import StatusBadge from "../components/StatusBadge.vue";
import Icon from "../components/Icon.vue";
import { api } from "../api/client";
import { loadNotify } from "../utils/prefs";
import { clsDelta, downloadText, fixDirectionCopy, money, pct, rankDelta, signed, toCsv, yen } from "../utils/format";
import { useEscape } from "../composables/useEscape";

const pieColors = ["#3b82f6", "#34d399", "#f59e0b", "#f87171", "#a78bfa"];
const shell = inject("appShell", null);
const route = useRoute();
const summary = ref(null);
const series = ref([]);
const rooms = ref([]);
const alerts = ref([]);
const notify = ref(loadNotify());
const current = ref(null);
const loading = ref(false);
const error = ref("");
const roomFilter = ref("");

const band = computed(() => summary.value?.competitor_band || {});
const hist = computed(() => series.value.filter((p) => !p.is_future));
const future = computed(() => series.value.filter((p) => p.is_future));
const histLabels = computed(() => hist.value.map((p) => p.date));
const allLabels = computed(() => series.value.map((p) => p.date));
const shownRooms = computed(() => rooms.value);
const pieItems = computed(() =>
  rooms.value.map((r, i) => ({ name: r.name, value: r.revenue, sub: yen(r.revenue), color: pieColors[i % pieColors.length] }))
);
const visibleAlerts = computed(() =>
  (alerts.value || []).filter((a) => a.status !== "open" || notify.value[a.alert_type] !== false)
);
const hiddenOpenCount = computed(
  () => (alerts.value || []).filter((a) => a.status === "open" && notify.value[a.alert_type] === false).length
);
const openCount = computed(() => visibleAlerts.value.filter((a) => a.status === "open").length);


const trendSeries = computed(() => [
  { name: "营收", values: hist.value.map((p) => p.revenue), color: "#155dfc" },
  { name: "ADR", values: hist.value.map((p) => p.adr), color: "#22c55e" },
  { name: "出租率", values: hist.value.map((p) => Number(p.occupancy || 0) * 100), color: "#f59e0b" },
]);

const paceSeries = computed(() => {
  const histOcc = series.value.map((p) => (p.is_future ? null : Number(p.occupancy || 0) * 100));
  const futOcc = series.value.map((p) => (p.is_future ? Number(p.occupancy || 0) * 100 : null));
  // connect at the junction: last hist point copied into future series start for a continuous look
  const lastHist = hist.value[hist.value.length - 1];
  const idx = series.value.findIndex((p) => p.is_future);
  if (idx > 0 && lastHist) futOcc[idx - 1] = Number(lastHist.occupancy || 0) * 100;
  return [
    { name: "实际", values: histOcc.map((v) => (v == null ? undefined : v)), color: "#155dfc" },
    { name: "预估", values: futOcc.map((v) => (v == null ? undefined : v)), color: "#93c5fd", area: true },
  ];
});

const kpis = computed(() => {
  const k = summary.value?.kpis;
  if (!k) return [];
  const d = k.delta || {};
  const views = Number(k.today_pageviews || 0).toLocaleString("zh-CN");
  return [
    { key: "week_rev", label: "本周营收", value: yen(k.week_revenue), delta: signed(d.week_revenue, "%") + " vs 上周同期", cls: clsDelta(d.week_revenue), ico: "blue", mark: "¥" },
    { key: "revpar", label: "RevPAR", value: yen(k.revpar, 1), delta: signed(d.revpar, "%") + " vs 上周同日", cls: clsDelta(d.revpar), ico: "green", mark: "¥" },
    { key: "adr", label: "ADR", value: yen(k.today_adr, 1), delta: signed(d.today_adr, "%") + " vs 上周同日", cls: clsDelta(d.today_adr), ico: "green", mark: "¥" },
    { key: "occ", label: "OCC", value: pct(k.today_occupancy), delta: signed(d.today_occupancy, "个百分点") + " vs 上周同日", cls: clsDelta(d.today_occupancy), ico: "purple", mark: "%" },
    { key: "orders", label: "今日订单", value: String(k.today_orders ?? 0) + " 单", delta: signed(d.today_orders, "%") + " vs 上周同日", cls: clsDelta(d.today_orders), ico: "orange", mark: "单" },
    { key: "today_rev", label: "今日营收", value: yen(k.today_revenue), delta: signed(d.today_revenue, "%") + " vs 上周同日", cls: clsDelta(d.today_revenue), ico: "blue", mark: "¥" },
    { key: "views", label: "浏览量", value: views, delta: signed(d.today_pageviews, "%") + " vs 上周同日", cls: clsDelta(d.today_pageviews), ico: "orange", mark: "览" },
    { key: "conv", label: "转化率", value: pct(k.today_conversion_rate, 2), delta: signed(d.today_conversion_rate, "个百分点") + " vs 上周同日", cls: clsDelta(d.today_conversion_rate), ico: "purple", mark: "%" },
  ];
});
const extraKpis = computed(() => {
  const k = summary.value?.kpis;
  if (!k) return [];
  const d = k.delta || {};
  return [
    { key: "rank", label: "搜索排名", value: String(k.search_rank), delta: rankDelta(d.search_rank) + "（数值越大越靠后）", cls: d.search_rank > 0 ? "rank-worse" : "rank-better" },
    { key: "band", label: "竞对中位价 vs 我方", value: yen(band.value.competitor_median), delta: "我方均价 " + yen(band.value.our_avg_list), cls: "" },
  ];
});

async function load() {
  const [s, t, r, a] = await Promise.all([api.summary(), api.timeseries(), api.rooms(), api.alerts()]);
  summary.value = s;
  series.value = t.series || [];
  rooms.value = r.rooms || [];
  alerts.value = a.alerts || [];
}
function openAlert(a) {
  current.value = a;
}
async function diagnose() {
  loading.value = true;
  error.value = "";
  try {
    current.value = await api.diagnose(current.value.id);
    await load();
    current.value = alerts.value.find((x) => x.id === current.value.id) || current.value;
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
async function ack(a) {
  try {
    await api.ackAlert(a.id);
    await load();
    if (current.value?.id === a.id) current.value = alerts.value.find((x) => x.id === a.id) || null;
  } catch (e) {
    error.value = e.message;
  }
}
function exportRooms() {
  const rows = [["房型", "营收", "出租率", "ADR", "排名", "转化"], ...rooms.value.map((r) => [r.name, r.revenue, r.occupancy, r.adr, r.search_rank, r.conversion_rate])];
  downloadText("oiro-rooms.csv", toCsv(rows), "text/csv;charset=utf-8");
}
function exportReport() {
  const k = summary.value?.kpis || {};
  const lines = [
    ["指标", "数值", "对比"],
    ["本周营收", k.week_revenue, k.delta?.week_revenue],
    ["RevPAR", k.revpar, k.delta?.revpar],
    ["ADR", k.today_adr, k.delta?.today_adr],
    ["OCC", k.today_occupancy, k.delta?.today_occupancy],
    ["今日订单", k.today_orders, k.delta?.today_orders],
    ["今日营收", k.today_revenue, k.delta?.today_revenue],
    ["浏览量", k.today_pageviews, k.delta?.today_pageviews],
    ["转化率", k.today_conversion_rate, k.delta?.today_conversion_rate],
    ["搜索排名（近7日）", k.search_rank, k.delta?.search_rank],
    ["竞对中位价", band.value.competitor_median, ""],
    ["我方均价", band.value.our_avg_list, ""],
  ];
  downloadText("oiro-dashboard.csv", toCsv(lines), "text/csv;charset=utf-8");
}

function focusAlerts() {
  if (route.query.focus !== "alerts") return;
  nextTick(() => {
    document.getElementById("alerts")?.scrollIntoView({ behavior: "smooth", block: "start" });
    if (route.query.id) {
      const a = alerts.value.find((x) => String(x.id) === String(route.query.id));
      if (a) current.value = a;
    }
  });
}

useEscape(current, () => { current.value = null; });

function onNotifyPref(e) {
  notify.value = e?.detail || loadNotify();
}
onMounted(async () => {
  window.addEventListener("oiro-notify-changed", onNotifyPref);
  try {
    await load();
    focusAlerts();
  } catch (e) {
    error.value = e.message;
  }
});
onUnmounted(() => window.removeEventListener("oiro-notify-changed", onNotifyPref));
watch(() => route.query, focusAlerts);
if (shell?.refreshTick) {
  watch(shell.refreshTick, () => load().catch((e) => { error.value = e.message; }));
}
</script>
