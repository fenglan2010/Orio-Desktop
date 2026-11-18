<template>
    <div class="page-head">
      <div>
        <h1>L2 策略分析师 - 多维分析</h1>
        <p>对照近 7 日真实指标与竞对价格，生成可解释的策略建议。价格由启发式计算，大模型只写理由。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="selected = ['demand', 'stock', 'pace', 'history', 'conv', 'comp']">
          <Icon name="filter" :size="14" /> 重置维度
        </button>
        <button class="btn btn-primary" type="button" v-if="can('generate')" :disabled="generating" @click="generate">
          {{ generating ? "生成中…" : "一键生成策略" }}
        </button>
      </div>
    </div>
    <div v-if="error" class="error">{{ error }}</div>

    <div class="card card-pad" style="margin-bottom:16px">
      <div class="section-title">分析维度组合</div>
      <div class="chip-grid">
        <button
          v-for="d in dimensions"
          :key="d.id"
          type="button"
          class="dim-chip"
          :class="{ active: selected.includes(d.id) }"
          @click="toggle(d.id)"
        >{{ d.label }}</button>
      </div>
    </div>

    <div class="card card-pad" style="margin-bottom:16px">
      <div class="section-title">按日期诊断</div>
      <p class="muted">选择今天或更早的已实现经营日，系统用该日及此前两周的排名、转化、竞对价格和评价做因果拆解。未来入住日只有在手订单，不能诊断。</p>
      <div class="row" style="gap:10px;align-items:center;margin:10px 0 4px;flex-wrap:wrap">
        <input type="date" v-model="diagnoseDate" :min="minDiagnoseDate" :max="maxDiagnoseDate" />
        <button class="btn btn-primary" type="button" :disabled="diagnosing || !diagnoseDate" @click="runDateDiagnosis">
          {{ diagnosing ? "诊断中…" : "生成诊断" }}
        </button>
      </div>
      <div v-if="dateDiagnosis">
        <div class="statline" v-if="dateDiagnosis.snapshot">
          <div>当日营收<br><b>{{ yen(dateDiagnosis.snapshot.metrics.revenue) }}</b>
            <small v-if="dateDiagnosis.snapshot.delta" :class="deltaClass(dateDiagnosis.snapshot.delta.revenue)">{{ signed(dateDiagnosis.snapshot.delta.revenue, "%") }}</small>
          </div>
          <div>出租率<br><b>{{ pct(dateDiagnosis.snapshot.metrics.occupancy, 1) }}</b>
            <small v-if="dateDiagnosis.snapshot.delta" :class="deltaClass(dateDiagnosis.snapshot.delta.occupancy_pp)">{{ signed(dateDiagnosis.snapshot.delta.occupancy_pp, "pp") }}</small>
          </div>
          <div>转化率<br><b>{{ pct(dateDiagnosis.snapshot.metrics.conversion_rate, 2) }}</b>
            <small v-if="dateDiagnosis.snapshot.delta" :class="deltaClass(dateDiagnosis.snapshot.delta.conversion_pp)">{{ signed(dateDiagnosis.snapshot.delta.conversion_pp, "pp") }}</small>
          </div>
          <div>搜索排名<br><b>{{ dateDiagnosis.snapshot.metrics.search_rank || "—" }}</b>
            <small v-if="dateDiagnosis.snapshot.delta" class="muted">较上周同日 {{ signed(dateDiagnosis.snapshot.delta.search_rank) }}</small>
          </div>
        </div>
        <p class="muted" v-if="dateDiagnosis.snapshot?.in_progress">今日为在途经营日，以上是截至当前的已实现数据，不是全天锁定结果。</p>
        <p class="muted" v-else-if="dateDiagnosis.snapshot?.reference_date">对照为上周同日（{{ dateDiagnosis.snapshot.reference_date }}），不是同比预售曲线。</p>
        <p style="margin-top:10px">
          <StatusBadge :value="dateDiagnosis.classification" />
          <span class="muted" style="margin-left:8px">{{ dateDiagnosis.classification_label }}</span>
        </p>
        <p v-if="dateDiagnosis.narrative">{{ dateDiagnosis.narrative }}</p>
        <ul class="findings" v-if="dateDiagnosis.primary_causes?.length">
          <li v-for="(c, i) in dateDiagnosis.primary_causes" :key="i">
            <strong>{{ c.factor }}</strong> · {{ c.detail }}
          </li>
        </ul>
        <p class="muted" v-if="dateDiagnosis.not_causes?.length">排除：{{ dateDiagnosis.not_causes.join("；") }}</p>
        <div v-if="dateDiagnosis.related_alerts?.length" style="margin-top:8px">
          <div class="muted">邻近预警</div>
          <ul class="findings">
            <li v-for="a in dateDiagnosis.related_alerts" :key="a.id">{{ a.metric_date }} · {{ a.title }}</li>
          </ul>
        </div>
        <p class="muted">{{ dateDiagnosis.window_note }}</p>
        <p class="notice" v-if="dateDiagnosis.degraded">{{ dateDiagnosis.degrade_notice }}</p>
      </div>
    </div>

    <div class="grid-2">
      <div class="card card-pad" v-if="on('demand')">
        <div class="section-title">
          需求趋势分析
          <span class="tag info">由出租率映射</span>
        </div>
        <LineChart :series="demandSeries" :labels="labels" :height="220" />
        <div class="legend">
          <span><i style="background:#93c5fd;border-top:1px dashed #93c5fd"></i>入住日在手出租率</span>
          <span><i style="background:#155dfc"></i>实际出租率</span>
        </div>
        <p class="muted">今日之后的点是入住日已售对应的在手出租率，不是单独的预测模型。</p>
      </div>
      <div class="card card-pad" v-if="on('comp')">
        <div class="section-title">
          竞品定位对比
          <span class="tag success">近 7 日均价</span>
        </div>
        <div class="statline">
          <div v-for="c in compSummary" :key="c.name">
            {{ c.name }}
            <b>{{ yen(c.price) }}</b>
            <span class="muted">排名 {{ c.rank || "—" }}</span>
          </div>
        </div>
        <LineChart v-if="compTrend.labels.length" :series="compTrend.series" :labels="compTrend.labels" :height="180" />
        <p class="muted" v-else>暂无竞对时序。</p>
        <p class="muted">横轴为日期，纵轴为挂牌价。竞对数据来自模拟适配器，不是实时抓取。</p>
      </div>
    </div>

    <div class="space"></div>
    <div class="grid-2">
      <div class="card card-pad" v-if="on('stock')">
        <div class="section-title">
          库存压力
          <span class="tag" :class="pressureClass(stock?.pressure)">{{ stock?.pressure_label || "—" }}</span>
        </div>
        <div class="statline" v-if="stock">
          <div>未来 7 日在手出租率<br><b>{{ pct(stock.otb_occupancy, 1) }}</b></div>
          <div>剩余可售<br><b>{{ stock.remaining_nights }} 间夜</b></div>
          <div>可售合计<br><b>{{ stock.capacity_nights }} 间夜</b></div>
        </div>
        <BarChart
          v-if="stockBars.categories.length"
          :categories="stockBars.categories"
          :series="[{ name: '剩余可售', values: stockBars.values, color: '#155dfc' }]"
          :height="200"
        />
        <div class="legend"><span><i style="background:#155dfc"></i>今日起 14 日剩余可售间数</span></div>
        <div class="room-pressure" v-if="stock?.by_room?.length">
          <div v-for="r in stock.by_room" :key="r.room_type_id" class="row" style="justify-content:space-between;margin-top:6px">
            <span>{{ r.name }}</span>
            <span class="muted">剩余 {{ r.remaining }} 间夜 · 在手 {{ pct(r.occupancy, 1) }}</span>
            <span class="tag" :class="pressureClass(r.pressure)">{{ r.pressure_label }}</span>
          </div>
        </div>
        <ul class="findings" v-if="stock?.findings?.length">
          <li v-for="(f, i) in stock.findings" :key="i">{{ f }}</li>
        </ul>
        <p class="muted">口径：今日及以后按入住日在手订单统计剩余可售，不是预测空房。</p>
      </div>
      <div class="card card-pad" v-if="on('pace')">
        <div class="section-title">预订节奏</div>
        <div class="statline" v-if="pace">
          <div>近 7 日新订<br><b>{{ pace.bookings_last7 }} 单</b></div>
          <div>前 7 日新订<br><b>{{ pace.bookings_prev7 }} 单</b></div>
          <div>今日 vs 上周同日<br><b>{{ pace.today_bookings }} / {{ pace.last_week_same_day_bookings }}</b></div>
        </div>
        <LineChart v-if="paceBooking.labels.length" :series="paceBooking.series" :labels="paceBooking.labels" :height="180" />
        <div class="legend"><span><i style="background:#155dfc"></i>每日渠道新订（含今日在途）</span></div>
        <LineChart v-if="paceOtb.labels.length" :series="paceOtb.series" :labels="paceOtb.labels" :height="180" />
        <div class="legend">
          <span><i style="background:#155dfc"></i>入住日在手出租率</span>
          <span><i style="background:#f59e0b"></i>上周同星期最终出租率（参考）</span>
        </div>
        <ul class="findings" v-if="pace?.findings?.length">
          <li v-for="(f, i) in pace.findings" :key="i">{{ f }}</li>
        </ul>
        <p class="muted">{{ pace?.reference_note }}</p>
      </div>
    </div>

    <div class="space"></div>
    <div class="grid-2">
      <div class="card card-pad" v-if="on('history')">
        <div class="section-title">历史对比</div>
        <div class="statline" v-if="history">
          <div>近 7 日营收<br><b>{{ yen(history.last7.revenue) }}</b>
            <small v-if="history.delta" :class="deltaClass(history.delta.revenue)">{{ signed(history.delta.revenue, "%") }}</small>
          </div>
          <div>近 7 日出租率<br><b>{{ pct(history.last7.occupancy, 1) }}</b>
            <small v-if="history.delta" :class="deltaClass(history.delta.occupancy_pp)">{{ signed(history.delta.occupancy_pp, "pp") }}</small>
          </div>
          <div>近 7 日 ADR<br><b>{{ yen(history.last7.adr) }}</b>
            <small v-if="history.delta" :class="deltaClass(history.delta.adr)">{{ signed(history.delta.adr, "%") }}</small>
          </div>
          <div>今日 vs 上周同日营收<br><b>{{ yen(history.today.revenue) }}</b>
            <small v-if="history.today_delta" :class="deltaClass(history.today_delta.revenue)">{{ signed(history.today_delta.revenue, "%") }}</small>
          </div>
        </div>
        <BarChart
          v-if="historyRoomBars.categories.length"
          :categories="historyRoomBars.categories"
          :series="historyRoomBars.series"
          :height="200"
        />
        <div class="legend">
          <span><i style="background:#155dfc"></i>近 7 日营收</span>
          <span><i style="background:#93c5fd"></i>前 7 日营收</span>
        </div>
        <ul class="findings" v-if="history?.findings?.length">
          <li v-for="(f, i) in history.findings" :key="i">{{ f }}</li>
        </ul>
        <p class="muted">{{ history?.reference_note }}</p>
      </div>
      <div class="card card-pad" v-if="on('conv')">
        <div class="section-title">渠道转化<span class="tag info">{{ conversion?.channel || "携程" }}</span></div>
        <div class="statline" v-if="conversion">
          <div>近 7 日转化率<br><b>{{ pct(conversion.last7.conversion_rate, 2) }}</b>
            <small v-if="conversion.delta_pp != null" :class="deltaClass(conversion.delta_pp)">{{ signed(conversion.delta_pp, "pp", 2) }}</small>
          </div>
          <div>近 7 日预订<br><b>{{ conversion.last7.bookings }} 单</b></div>
          <div>近 7 日浏览<br><b>{{ conversion.last7.clicks }} 次</b></div>
        </div>
        <LineChart v-if="convSeries.labels.length" :series="convSeries.series" :labels="convSeries.labels" :height="180" />
        <div class="legend"><span><i style="background:#155dfc"></i>每日转化率（预订/浏览）</span></div>
        <BarChart
          v-if="convRoomBars.categories.length"
          :categories="convRoomBars.categories"
          :series="[{ name: '转化率', values: convRoomBars.values, color: '#155dfc' }]"
          :height="180"
        />
        <div class="legend"><span><i style="background:#155dfc"></i>近 7 日各房型转化率</span></div>
        <ul class="findings" v-if="conversion?.findings?.length">
          <li v-for="(f, i) in conversion.findings" :key="i">{{ f }}</li>
        </ul>
        <p class="muted">{{ conversion?.reference_note }}</p>
      </div>
    </div>

    <div class="space"></div>
    <div class="grid-2">
      <div class="card card-pad" v-if="on('rooms') || on('channel')">
        <div class="section-title">{{ on('channel') && !on('rooms') ? "渠道净贡献分析" : "房型表现对比" }}</div>
        <BarChart
          :categories="rooms.map((r) => r.name)"
          :series="[{ name: '营收', values: rooms.map((r) => r.revenue), color: '#155dfc' }]"
          :height="220"
        />
        <div class="legend"><span><i style="background:#155dfc"></i>近 7 日营收</span></div>
        <div class="notice" style="margin-top:12px" v-if="on('channel')">
          发现：本轮没有分渠道订单数据，此图以房型营收代替原型中的渠道净贡献，避免用虚构渠道数字冒充实况。
        </div>
        <div class="notice" style="margin-top:12px" v-else>
          可对照各房型营收差异，为定价与活动建议提供依据。
        </div>
      </div>
      <div class="card card-pad" v-if="on('radar')">
        <div class="section-title">综合健康度雷达</div>
        <RadarChart :axes-data="radar" />
        <p class="muted">由近 7 日指标归一化得到，用于对照，不是独立评分模型。</p>
      </div>
    </div>

    <div class="card card-pad" style="margin-bottom:16px" v-if="unsupportedSelected.length">
      <div class="section-title">其它已选维度</div>
      <p class="muted">以下维度在本轮没有独立数据源，先保留筛选状态，避免用虚构数字填充。</p>
      <div class="chip-grid" style="margin-top:8px">
        <span class="dim-chip" v-for="d in unsupportedSelected" :key="d.id">{{ d.label }}</span>
      </div>
    </div>
    <div class="space"></div>
    <div class="card card-pad">
      <div class="section-title">
        <span>AI 策略建议</span>
        <button type="button" class="link" @click="exportStrategies" :disabled="!strategies.length">导出建议报告</button>
      </div>
      <div v-if="!strategies.length" class="empty">还没有策略。点击右上角「一键生成策略」，系统会走完整决策链路后进入人审队列。</div>
      <div
        v-for="s in strategies"
        :key="s.id"
        class="suggest"
        :class="s.risk_level || 'medium'"
      >
        <div class="row" style="justify-content:space-between;align-items:flex-start">
          <div style="flex:1;min-width:0">
            <h3>
              {{ titleOf(s) }}
              <StatusBadge :value="s.risk_level" />
              <span class="tag info">置信度 {{ pct0(s.confidence) }}</span>
              <StatusBadge :value="s.status" />
            </h3>
            <p v-if="s.kind === 'pricing'">
              建议 {{ yen(s.recommended_price) }}（区间 {{ yen(s.min_price) }}–{{ money(s.max_price) }}）。{{ previewText(s) }}
            </p>
            <p v-else>
              <span class="tag" :class="s.campaign_advice?.recommendation === 'join' ? 'success' : s.campaign_advice?.recommendation === 'join_limited' ? 'warning' : 'rejected'">
                {{ s.campaign_advice?.recommendation_label || (s.campaign_advice?.recommend_join ? "建议参加" : "暂不建议全量参加") }}
              </span>
              {{ s.campaign_advice?.scope || s.campaign_advice?.campaign_name || "活动参与建议" }}
            </p>
            <div class="impact" v-if="s.kind === 'pricing' && s.expected_impact">
              预期影响：价格 {{ signed(s.expected_impact.price_change_pct) }}%，
              出租率 {{ signed(s.expected_impact.occupancy_delta_pct) }} 个百分点，
              窗口营收约 {{ yen(s.expected_impact.revenue_delta) }}
            </div>
            <div class="impact" v-else-if="s.expected_impact">
              按此建议的预期影响：出租率 {{ signed(s.expected_impact.occupancy_delta_pct) }} 个百分点，
              ADR {{ signed(s.expected_impact.adr_delta_pct) }}%，
              窗口营收约 {{ yen(s.expected_impact.revenue_delta) }}
            </div>
          </div>
          <div class="row" style="flex-shrink:0">
            <button class="btn btn-primary btn-sm" type="button" @click="adopt(s)">查看并处理</button>
            <button class="btn btn-sm" type="button" @click="detail = s">查看详情</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="detail" class="modal" @click.self="detail=null">
      <div class="panel">
        <div class="row" style="justify-content:space-between">
          <div>
            <div class="section-title" style="margin:0">{{ titleOf(detail) }}</div>
            <div class="muted">{{ detail.date_from }} 至 {{ detail.date_to }}</div>
          </div>
          <div class="row">
            <StatusBadge :value="detail.status" />
            <StatusBadge :value="detail.risk_level" />
            <button class="icon-btn" type="button" aria-label="关闭" title="关闭" @click="detail=null"><Icon name="close" /></button>
          </div>
        </div>
        <template v-if="detail.kind === 'pricing'">
          <div class="statline">
            <div>当前价<br><b>{{ yen(detail.current_price) }}</b></div>
            <div>推荐价<br><b>{{ yen(detail.recommended_price) }}</b></div>
            <div>建议区间<br><b>{{ yen(detail.min_price) }}–{{ money(detail.max_price) }}</b></div>
            <div>置信度<br><b>{{ pct0(detail.confidence) }}</b></div>
          </div>
          <p class="muted" v-if="detail.constraints">
            约束：最低价 {{ yen(detail.constraints.floor) }}，最高价 {{ yen(detail.constraints.ceiling) }}，
            竞对中位 {{ yen(detail.constraints.competitor_median) }}
            <span v-if="detail.constraints.competitor_cut_pct">（近期下调约 {{ detail.constraints.competitor_cut_pct }}%）</span>。
          </p>
        </template>
        <template v-else>
          <CampaignAdvice :advice="detail.campaign_advice" :confidence="detail.confidence" :expected-impact="detail.expected_impact" />
        </template>
        <div class="section-title">策略说明</div>
        <p style="white-space:pre-wrap">{{ detail.rationale }}</p>
        <WorkflowTimeline
          :path="detail.path"
          :checkpoints="detail.checkpoints"
          :approvals="detail.approvals"
          :risk="detail.risk_level"
          :checkpoint-id="detail.checkpoint_id"
        />
        <div class="row" style="justify-content:flex-end;margin-top:12px">
          <button class="btn btn-primary" type="button" @click="adopt(detail)">前往执行队列</button>
        </div>
      </div>
    </div>
