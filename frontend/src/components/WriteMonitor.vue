<template>
  <div class="card card-pad">
    <div class="section-title">
      写入监控
      <span class="muted" style="font-weight:500">{{ headerHint }}</span>
    </div>
    <p class="muted" style="margin-top:0">
      这里跟踪每次调价、活动报名和评价回复的写入账本：回执模式、平台、官方回执号、尝试次数和对账结果。默认成功路径是官方沙盒回执。酒店只需授权本店 OTA 账号和密码；官方契约和接口由运维端配置。超时和 HTTP 5xx 可重试，最多 {{ maxAttempts }} 次；缺少账号授权、尚未启用契约，以及 HTTP 4xx，不能当网络错误重试。评价回复不会自动对外发布。
    </p>
    <div v-if="jobStatus" class="muted" style="margin:0 0 10px">
      最近采集 {{ formatRunTime(jobStatus.last_ingested_at || jobStatus.last_ingest) }}<span v-if="ingestHint"> · {{ ingestHint }}</span>
      <span> · 最近写入重试 {{ formatRunTime(jobStatus.last_retry) }}</span>
      <span> · 最近对账 {{ formatRunTime(jobStatus.last_reconcile) }}</span>
      <div v-if="(jobStatus.runs || []).length" style="margin-top:4px">
        最近任务：
        <span v-for="(r, i) in (jobStatus.runs || []).slice(0, 5)" :key="r.id">
          <span v-if="i">；</span>{{ jobTypeLabel(r.job_type) }} {{ jobStatusLabel(r.status) }} {{ formatRunTime(r) }}
        </span>
      </div>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <div class="row" style="justify-content:space-between;margin-bottom:10px;flex-wrap:wrap;gap:8px">
      <div class="muted">待处理失败/部分成功 {{ openFailures }} 项 · 可重试 {{ retryableCount }} 项</div>
      <button v-if="canReconcile" class="btn btn-sm" type="button" :disabled="busy" @click="sweep">{{ busy ? "对账中…" : "全部对账" }}</button>
    </div>
    <div v-if="!jobs.length" class="empty">暂无写入任务。人审通过或低风险自动执行后会出现在这里。</div>
    <div v-for="j in jobs" :key="j.id" class="queue-item">
      <div class="q-ico" :class="icoClass(j)">写</div>
      <div class="meta">
        <h3>任务 #{{ j.id }} · {{ kindLabel(j.kind) }} · {{ platformLabel(j.platform) }}</h3>
        <p>
          {{ modeLabel(j.receipt_mode || j.write_mode) }}
          <span v-if="j.receipt_mode === 'refused' || j.refuse_reason"> · 已拒绝{{ j.refuse_reason ? "（" + j.refuse_reason + "）" : "" }}</span>
          · 尝试 {{ j.attempt_count }}/{{ j.max_attempts }}
          <span v-if="j.receipt_id"> · 官方回执 {{ j.receipt_id }}</span>
          <span v-if="j.request_id"> · 请求 {{ j.request_id }}</span>
          <span v-if="j.reconciled_at"> · 最近对账 {{ okText(j) }}</span>
        </p>
        <p v-if="j.last_error" class="muted">{{ j.last_error }}</p>
        <p v-if="issueText(j)" class="muted">对账：{{ issueText(j) }}</p>
      </div>
      <StatusBadge :value="j.status" />
      <div class="row" style="flex-shrink:0">
        <button v-if="canRetry" class="btn btn-primary btn-sm" type="button" :disabled="busy || !j.retryable" @click="retry(j)">重试</button>
        <button v-if="canReconcile" class="btn btn-sm" type="button" :disabled="busy" @click="reconcile(j)">对账</button>
      </div>
    </div>
  </div>
</template>
<script setup>
import { computed, onMounted, ref } from "vue";
import { api } from "../api/client";
import StatusBadge from "./StatusBadge.vue";
import { can } from "../utils/permissions";

const props = defineProps({
  items: { type: Array, default: null },
});
const emit = defineEmits(["refresh"]);

