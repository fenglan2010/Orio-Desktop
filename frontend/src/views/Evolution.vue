<template>
    <div class="page-head">
      <div>
        <h1>L5 本店经验沉淀</h1>
        <p>{{ engine.enabled ? engineOnIntro : engineOffIntro }}</p>
      </div>
      <div class="page-actions">
        <span class="chip">进化引擎
          <button
            class="toggle"
            type="button"
            :class="{ on: engine.enabled }"
            :disabled="engineBusy || !can('update_policy')"
            :title="engineToggleTitle"
            @click="toggleEngine(!engine.enabled)"
          ><i></i></button>
        </span>
        <button
          class="btn btn-purple"
          type="button"
          :disabled="engineBusy || !engine.enabled || !can('update_policy')"
          :title="engine.enabled ? (can('update_policy') ? (engine.sandbox_mode ? '退出沙盒评估' : '仅在沙盒中评估候选策略') : '仅酒店管理员可切换沙盒模式') : '请先开启进化引擎'"
          @click="toggleSandbox(!engine.sandbox_mode)"
        >{{ engine.sandbox_mode ? "退出沙盒模式" : "进入沙盒模式" }}</button>
      </div>
    </div>
    <p v-if="actionError" class="error">{{ actionError }}</p>
    <p v-if="actionNotice" class="notice notice-ok">{{ actionNotice }}</p>

    <div class="card card-pad" style="background:var(--purple-bg);border-color:#e9d5ff;margin-bottom:16px">
      <div class="row" style="gap:12px;align-items:flex-start">
        <div class="q-ico purple"><Icon name="spark" /></div>
        <div style="flex:1">
          <b>{{ engine.enabled ? "进化引擎已接通（本店离线学习与建议）" : "本店经验沉淀、知识图谱与匿名跨店学习" }}</b>
          <p class="muted" style="margin:4px 0 0">{{ engine.enabled ? engineOnBody : engineOffBody }}</p>
        </div>
      </div>
      <div class="grid-4" style="margin-top:14px">
        <div class="subcard" style="background:var(--surface)"><div class="muted">记录范围</div><b>本店经验仅当前酒店</b></div>
        <div class="subcard" style="background:var(--surface)"><div class="muted">跨店学习</div><b>匿名模式引用</b></div>
        <div class="subcard" style="background:var(--surface)"><div class="muted">人审节点</div><b>必须保留</b></div>
        <div class="subcard" style="background:var(--surface)"><div class="muted">自动调价</div><b>未启用</b></div>
      </div>
    </div>

    <div class="grid-2">
      <div class="card card-pad">
        <div class="section-title">
          近 14 日实际转化率
          <span class="tag success">当前酒店真实指标</span>
        </div>
        <LineChart :series="[{ name: '转化率', values: convValues, color: '#8200db', area: true }]" :labels="labels" :area="true" :height="220" />
        <div class="notice notice-purple" style="margin-top:12px">曲线使用本店近 14 日实际转化率，用于观察走弱，不是模型准确率，也不代表这次调价带来了多少收入。</div>
      </div>
      <div class="card card-pad">
        <div class="section-title">能力代次</div>
        <div class="tree-item" v-for="g in gens" :key="g.id" :class="{ active: g.active }">
          <div class="gen">{{ g.id }}</div>
          <div class="grow">
            <b>{{ g.title }}</b>
            <div class="muted">{{ g.desc }}</div>
          </div>
          <span class="tag" :class="g.active ? 'info' : 'rejected'">{{ g.active ? "当前产品" : "规划中" }}</span>
        </div>
        <button
          class="btn btn-purple btn-block"
          type="button"
          :disabled="engineBusy || !engine.enabled || !can('generate')"
          :title="inventTitle"
          @click="inventStrategies"
        >{{ inventButtonLabel }}</button>
      </div>
    </div>

    <div class="space"></div>
    <div class="grid-2">
      <div class="card card-pad">
        <div class="section-title">
          本店知识图谱
          <span class="phase-note">仅当前酒店已有事实</span>
        </div>
        <p class="muted" style="margin:0 0 12px">{{ graph.summary || graph.note || "正在整理本店决策与信号之间的关系。" }}</p>
        <p v-if="graphError" class="muted">{{ graphError }}</p>
        <div v-else-if="!(graph.edges || []).length" class="muted">暂无图谱关系。完成人审并沉淀决策后，会在这里看到房型、信号与结果之间的联系。</div>
        <div v-else>
          <div class="chip-grid" style="margin-bottom:12px">
            <span class="chip" v-for="n in graph.nodes.slice(0, 24)" :key="n.key">{{ n.label }}</span>
          </div>
          <div class="queue-item" v-for="(rel, idx) in graphRelations" :key="idx">
            <div class="meta">
              <h3>{{ rel.from }}</h3>
              <p class="muted">{{ rel.edge }} → {{ rel.to }}</p>
            </div>
          </div>
        </div>
      </div>
      <div class="card card-pad">
        <div class="section-title">
          本店离线学习
          <span class="phase-note">观察奖励，不是因果收入</span>
        </div>
        <p class="muted" style="margin:0 0 12px">{{ rl.note || "用本店已复盘记录做表格学习，样本不足时不会编造策略。" }}</p>
        <p v-if="rlError" class="muted">{{ rlError }}</p>
        <div v-else-if="rl.insufficient" class="notice">{{ rl.insufficient_reason }}</div>
        <div v-else-if="!(rl.suggestions || []).length" class="muted">暂无足够的分情境建议。已拟合的观察值会在积累更多复盘后出现。</div>
        <div class="queue-item" v-for="s in (rl.suggestions || [])" :key="s.context_key + s.action_bucket">
          <div class="meta">
            <h3>{{ s.action_label }} · 观察奖励 {{ formatReward(s) }}</h3>
            <p>{{ suggestionDetail(s) }}</p>
            <p class="muted">{{ s.note }}</p>
          </div>
        </div>
        <div v-if="candidates.length" style="margin-top:16px">
          <div class="section-title">引擎候选策略</div>
          <div class="queue-item" v-for="c in candidates" :key="c.id">
            <div class="meta">
              <h3>{{ c.title }}</h3>
              <p>{{ c.rationale }}</p>
              <p class="muted" v-if="c.strategy_id">已进入人审队列（策略 #{{ c.strategy_id }}），不会自动执行。</p>
              <p class="muted" v-if="c.sandbox_evaluation">沙盒预览：观察奖励 {{ formatReward(c.sandbox_evaluation) }}，未做线上写入。</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="space"></div>
    <div class="card card-pad">
      <div class="section-title">
        本店经验记录
        <span class="phase-note">对照决策时基线观察，不是因果收入</span>
      </div>
      <p v-if="loadError" class="muted">{{ loadError }}</p>
      <div v-else-if="!memories.length" class="muted" style="padding:8px 0 4px">
        暂无已沉淀的本店决策。生成策略并完成人审（通过后执行，或否决）后，会在这里留下记录。已执行、否决、回滚或执行失败的调价，都可在入住日后对照基线观察。
      </div>
      <div class="queue-item" v-for="e in memories" :key="e.id">
        <div class="meta">
          <h3>{{ formatStamp(e.created_at) }} · {{ e.action_kind_label || "决策" }} · {{ e.status_label || e.approval_status }}</h3>
          <p>{{ e.summary }}</p>
          <p class="muted" v-if="detailLine(e)">{{ detailLine(e) }}</p>
          <div v-if="hasObservedImpact(e)" class="impact-review">
            <b>{{ impactTitle(e) }}</b>
            <p class="muted">{{ (e.impact && e.impact.label) || "相对决策时基线的观察变化，不是因果收入。" }}</p>
            <div v-if="e.impact && e.impact.realized" class="subcard" style="margin-top:8px">
              <div class="muted">已实现入住日（{{ e.impact.realized.date_from }} 至 {{ e.impact.realized.date_to }}）</div>
              <p>{{ sliceLine(e.impact.realized, { revenue: true }) }}</p>
            </div>
            <div v-if="e.impact && e.impact.otb_pickup" class="subcard" style="margin-top:8px">
              <div class="muted">在手订单变化 / OTB pickup（不是已实现客房收入，也不是预测）</div>
              <p>{{ sliceLine(e.impact.otb_pickup, { revenue: false }) }}</p>
            </div>
            <p v-if="scenarioLine(e)" class="muted" style="margin-top:8px">{{ scenarioLine(e) }}</p>
          </div>
          <p v-else class="impact">{{ unmeasuredText(e) }}</p>
        </div>
      </div>
    </div>

    <div class="space"></div>
    <div class="card card-pad">
      <div class="section-title">
        匿名行业模式 / 跨店学习
        <span class="phase-note">匿名模式引用，不复制其他酒店的在用策略</span>
      </div>
      <p class="muted" style="margin:0 0 12px">跨店学习已开始，但是匿名模式，不展示其他酒店名称，不会训练模型，不会自动改价。只有至少两家酒店贡献了相同的匿名特征时，才会在这里展示。</p>
      <p v-if="patternError" class="muted">{{ patternError }}</p>
      <div v-else-if="!patterns.length" class="muted" style="padding:8px 0 4px">
        暂无可见的匿名行业模式。本店经验仍只属于当前酒店；不会出现其他酒店的名称或身份信息。
      </div>
      <div class="queue-item" v-for="p in patterns" :key="p.pattern_key">
        <div class="meta">
          <h3>{{ p.action_kind_label || "决策" }} · {{ p.status_label || p.terminal_status }}<template v-if="p.price_direction"> · 价格方向：{{ p.price_direction }}</template></h3>
          <p>{{ p.summary }}</p>
          <p class="muted" v-if="patternDetail(p)">{{ patternDetail(p) }}</p>
          <p class="impact">结果影响：尚未测算</p>
        </div>
      </div>
    </div>

    <div class="space"></div>
    <div class="grid-3">
      <div class="card card-pad">
        <div class="section-title">跨系统迁移</div>
        <div class="subcard" style="background:var(--purple-soft)">
          <div class="muted">源酒店</div>
          <b>{{ shellState.me.hotel_name || "当前酒店" }}</b>
        </div>
        <div class="muted" style="text-align:center;margin:6px 0">一键迁移不开放，以免带出其他酒店身份</div>
        <div class="subcard">
          <div class="muted">目标酒店</div>
          <b>—</b>
        </div>
        <button class="btn btn-purple btn-block" type="button" disabled>一键迁移策略</button>
        <p class="muted" style="margin-top:8px">一键迁移不开放，以免带出其他酒店身份。跨店学习只提供匿名模式引用，不会复制其他酒店的在用策略。</p>
      </div>
      <div class="card card-pad">
        <div class="section-title">伦理与风险自检</div>
        <div class="row" style="justify-content:space-between;margin:8px 0"><span>价格公平性</span><span class="tag success">约束展示中</span></div>
        <div class="row" style="justify-content:space-between;margin:8px 0"><span>客人隐私保护</span><span class="tag success">不展示密钥</span></div>
        <div class="row" style="justify-content:space-between;margin:8px 0"><span>自动调价</span><span class="tag warning">未启用</span></div>
        <p class="muted">自检底线：调价与回复必须人审；本店经验、匿名模式和进化引擎都不会跳过人审，也不会自动执行。</p>
      </div>
      <div class="card card-pad">
        <div class="section-title">长期价值</div>
        <div v-for="r in longs" :key="r.label" style="margin-bottom:12px">
          <div class="row" style="justify-content:space-between"><span>{{ r.label }}</span><b>{{ r.val }}</b></div>
          <div class="bar-track"><span :style="{ width: r.pct + '%', background: r.color }"></span></div>
        </div>
        <p class="muted">说明性目标：短期收益与长期口碑并重。数值为示意，不是测算结果。</p>
      </div>
    </div>
