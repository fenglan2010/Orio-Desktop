<template>
  <section v-if="path?.length || approvals?.length" class="decision-trace">
    <div class="row" style="justify-content:space-between;align-items:center;gap:8px">
      <div class="section-title" style="margin:0">决策路径追溯</div>
      <div class="row" style="gap:8px">
        <StatusBadge v-if="risk" :value="risk" />
        <span v-if="checkpointId" class="muted" :title="checkpointId">当前检查点 {{ shortId(checkpointId) }}</span>
      </div>
    </div>
    <p class="muted" style="margin:6px 0 12px">
      可回看本条策略经过的节点、检查点与审批记录。本阶段仅建议，不会自动调价或写入 OTA。
    </p>

    <ol class="timeline">
      <li v-for="(step, i) in path" :key="`${step.node}-${i}`" class="t-step">
        <div class="t-rail"><span class="t-dot" :class="dotClass(step)"></span></div>
        <div class="t-body">
          <div class="name">{{ step.label || labels[step.node] || step.node }}</div>
          <div class="meta">
            <span>{{ statusText(step) }}</span>
            <span v-if="step.finished_at || step.started_at"> · {{ formatDate(step.finished_at || step.started_at) }}</span>
            <span v-if="step.checkpoint_id" :title="step.checkpoint_id"> · 检查点 {{ shortId(step.checkpoint_id) }}</span>
          </div>
          <p v-if="step.summary" class="t-summary">{{ step.summary }}</p>
          <p v-if="step.error" class="t-summary error">{{ step.error }}</p>
        </div>
      </li>
    </ol>

    <div class="section-title" style="margin-top:16px">审批记录</div>
    <ul v-if="approvals?.length" class="approval-list">
      <li v-for="(a, i) in approvals" :key="i">
        <div class="name">{{ a.action_label || a.action }} · {{ a.actor }}</div>
        <div class="meta">
          {{ formatDate(a.at) }}
          <span v-if="a.risk_level"> · 风险{{ riskText(a.risk_level) }}</span>
          <span v-if="a.checkpoint_id" :title="a.checkpoint_id"> · 检查点 {{ shortId(a.checkpoint_id) }}</span>
        </div>
        <p v-if="a.reason" class="t-summary">{{ a.reason }}</p>
      </li>
    </ul>
    <p v-else class="muted">尚未审批。策略生成后必须经人工确认，才会进入模拟执行。</p>

    <details v-if="checkpoints?.length" class="ck-details">
      <summary>全部检查点（{{ checkpoints.length }}）</summary>
      <ul class="approval-list" style="margin-top:8px">
        <li v-for="c in checkpoints" :key="c.checkpoint_id">
          <div class="name">{{ c.kind_label || c.kind }} · {{ c.node_label || c.node_name }}</div>
          <div class="meta">{{ formatDate(c.created_at) }} · {{ c.checkpoint_id }}</div>
        </li>
      </ul>
    </details>
  </section>
</template>

<script setup>
import StatusBadge from "./StatusBadge.vue";
import { formatDate } from "../utils/format";

const props = defineProps({
  path: { type: Array, default: () => [] },
  checkpoints: { type: Array, default: () => [] },
  approvals: { type: Array, default: () => [] },
  risk: { type: String, default: "" },
  checkpointId: { type: String, default: "" },
});

const labels = {
  data_node: "数据采集",
  signal_node: "信号识别",
  analysis_node: "因果分析",
  strategy_node: "策略生成",
  human_review: "人工审核",
  execution_node: "模拟执行",
  rollback: "检查点回滚",
  memory_node: "经验沉淀",
};

function shortId(id) {
  return String(id || "").slice(0, 8);
}

function riskText(level) {
  return ({ low: "低", medium: "中", high: "高" })[level] || level;
}

function statusText(step) {
  if (step.status === "pending") return "等待审核";
  if (step.status === "error") return "失败";
  if (step.node === "rollback") return "已回滚";
  return "已完成";
}

function dotClass(step) {
  if (step.status === "pending") return "pending";
  if (step.status === "error") return "error";
  if (step.node === "rollback") return "rollback";
  return "ok";
}
</script>