</template>

<script setup>
import { computed, inject, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import LineChart from "../components/LineChart.vue";
import BarChart from "../components/charts/BarChart.vue";
import RadarChart from "../components/charts/RadarChart.vue";
import CampaignAdvice from "../components/CampaignAdvice.vue";
import StatusBadge from "../components/StatusBadge.vue";
import WorkflowTimeline from "../components/WorkflowTimeline.vue";
import Icon from "../components/Icon.vue";
import { api } from "../api/client";
import { downloadText, money, pct, signed, toCsv, yen } from "../utils/format";
import { can } from "../utils/permissions";
import { useEscape } from "../composables/useEscape";

const dimensions = [
  { id: "demand", label: "需求趋势" },
  { id: "stock", label: "库存压力" },
  { id: "comp", label: "竞品定位" },
  { id: "channel", label: "渠道净贡献" },
  { id: "pace", label: "预订节奏" },
  { id: "cancel", label: "取消率" },
  { id: "event", label: "事件影响" },
  { id: "rooms", label: "房型表现" },
  { id: "guest", label: "客人分段" },
  { id: "conv", label: "渠道转化" },
  { id: "history", label: "历史对比" },
  { id: "elastic", label: "价格弹性" },
  { id: "commission", label: "佣金结构" },
  { id: "direct", label: "直接 vs OTA" },
  { id: "radar", label: "综合健康度" },
];

const selected = ref(["demand", "stock", "pace", "history", "conv", "comp"]);
const summary = ref(null);
const series = ref([]);
const stock = ref(null);
const pace = ref(null);
const history = ref(null);
const conversion = ref(null);
const diagnoseDate = ref("");
const dateDiagnosis = ref(null);
const diagnosing = ref(false);
const asOfToday = ref("");
const rooms = ref([]);
const comps = ref([]);
const strategies = ref([]);
const detail = ref(null);
const generating = ref(false);
const error = ref("");
const shell = inject("appShell", null);
const router = useRouter();

function on(id) {
  return selected.value.includes(id);
}
function toggle(id) {
  if (selected.value.includes(id)) selected.value = selected.value.filter((x) => x !== id);
  else selected.value = [...selected.value, id];
}
function titleOf(s) {
  if (s.kind === "campaign") return "活动参与建议";
  return (s.room_name || "房型") + " 定价建议";
}
function pct0(n) { return (Number(n || 0) * 100).toFixed(0) + "%"; }
function previewText(s) {
  return (s.rationale || "").replace(/\s+/g, " ").slice(0, 80) + ((s.rationale || "").length > 80 ? "…" : "");
}

const labels = computed(() => series.value.map((p) => p.date));
const demandSeries = computed(() => [
  { name: "实际", values: series.value.map((p) => (p.is_future ? undefined : Number(p.occupancy || 0) * 100)), color: "#155dfc" },
  { name: "入住日在手出租率", values: series.value.map((p) => (p.is_future ? Number(p.occupancy || 0) * 100 : undefined)), color: "#93c5fd", dashed: true },
]);

const recentComps = computed(() => {
  const rows = comps.value || [];
  const lastDates = [...new Set(rows.map((r) => r.date))].sort().slice(-7);
  return rows.filter((r) => lastDates.includes(r.date));
});
const compSummary = computed(() => {
  const map = new Map();
  for (const r of recentComps.value) {
    const cur = map.get(r.competitor) || { name: r.competitor, price: 0, rank: 0, n: 0 };
    cur.price += Number(r.price || 0);
    cur.rank += Number(r.rank || 0);
    cur.n += 1;
    map.set(r.competitor, cur);
  }
  const ours = summary.value?.competitor_band?.our_avg_list;
  const list = [...map.values()].map((c) => ({ name: c.name, price: c.n ? c.price / c.n : 0, rank: c.n ? (c.rank / c.n).toFixed(1) : "—" }));
  if (ours) list.unshift({ name: "本酒店", price: ours, rank: summary.value?.kpis?.search_rank });
  return list;
});
const compTrend = computed(() => {
  const names = [...new Set(recentComps.value.map((r) => r.competitor))].slice(0, 3);
  const dates = [...new Set(recentComps.value.map((r) => r.date))].sort();
  const colors = ["#f59e0b", "#22c55e", "#a78bfa"];
  const seriesOut = names.map((name, i) => ({
    name,
    color: colors[i % colors.length],
    values: dates.map((d) => {
      const rows = recentComps.value.filter((r) => r.competitor === name && r.date === d);
      if (!rows.length) return undefined;
      return rows.reduce((s, r) => s + Number(r.price || 0), 0) / rows.length;
    }),
  }));
  return { labels: dates, series: seriesOut };
});


function pressureClass(level) {
  if (level === "high" || level === "tight") return "danger";
  if (level === "medium") return "warning";
  if (level === "low") return "success";
  return "info";
}
const stockBars = computed(() => {
  const rows = stock.value?.by_date || [];
  return {
    categories: rows.map((r) => String(r.date).slice(5)),
    values: rows.map((r) => Number(r.remaining || 0)),
  };
});
const paceBooking = computed(() => {
  const rows = pace.value?.booking_series || [];
  return {
    labels: rows.map((r) => r.date),
    series: [{ name: "新订", values: rows.map((r) => Number(r.bookings || 0)), color: "#155dfc" }],
  };
});
const paceOtb = computed(() => {
  const rows = pace.value?.otb_vs_reference || [];
  return {
    labels: rows.map((r) => r.date),
    series: [
      { name: "在手出租率", values: rows.map((r) => Number(r.otb_occupancy || 0) * 100), color: "#155dfc" },
      { name: "上周同星期最终出租率", values: rows.map((r) => (r.reference_occupancy == null ? undefined : Number(r.reference_occupancy) * 100)), color: "#f59e0b", dashed: true },
    ],
  };
});

function deltaClass(n) {
  const v = Number(n || 0);
  if (v > 0) return "delta up";
  if (v < 0) return "delta down";
  return "muted";
}
const maxDiagnoseDate = computed(() => asOfToday.value || "");
const minDiagnoseDate = computed(() => {
  const hist = (series.value || []).filter((p) => !p.is_future).map((p) => p.date);
  return hist.length ? hist[0] : "";
});


const historyRoomBars = computed(() => {
  const rows = history.value?.by_room || [];
  return {
    categories: rows.map((r) => r.name),
    series: [
      { name: "近 7 日营收", values: rows.map((r) => Number(r.last7?.revenue || 0)), color: "#155dfc" },
      { name: "前 7 日营收", values: rows.map((r) => Number(r.prev7?.revenue || 0)), color: "#93c5fd" },
    ],
  };
});
const convSeries = computed(() => {
  const rows = conversion.value?.series || [];
  return {
    labels: rows.map((r) => r.date),
    series: [{ name: "转化率", values: rows.map((r) => Number(r.conversion_rate || 0) * 100), color: "#155dfc" }],
  };
});
const convRoomBars = computed(() => {
  const rows = conversion.value?.by_room || [];
  return {
    categories: rows.map((r) => r.name),
    values: rows.map((r) => Number(r.conversion_rate || 0) * 100),
  };
});

const supportedIds = ["demand", "stock", "pace", "history", "conv", "comp", "rooms", "channel", "radar"];
const unsupportedSelected = computed(() => dimensions.filter((d) => selected.value.includes(d.id) && !supportedIds.includes(d.id)));

const radar = computed(() => {
  const k = summary.value?.kpis || {};
  const band = summary.value?.competitor_band || {};
  const occ = Number(k.occupancy || 0);
  const conv = Number(k.conversion_rate || 0);
  const rank = Number(k.search_rank || 10);
  const our = Number(band.our_avg_list || 0);
  const mid = Number(band.competitor_median || our || 1);
  const pricePos = mid ? Math.max(0, Math.min(1, 1 - Math.abs(our - mid) / mid)) : 0.5;
  return [
    { label: "需求强度", value: Math.min(1, occ / 0.85) },
    { label: "库存压力", value: Math.min(1, occ) },
    { label: "竞品对比", value: pricePos },
    { label: "转化效率", value: Math.min(1, conv / 0.12) },
    { label: "搜索排名", value: Math.max(0, 1 - (rank - 1) / 12) },
    { label: "价格弹性", value: 0.55 },
  ];
});

async function load() {
  const [s, t, r, c, st] = await Promise.all([
    api.summary(),
    api.timeseries(),
    api.rooms(),
    api.competitors(),
    api.strategies(),
  ]);
  summary.value = s;
  series.value = t.series || [];
  stock.value = t.stock || null;
  pace.value = t.pace || null;
  history.value = t.history || null;
  conversion.value = t.conversion || null;
  rooms.value = r.rooms || [];
  comps.value = c.rows || [];
  strategies.value = st.strategies || [];
}

async function runDateDiagnosis() {
  if (!diagnoseDate.value) return;
  diagnosing.value = true;
  error.value = "";
  dateDiagnosis.value = null;
  try {
    dateDiagnosis.value = await api.diagnoseDate(diagnoseDate.value);
  } catch (e) {
    error.value = e.message;
  } finally {
    diagnosing.value = false;
  }
}
async function generate() {
  generating.value = true;
  error.value = "";
  try {
    const data = await api.generate();
    await load();
    if ((data.strategies || [])[0]) detail.value = data.strategies[0];
  } catch (e) {
    error.value = e.message;
  } finally {
    generating.value = false;
  }
}
function adopt(s) {
  router.push({ path: "/execution", query: { tab: s.kind === "campaign" ? "promos" : "pricing", sid: s.id } });
}
function exportStrategies() {
  const rows = [
    ["类型", "标题", "状态", "风险", "推荐价", "区间", "置信度"],
    ...strategies.value.map((s) => [
      s.kind,
      titleOf(s),
      s.status,
      s.risk_level,
      s.recommended_price || "",
      s.min_price ? `${s.min_price}-${s.max_price}` : "",
      s.confidence,
    ]),
  ];
  downloadText("oiro-strategies.csv", toCsv(rows), "text/csv;charset=utf-8");
}

useEscape(detail, () => { detail.value = null; });

onMounted(async () => {
  try { await load(); } catch (e) { error.value = e.message; }
});
if (shell?.refreshTick) watch(shell.refreshTick, () => load().catch((e) => { error.value = e.message; }));
</script>
