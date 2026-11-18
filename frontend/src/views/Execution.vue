<template>
    <div class="page-head">
      <div>
        <h1>L3 执行型助手 - 执行中心</h1>
        <p>辅助执行运营动作。默认须人工确认后写入官方沙盒回执；写入账本、对账和重试见「写入监控」。本增量不会改真实 OTA 价格，也不会对外发送回复。</p>
      </div>
      <div class="chip" style="height:auto;padding:6px 12px">待确认：{{ pendingCount }} 项</div>
    </div>
    <p v-if="error" class="error">{{ error }}</p>

    <div class="card card-pad" style="margin-bottom:16px">
      <div class="tabs">
        <button v-for="t in tabs" :key="t.id" type="button" :class="{ active: tab === t.id }" @click="setTab(t.id)">
          <Icon :name="t.icon" :size="14" /> {{ t.label }}
        </button>
      </div>

      <!-- pricing -->
      <div v-if="tab === 'pricing'">
        <div class="form-grid">
          <div class="field">
            <label>选择日期范围</label>
            <select v-model="dateKey" class="input">
              <option v-for="d in dateOptions" :key="d.key" :value="d.key">{{ d.label }}（{{ d.from }} 至 {{ d.to }}）</option>
            </select>
          </div>
          <div class="field">
            <label>选择房型</label>
            <label v-for="r in rooms" :key="r.id" class="check-row">
              <input type="checkbox" :value="r.id" v-model="pickedRooms" />
              <span>{{ r.name }}（{{ r.inventory }} 间 · 基础价 {{ yen(r.base_price) }}）</span>
            </label>
          </div>
        </div>
        <div class="field">
          <label>价格调整幅度：{{ signed(adjust, "%", 0) }}（仅预览，不作为最终执行价）</label>
          <div class="bipolar-slider">
            <div class="bipolar-track">
              <span class="bipolar-zero"></span>
              <span class="bipolar-fill" :style="sliderFillStyle"></span>
              <input type="range" min="-30" max="30" step="1" v-model.number="adjust" aria-label="价格调整幅度" />
            </div>
            <div class="bipolar-labels"><span>-30%</span><span>0%</span><span>+30%</span></div>
          </div>
        </div>
        <div class="preview-bar">
          <div><div class="lab">当前基础价（已选均价）</div><div class="val">{{ yen(baseAvg) }}</div></div>
          <div><div class="lab">调整后预览价</div><div class="val">{{ yen(previewPrice) }}</div></div>
          <div><div class="lab">影响范围</div><div class="val">{{ pickedRooms.length }} 房型</div></div>
        </div>
        <p class="muted">滑杆只帮助预估方向。提交后由系统按约束计算建议区间并进入人审，不会直接改价。房型约束见系统设置中的价格上下限。</p>
        <div class="row" style="justify-content:flex-end">
          <button class="btn" type="button" @click="adjust = 0">取消</button>
          <button class="btn btn-primary" type="button" v-if="can('generate')" :disabled="generating || !pickedRooms.length" @click="addPricing">
            {{ generating ? "生成中…" : "添加到执行队列" }}
          </button>
        </div>
      </div>

      <!-- inventory -->
      <div v-else-if="tab === 'inventory'">
        <div class="section-title">
          渠道配额调整
          <span class="phase-note">后续阶段（本轮未接通）</span>
        </div>
        <p class="muted">本轮没有真实渠道库存分配接口。以下滑杆仅为界面预览，不会写入携程或其它渠道。</p>
        <div v-for="c in channels" :key="c.name" class="field">
          <div class="row" style="justify-content:space-between">
            <b>{{ c.name }}</b>
            <span class="muted">当前 {{ c.current }}% <span v-if="c.suggest != null">· 建议 {{ c.suggest }}%</span></span>
          </div>
          <input type="range" min="0" max="50" :value="c.current" disabled />
        </div>
        <button class="btn btn-success btn-block" type="button" @click="inventoryNotice = true">应用配额调整</button>
        <div v-if="inventoryNotice" class="notice" style="margin-top:12px">未写入任何渠道。库存分配将在后续阶段接通后，再进入人审队列。</div>
      </div>

      <!-- promos -->
      <div v-else-if="tab === 'promos'">
        <p class="muted" style="margin-top:0">
          站在酒店立场评估是否跟平台活动：给出是否参加、适用范围、原因与风险。本增量不会向真实 OTA 报名，人审或自动执行后只记官方沙盒回执。
        </p>
        <div class="field" style="max-width:360px">
          <label>观察窗口</label>
          <select v-model="dateKey" class="input">
            <option v-for="d in dateOptions" :key="d.key" :value="d.key">{{ d.label }}（{{ d.from }} 至 {{ d.to }}）</option>
          </select>
        </div>
        <div class="section-title">当前可评估的渠道活动</div>
        <div class="campaign-catalog">
          <div class="subcard" v-for="p in campaigns" :key="p.id">
            <b>{{ p.name }}</b>
            <p class="muted" style="margin:6px 0 0">
              {{ p.platform }} · 佣金上浮约 {{ p.commission_uplift_pp }} 个百分点 · 活动价约 {{ p.typical_discount_pct }}%
            </p>
            <p style="margin:6px 0 0">{{ p.description }}</p>
          </div>
        </div>
        <p v-if="campaignNote" class="muted" style="margin-top:8px">{{ campaignNote }}</p>
        <div v-if="latestCampaign" class="subcard" style="margin-top:16px">
          <div class="row" style="justify-content:space-between">
            <div class="section-title" style="margin:0">最新活动参与建议</div>
            <div class="row">
              <StatusBadge :value="latestCampaign.risk_level" />
              <StatusBadge :value="latestCampaign.status" />
            </div>
          </div>
          <p class="muted">{{ latestCampaign.date_from }} 至 {{ latestCampaign.date_to }}</p>
          <CampaignAdvice :advice="latestCampaign.campaign_advice" :confidence="latestCampaign.confidence" :expected-impact="latestCampaign.expected_impact" />
        </div>
        <div class="row" style="justify-content:flex-end;margin-top:12px">
          <button class="btn btn-primary" type="button" v-if="can('generate')" :disabled="generating" @click="generateCampaign">
            {{ generating ? "生成中…" : "生成活动参与建议" }}
          </button>
        </div>
      </div>

      <!-- orders -->
      <div v-else-if="tab === 'orders'">
        <div class="notice">订单升级、延迟退房等处理为后续阶段，本轮没有真实订单接口。评价回复已单独放在「评价回复」页签，草稿与人审流程保持可用。</div>
        <div class="empty">暂无待处理订单。</div>
      </div>

      <!-- reviews -->
      <div v-else-if="tab === 'reviews'">
        <div class="detail-grid">
          <div class="list-card">
            <div
              v-for="r in reviews"
              :key="r.id"
              class="item"
              :class="{ active: currentReview?.id === r.id }"
              @click="currentReview = r; comment = ''"
            >
              <div class="row">
                <span class="tag" :class="r.rating <= 2 ? 'danger' : r.rating === 3 ? 'warning' : 'success'">{{ r.rating }} 分</span>
                <StatusBadge :value="r.reply_status" />
                <span class="muted">{{ r.review_date }}</span>
              </div>
              <h3 style="margin-top:8px">{{ r.title || "评价" }} · {{ r.guest_name }}</h3>
              <p>{{ r.content }}</p>
            </div>
            <div v-if="!reviews.length" class="empty">暂无评价。</div>
          </div>
          <div v-if="currentReview">
            <p><strong>{{ currentReview.guest_name }}</strong> · {{ currentReview.rating }} 分 · {{ currentReview.review_date }}</p>
            <p>{{ currentReview.content }}</p>
            <div class="row" style="margin: 12px 0">
              <button v-if="can('generate')" class="btn btn-primary" :disabled="busy" @click="draft">{{ busy ? "生成中…" : "生成回复草稿" }}</button>
            </div>
            <div v-if="currentReview.reply_draft">
              <div class="section-title">回复草稿</div>
              <p style="white-space:pre-wrap">{{ currentReview.reply_draft }}</p>
              <div class="field">
                <label>人审意见</label>
                <textarea v-model="comment" rows="2"></textarea>
              </div>
              <div class="row">
                <button v-if="can('approve')" class="btn btn-primary" :disabled="busy || currentReview.reply_status !== 'pending_review'" @click="reviewReply('approve')">{{ can('execute') ? "人审通过并沙盒执行" : "通过" }}</button>
                <button v-if="can('approve')" class="btn" :disabled="busy || currentReview.reply_status !== 'pending_review'" @click="reviewReply('reject')">拒绝</button>
                <button v-if="can('execute')" class="btn btn-primary" :disabled="busy || currentReview.reply_status !== 'approved'" @click="executeReply">执行</button>
                <button v-if="can('rollback')" class="btn btn-danger" :disabled="busy || !['executed','execution_failed','execution_partial'].includes(currentReview.reply_status)" @click="rollbackReply">按检查点回滚</button>
              </div>
              <p class="notice" style="margin-top:12px">系统不会对外发送回复，也不会因低风险自动回复。</p>
            </div>
            <div class="space"></div>
            <WorkflowTimeline
              :path="currentReview.path"
              :checkpoints="currentReview.checkpoints"
              :approvals="currentReview.approvals"
              :risk="currentReview.risk_level"
              :checkpoint-id="currentReview.checkpoint_id"
            />
          </div>
          <div v-else class="empty">选择一条评价。</div>
        </div>
      </div>

      <!-- autoprotect -->
      <div v-else-if="tab === 'writes'">
        <WriteMonitor :items="writes" @refresh="load" />
      </div>

      <div v-else-if="tab === 'protect'">
        <div class="notice notice-ok" style="margin-bottom:12px">
          自动保护规则可在本机预览开关，<b>本阶段不会自动关房或改价</b>。
        </div>
        <div class="subcard" v-for="p in protectItems" :key="p.key">
          <div class="row" style="justify-content:space-between">
            <div>
              <div class="row">
                <b>{{ p.title }}</b>
                <span class="tag" :class="protect[p.key] ? 'success' : 'rejected'">{{ protect[p.key] ? "已启用（预览）" : "已关闭" }}</span>
              </div>
              <p class="muted" style="margin:4px 0 0">{{ p.desc }}</p>
            </div>
            <button v-if="protect[p.key]" class="btn btn-sm" type="button" @click="toggleProtect(p.key, false)">禁用</button>
            <button v-else class="btn btn-success btn-sm" type="button" @click="toggleProtect(p.key, true)">启用</button>
          </div>
        </div>
      </div>
    </div>

    <ExecutionQueue :strategies="strategies" :reviews="reviews" @refresh="load" @open="openItem" />

    <div v-if="detail" class="modal" @click.self="detail=null">
      <div class="panel">
        <div class="row" style="justify-content:space-between">
          <h2 style="margin:0">{{ detailTitle }}</h2>
          <button class="icon-btn" type="button" aria-label="关闭" title="关闭" @click="detail=null"><Icon name="close" /></button>
        </div>
        <template v-if="detail.type === 'strategy'">
          <p class="muted">{{ detail.raw.date_from }} 至 {{ detail.raw.date_to }}</p>
          <div class="statline" v-if="detail.raw.kind === 'pricing'">
            <div>当前价<b>{{ yen(detail.raw.current_price) }}</b></div>
            <div>推荐价<b>{{ yen(detail.raw.recommended_price) }}</b></div>
            <div>区间<b>{{ yen(detail.raw.min_price) }}–{{ money(detail.raw.max_price) }}</b></div>
          </div>
          <template v-if="detail.raw.kind === 'campaign'">
            <CampaignAdvice :advice="detail.raw.campaign_advice" :confidence="detail.raw.confidence" :expected-impact="detail.raw.expected_impact" />
            <p v-if="detail.raw.rationale" style="white-space:pre-wrap;margin-top:12px">{{ detail.raw.rationale }}</p>
          </template>
          <p v-else style="white-space:pre-wrap">{{ detail.raw.rationale }}</p>
          <WorkflowTimeline :path="detail.raw.path" :checkpoints="detail.raw.checkpoints" :approvals="detail.raw.approvals" :risk="detail.raw.risk_level" :checkpoint-id="detail.raw.checkpoint_id" />
        </template>
        <template v-else>
          <p>{{ detail.raw.content }}</p>
          <p style="white-space:pre-wrap">{{ detail.raw.reply_draft }}</p>
          <WorkflowTimeline :path="detail.raw.path" :checkpoints="detail.raw.checkpoints" :approvals="detail.raw.approvals" :risk="detail.raw.risk_level" :checkpoint-id="detail.raw.checkpoint_id" />
        </template>
      </div>
    </div>