</template>

<script setup>
import { computed, inject, onMounted, ref, watch } from "vue";
import LineChart from "../components/LineChart.vue";
import Icon from "../components/Icon.vue";
import { api } from "../api/client";
import { shellState } from "../utils/shellState";
import { can } from "../utils/permissions";
import { formatDate, pct, signed, yen } from "../utils/format";

const series = ref([]);
const memories = ref([]);
const patterns = ref([]);
const graph = ref({ nodes: [], edges: [], summary: "", note: "" });
const rl = ref({ suggestions: [], insufficient: false, note: "" });
const candidates = ref([]);
const engine = ref({ enabled: false, sandbox_mode: false });
const loadError = ref("");
const patternError = ref("");
const graphError = ref("");
const rlError = ref("");
const actionError = ref("");
const actionNotice = ref("");
const engineBusy = ref(false);
const shell = inject("appShell", null);
const labels = computed(() => series.value.filter((p) => !p.is_future).map((p) => p.date));
const convValues = computed(() => series.value.filter((p) => !p.is_future).map((p) => Number(p.conversion_rate || 0) * 100));

const engineOffIntro = "本页展示当前酒店已执行、已否决、已回滚或执行失败后的决策摘要，以及本店知识图谱与离线学习。跨店学习已开始，但是匿名模式，不展示其他酒店名称。进化引擎默认关闭，不会自动改价。";
const engineOnIntro = "进化引擎已接通，但只在本店经验上做离线学习与建议，不会训练跨店可识别模型，不会跳过人审，不会自动改价，也不会改为线上写入。";
const engineOffBody = "人审通过并执行，或人审否决、回滚、执行失败后，会在本店留下一条可复查的决策记录。这些记录会对照决策时基线做观察复盘；观察变化不是因果收入，也不是上年同期。知识图谱和离线学习只使用本店事实。跨店学习是匿名模式，不会训练模型，不会自动改价，也不会跳过人审。";
const engineOnBody = "进化引擎已接通，但只在本店经验上做离线学习与建议，不会训练跨店可识别模型，不会跳过人审，不会自动改价，也不会改为线上写入。发明的候选策略仍须进入人审。一键迁移策略仍不开放，以免带出其他酒店身份。";