const localJobs = ref([]);
const jobStatus = ref(null);
const busy = ref(false);
const error = ref("");
const canRetry = computed(() => can("retry_writes"));
const canReconcile = computed(() => can("reconcile_writes"));

const jobs = computed(() => (props.items != null ? props.items : localJobs.value));
const openFailures = computed(() => jobs.value.filter((j) => ["failed", "timeout", "partial"].includes(j.status)).length);
const retryableCount = computed(() => jobs.value.filter((j) => j.retryable).length);
const maxAttempts = computed(() => (jobs.value[0] && jobs.value[0].max_attempts) || 3);

const headerHint = computed(() => {
  const modes = new Set(jobs.value.map((j) => j.receipt_mode || j.write_mode).filter(Boolean));
  if (!modes.size) return "默认官方沙盒 · 演示不会随机出站";
  if (modes.has("live") && (modes.has("sandbox_official") || modes.has("sandbox") || modes.has("refused"))) return "含真实回执与沙盒/拒绝记录";
  if (modes.has("live")) return "真实官方回执";
  if (modes.has("refused")) return "含已拒绝的 live 写入（未出站或官方 4xx）";
  return "官方沙盒回执 · 未向真实 OTA 发请求";
});
function kindLabel(kind) {
  return { pricing: "调价", campaign: "活动报名", review_reply: "评价回复" }[kind] || kind;
}
function modeLabel(mode) {
  return { sandbox_official: "官方沙盒", sandbox: "官方沙盒", live: "真实回执", refused: "已拒绝" }[mode] || mode || "—";
}
function platformLabel(platform) {
  return { ctrip: "携程", meituan: "美团", fliggy: "飞猪" }[platform] || platform || "—";
}
function icoClass(j) {
  if (j.status === "accepted" || j.status === "reconciled") return "green";
  if (j.status === "partial" || j.status === "timeout") return "purple";
  return "";
}
function okText(j) {
  if (j.reconcile_ok === true) return "一致";
  if (j.reconcile_ok === false) return "有问题";
  return "已检查";
}
function formatRunTime(value) {
  if (!value) return "暂无";
  const raw = typeof value === "string" ? value : (value.finished_at || value.started_at || "");
  if (!raw) return "暂无";
  return raw.replace("T", " ").replace("Z", "").slice(0, 16);
}
const ingestHint = computed(() => {
  const result = (jobStatus.value && jobStatus.value.last_ingest && jobStatus.value.last_ingest.result) || {};
  if (result.source === "mock") return "本地模拟刷新";
  if (result.source === "official") return "官方契约采集";
  if (result.source === "skipped") return "已跳过";
  return "";
});
function jobTypeLabel(type) {
  return { ingest: "采集", write_retry: "写入重试", write_reconcile: "写入对账" }[type] || type;
}
function jobStatusLabel(status) {
  return { succeeded: "成功", failed: "失败", running: "进行中", skipped: "跳过" }[status] || status || "";
}
function issueText(j) {
  const issues = (j.reconcile_result && (j.reconcile_result.unexpected_issues || j.reconcile_result.issues)) || [];
  if (!issues.length) return "";
  return issues.map((i) => i.message || i.code).slice(0, 2).join("；");
}

async function loadJobStatus() {
  try {
    jobStatus.value = await api.jobs();
  } catch {
    // Job status is auxiliary; write ledger should still render.
  }
}
async function refresh() {
  await loadJobStatus();
  if (props.items != null) {
    emit("refresh");
    return;
  }
  const data = await api.writes();
  localJobs.value = data.writes || [];
}
async function retry(j) {
  busy.value = true;
  error.value = "";
  try {
    await api.retryWrite(j.id);
    await refresh();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function reconcile(j) {
  busy.value = true;
  error.value = "";
  try {
    await api.reconcileWrite(j.id);
    await refresh();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function sweep() {
  busy.value = true;
  error.value = "";
  try {
    await api.reconcileWrites();
    await refresh();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

onMounted(async () => {
  try {
    if (props.items != null) await loadJobStatus();
    else await refresh();
  } catch (e) { error.value = e.message; }
});
</script>