</template>

<script setup>
import { computed, inject, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import CampaignAdvice from "../components/CampaignAdvice.vue";
import ExecutionQueue from "../components/ExecutionQueue.vue";
import WriteMonitor from "../components/WriteMonitor.vue";
import StatusBadge from "../components/StatusBadge.vue";
import WorkflowTimeline from "../components/WorkflowTimeline.vue";
import Icon from "../components/Icon.vue";
import { api } from "../api/client";
import { can } from "../utils/permissions";
import { addDays, money, signed, todayISO, yen } from "../utils/format";
import { useEscape } from "../composables/useEscape";
import { loadProtect, saveProtect } from "../utils/prefs";

const tabs = [
  { id: "pricing", label: "价格与产品", icon: "dollar" },
  { id: "inventory", label: "库存分配", icon: "box" },
  { id: "promos", label: "活动参与", icon: "tag" },
  { id: "orders", label: "订单处理", icon: "mail" },
  { id: "reviews", label: "评价回复", icon: "feedback" },
  { id: "writes", label: "写入监控", icon: "link" },
  { id: "protect", label: "自动保护", icon: "shield" },
];

const route = useRoute();
const router = useRouter();
const shell = inject("appShell", null);
const tab = ref("pricing");
const rooms = ref([]);
const strategies = ref([]);
const reviews = ref([]);
const currentReview = ref(null);
const comment = ref("");
const busy = ref(false);
const generating = ref(false);
const error = ref("");
const adjust = ref(0);
const pickedRooms = ref([]);
const dateKey = ref("weekend");
const inventoryNotice = ref(false);
const detail = ref(null);
const protect = ref(loadProtect());
const writes = ref([]);

const campaigns = ref([]);
const campaignNote = ref("");
const channels = [
  { name: "携程（模拟）", current: 35, suggest: 30 },
  { name: "美团", current: 25, suggest: null },
  { name: "飞猪", current: 20, suggest: null },
  { name: "直销", current: 10, suggest: 15 },
  { name: "其他 OTA", current: 10, suggest: null },
];
const protectItems = [
  { key: "oversell", title: "超卖保护", desc: "库存不足时提示关房。本阶段只保存开关，不会自动关房。" },
  { key: "price", title: "价格保护", desc: "防止价格异常波动。本阶段不会自动改价。" },
  { key: "inventory_sync", title: "库存同步保护", desc: "同步失败时重试。本轮无真实同步任务。" },
  { key: "parity", title: "竞价保护", desc: "防止渠道价差异常。本轮仅预览。" },
];

function weekendRange() {
  const t = new Date();
  const day = t.getDay();
  const toSat = (6 - day + 7) % 7 || 7;
  const from = addDays(todayISO(), toSat === 7 && day === 6 ? 0 : toSat);
  return { from, to: addDays(from, 1) };
}
const dateOptions = computed(() => {
  const w = weekendRange();
  const t = todayISO();
  return [
    { key: "weekend", label: "本周末", from: w.from, to: w.to },
    { key: "d7", label: "未来 7 日", from: t, to: addDays(t, 6) },
    { key: "d14", label: "未来 14 日", from: t, to: addDays(t, 13) },
  ];
});
const dateRange = computed(() => dateOptions.value.find((d) => d.key === dateKey.value) || dateOptions.value[0]);
const roomsReady = ref(false);
const selectedRooms = computed(() => rooms.value.filter((r) => pickedRooms.value.includes(r.id)));
const sliderFillStyle = computed(() => {
  const t = Number(adjust.value) || 0;
  const half = Math.abs(t) / 30 * 50;
  if (t >= 0) return { left: "50%", width: half + "%" };
  return { left: (50 - half) + "%", width: half + "%" };
});
const baseAvg = computed(() => {
  const list = selectedRooms.value;
  if (!list.length) return 0;
  return list.reduce((s, r) => s + Number(r.base_price || 0), 0) / list.length;
});
const previewPrice = computed(() => Math.round(baseAvg.value * (1 + adjust.value / 100)));
const pendingCount = computed(() => {
  return strategies.value.filter((s) => s.status === "pending_review").length
    + reviews.value.filter((r) => r.reply_status === "pending_review").length;
});
const detailTitle = computed(() => {
  if (!detail.value) return "";
  if (detail.value.type === "review") return "评价回复详情";
  const s = detail.value.raw;
  return s.kind === "campaign" ? "活动参与建议" : (s.room_name || "房型") + " 定价建议";
});

function setTab(id) {
  tab.value = id;
  router.replace({ path: "/execution", query: { ...route.query, tab: id } });
}
function toggleProtect(key, on) {
  protect.value = saveProtect({ ...protect.value, [key]: on });
}

async function load() {
  const [rm, st, rv, cp, wr] = await Promise.all([api.rooms(), api.strategies(), api.reviews(), api.campaigns(), api.writes()]);
  rooms.value = rm.rooms || [];
  campaigns.value = cp.campaigns || [];
  campaignNote.value = cp.note || "";
  if (!roomsReady.value && rooms.value.length) {
    pickedRooms.value = rooms.value.map((r) => r.id);
    roomsReady.value = true;
  }
  strategies.value = st.strategies || [];
  reviews.value = rv.reviews || [];
  writes.value = wr.writes || [];
  if (currentReview.value) {
    currentReview.value = reviews.value.find((r) => r.id === currentReview.value.id) || reviews.value[0] || null;
  }
  if (route.query.sid) {
    const s = strategies.value.find((x) => String(x.id) === String(route.query.sid));
    if (s) detail.value = { type: "strategy", raw: s, id: s.id };
  }
}
async function addPricing() {
  if (!pickedRooms.value.length) return;
  generating.value = true;
  error.value = "";
  try {
    const range = dateRange.value;
    const ids = pickedRooms.value;
    if (ids.length === 1) {
      await api.generate({
        room_type_id: ids[0] || undefined,
        date_from: range.from,
        date_to: range.to,
        kind: "pricing",
      });
    } else {
      for (const id of ids) {
        await api.generate({ room_type_id: id, date_from: range.from, date_to: range.to, kind: "pricing" });
      }
    }
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    generating.value = false;
  }
}
async function draft() {
  busy.value = true;
  error.value = "";
  try {
    currentReview.value = await api.draftReply(currentReview.value.id);
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function reviewReply(decision) {
  busy.value = true;
  error.value = "";
  try {
    currentReview.value = await api.reviewReply(currentReview.value.id, decision, comment.value);
    comment.value = "";
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function executeReply() {
  if (!currentReview.value) return;
  busy.value = true;
  error.value = "";
  try {
    currentReview.value = await api.executeReply(currentReview.value.id);
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function rollbackReply() {
  busy.value = true;
  error.value = "";
  try {
    currentReview.value = await api.rollbackReply(currentReview.value.id, comment.value || "撤销模拟回复");
    comment.value = "";
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function generateCampaign() {
  generating.value = true;
  error.value = "";
  try {
    const range = dateRange.value;
    await api.generate({ date_from: range.from, date_to: range.to, kind: "campaign" });
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    generating.value = false;
  }
}
function openItem(it) {
  detail.value = it;
  if (it.type === "review") {
    tab.value = "reviews";
    currentReview.value = it.raw;
  }
}

useEscape(detail, () => { detail.value = null; });

onMounted(async () => {
  const allowed = tabs.map((t) => t.id);
  if (allowed.includes(route.query.tab)) tab.value = route.query.tab;
  try { await load(); } catch (e) { error.value = e.message; }
  if (!reviews.value.length) return;
  if (!currentReview.value) currentReview.value = reviews.value[0];
});
watch(() => route.query.tab, (v) => { if (tabs.some((t) => t.id === v)) tab.value = v; });
if (shell?.refreshTick) watch(shell.refreshTick, () => load().catch((e) => { error.value = e.message; }));
</script>