const gens = [
  { id: "G1", title: "数据观察", desc: "看板、预警与竞对价格带", active: true },
  { id: "G2", title: "可解释策略", desc: "启发式定价 + 人审", active: true },
  { id: "G3", title: "本店经验沉淀", desc: "记录本店已执行、已否决、已回滚或执行失败的决策", active: true },
  { id: "G4", title: "跨酒店学习", desc: "匿名模式引用，不迁移策略，不训练跨店可识别模型", active: true },
  { id: "G5", title: "决策后观察", desc: "对照决策时基线观察变化，含否决、回滚与失败后的路径，不是因果收入", active: true },
  { id: "G6", title: "本店进化引擎", desc: "本店知识图谱与离线学习，只做建议，默认关闭，不跳过人审，不自动改价", active: true },
];
const longs = [
  { label: "口碑与回复质量", val: "人审保护", pct: 80, color: "#8200db" },
  { label: "价格纪律", val: "上下限约束", pct: 70, color: "#155dfc" },
  { label: "市场定位", val: "按当前登录酒店", pct: 60, color: "#22c55e" },
];

const engineToggleTitle = computed(() => {
  if (!can("update_policy")) return "仅酒店管理员可开关进化引擎";
  return engine.value.enabled ? "关闭进化引擎" : "开启进化引擎（只做本店离线学习与建议）";
});
const inventButtonLabel = computed(() => {
  if (!engine.value.enabled) return "发明新策略（引擎未开启）";
  return "发明新策略";
});
const inventTitle = computed(() => {
  if (!engine.value.enabled) return "请先开启进化引擎";
  if (!can("generate")) return "当前角色不能生成策略";
  return "根据本店离线学习生成候选，并进入人审，不会自动执行";
});

const EDGE_LABELS = {
  triggered_by: "由信号触发",
  for_room_type: "针对房型",
  has_status: "终态",
  has_action: "动作类型",
  observed_outcome: "观察结果",
  similar_to: "相似于匿名模式",
};

const graphRelations = computed(() => {
  const labelsByKey = Object.fromEntries((graph.value.nodes || []).map((n) => [n.key, n.label]));
  return (graph.value.edges || []).slice(0, 30).map((e) => ({
    from: labelsByKey[e.from] || e.from,
    to: labelsByKey[e.to] || e.to,
    edge: EDGE_LABELS[e.edge_type] || e.edge_type,
  }));
});

function formatStamp(value) {
  return formatDate(value) || "—";
}

function impactTitle(e) {
  const status = e?.approval_status || e?.impact?.terminal_status || "";
  if (status === "rejected") return "否决后实际发生了什么";
  if (status === "rolled_back") return "回滚后实际发生了什么";
  if (status === "execution_failed") return "执行失败后实际发生了什么";
  if (e.impact?.title) return e.impact.title;
  if (e.action_kind === "campaign") return "这次活动实际带来了什么";
  return "这次调价实际带来了什么";
}

function hasObservedImpact(e) {
  const outcome = e?.outcome;
  const impact = e?.impact;
  if (outcome !== "measured" && outcome !== "partially_measured") return false;
  if (!impact || typeof impact !== "object") return false;
  return Boolean(impact.realized || impact.otb_pickup);
}

function unmeasuredText(e) {
  if (e?.action_kind === "review_reply") {
    return "评价回复不按调价入住窗口自动复盘。";
  }
  const title = impactTitle(e);
  return `${title}：尚未复盘。等待入住日实现或后续在手订单变化。`;
}

function sliceLine(slice, { revenue } = { revenue: true }) {
  if (!slice) return "";
  const cur = slice.current_kpis || {};
  const delta = slice.delta || {};
  const bits = [];
  if (revenue && cur.revenue != null) {
    bits.push(`客房收入 ${yen(cur.revenue)}，相对基线 ${signed(delta.revenue_abs || 0, " 元", 0)}（${signed(delta.revenue_pct || 0, "%")}）`);
  }
  if (cur.occupancy != null) {
    bits.push(`出租率 ${pct(cur.occupancy)}，相对基线 ${signed(delta.occupancy_pp || 0, " 个百分点")}`);
  }
  if (cur.sold_rooms != null) {
    bits.push(`已售/在手 ${cur.sold_rooms} 间夜，相对基线 ${signed(delta.sold_rooms_abs || 0, " 间夜", 0)}`);
  }
  if (cur.adr != null && revenue) {
    bits.push(`ADR ${yen(cur.adr, 1)}，相对基线 ${signed(delta.adr_abs || 0, " 元", 1)}`);
  }
  if (cur.orders != null && !revenue) {
    bits.push(`在手订单 ${cur.orders} 单，相对基线 ${signed(delta.orders_abs || 0, " 单", 0)}`);
  }
  return bits.join("；") || (slice.label || "");
}

function scenarioLine(e) {
  const cmp = e?.impact?.scenario_comparison;
  if (!cmp || !cmp.recorded_scenario) return "";
  const rec = cmp.recorded_scenario;
  const obs = cmp.observed || {};
  const bits = ["建议情景对照（不是评分，也不是承诺兑现）"];
  if (cmp.compared_slice) bits.push(`切片：${cmp.compared_slice}`);
  if (rec.occupancy_delta_pct != null && obs.occupancy_delta_pct != null) {
    bits.push(`出租率情景 ${signed(rec.occupancy_delta_pct, " 个百分点")}，观察 ${signed(obs.occupancy_delta_pct, " 个百分点")}`);
  }
  if (rec.revenue_delta != null && obs.revenue_delta != null) {
    bits.push(`客房收入情景 ${signed(rec.revenue_delta, " 元", 0)}，观察 ${signed(obs.revenue_delta, " 元", 0)}`);
  }
  return bits.join(" · ");
}

function detailLine(e) {
  const bits = [];
  if (e.room_name) bits.push(e.room_name);
  if (e.date_from && e.date_to) bits.push(`${e.date_from} 至 ${e.date_to}`);
  else if (e.date_from) bits.push(e.date_from);
  if (e.risk_level) bits.push(`风险${({ low: "低", medium: "中", high: "高" })[e.risk_level] || e.risk_level}`);
  if (e.actor) bits.push(e.actor);
  return bits.join(" · ");
}

function patternDetail(p) {
  const bits = [];
  const signalLabels = {
    rank_drop: "排名下跌",
    price_inversion: "价格倒挂",
    conversion_drop: "转化走弱",
    competitor_cut: "竞对降价",
    negative_review: "差评信号",
  };
  const signals = (p.signal_types || []).map((s) => signalLabels[s] || s);
  if (signals.length) bits.push(signals.join("、"));
  if (p.risk_level) bits.push(`风险${({ low: "低", medium: "中", high: "高" })[p.risk_level] || p.risk_level}`);
  if (p.positioning_bucket) bits.push(`${p.positioning_bucket}定位`);
  if (p.peer_hotel_count) bits.push(`来自其他 ${p.peer_hotel_count} 家同类酒店的匿名记录`);
  if (p.peer_episode_count) bits.push(`${p.peer_episode_count} 条同模式片段`);
  return bits.join(" · ");
}

function formatReward(item) {
  if (!item) return "—";
  const value = item.mean_observed_reward ?? item.mean_reward;
  if (value == null) return "—";
  const unit = item.reward_unit_label || (item.reward_unit === "sold_rooms" ? "间夜" : "元");
  return `${value} ${unit}`;
}

function suggestionDetail(s) {
  const bits = [`支持片段 ${s.episode_count} 条`];
  const signals = (s.context && s.context.signal_types) || [];
  if (signals.length) bits.push(`信号：${signals.join("、")}`);
  if (s.context?.risk_level) bits.push(`风险 ${s.context.risk_level}`);
  return bits.join(" · ");
}

async function load() {
  loadError.value = "";
  patternError.value = "";
  graphError.value = "";
  rlError.value = "";
  const t = await api.timeseries();
  series.value = t.series || [];
  try {
    const body = await api.memories("limit=50");
    memories.value = body.memories || [];
  } catch (err) {
    memories.value = [];
    loadError.value = err?.message || "无法加载本店经验记录";
  }
  try {
    const body = await api.industryPatterns("limit=50");
    patterns.value = body.patterns || [];
  } catch (err) {
    patterns.value = [];
    patternError.value = err?.message || "无法加载匿名行业模式";
  }
  try {
    const overview = await api.evolution();
    engine.value = overview.engine || { enabled: false, sandbox_mode: false };
    candidates.value = overview.candidates || [];
    rl.value = overview.rl || { suggestions: [], insufficient: false };
  } catch (err) {
    engine.value = { enabled: false, sandbox_mode: false };
    rlError.value = err?.message || "无法加载进化引擎状态";
  }
  try {
    graph.value = await api.evolutionGraph();
  } catch (err) {
    graph.value = { nodes: [], edges: [], summary: "" };
    graphError.value = err?.message || "无法加载本店知识图谱";
  }
}

async function toggleEngine(enabled) {
  actionError.value = "";
  actionNotice.value = "";
  engineBusy.value = true;
  try {
    engine.value = await api.toggleEvolutionEngine(enabled);
    actionNotice.value = engine.value.enabled
      ? "进化引擎已接通，但只在本店经验上做离线学习与建议，不会跳过人审，也不会自动改价。"
      : "进化引擎已关闭。本店经验与图谱仍可查看。";
    await load();
  } catch (err) {
    actionError.value = err?.message || "无法切换进化引擎";
  } finally {
    engineBusy.value = false;
  }
}

async function toggleSandbox(active) {
  actionError.value = "";
  actionNotice.value = "";
  engineBusy.value = true;
  try {
    const body = await api.toggleEvolutionSandbox(active);
    engine.value = body.engine || engine.value;
    candidates.value = body.candidates || candidates.value;
    actionNotice.value = engine.value.sandbox_mode
      ? "已进入沙盒模式：只做对照预览，未改变写入模式，也未打开自动执行。"
      : "已退出沙盒模式。酒店写入模式与自动执行设置保持不变。";
  } catch (err) {
    actionError.value = err?.message || "无法切换沙盒模式";
  } finally {
    engineBusy.value = false;
  }
}

async function inventStrategies() {
  actionError.value = "";
  actionNotice.value = "";
  engineBusy.value = true;
  try {
    const body = await api.inventEvolutionStrategies();
    candidates.value = body.candidates || [];
    if (body.insufficient) {
      actionNotice.value = body.insufficient_reason || "样本不足，不会编造策略。";
    } else {
      actionNotice.value = body.created
        ? `已生成 ${body.created} 条候选。涨价、降价或持平建议已进入人审，不会自动执行。`
        : "没有新的候选（可能已存在相同建议）。";
    }
    await load();
  } catch (err) {
    actionError.value = err?.message || "无法发明新策略";
  } finally {
    engineBusy.value = false;
  }
}

onMounted(() => load().catch(() => {}));
if (shell?.refreshTick) watch(shell.refreshTick, () => load().catch(() => {}));
</script>
