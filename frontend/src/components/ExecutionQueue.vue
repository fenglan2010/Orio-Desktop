<template>
  <div class="card card-pad">
    <div class="section-title">
      批量执行队列
      <span class="muted" style="font-weight:500">待人审 {{ pendingCount }} 项</span>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="!items.length" class="empty">暂无待处理项。请先在「多维分析」或本页生成策略，或为评价生成回复草稿。</div>
    <div v-for="it in items" :key="it.key" class="queue-item">
      <div class="q-ico" :class="it.ico">{{ it.mark }}</div>
      <div class="meta">
        <h3>{{ it.title }}</h3>
        <p>{{ it.desc }}</p>
      </div>
      <span class="pend" v-if="it.status === 'pending_review'">待确认</span>
      <StatusBadge v-else :value="it.badge || it.status" />
      <div class="row" style="flex-shrink:0">
        <button v-if="canApprove" class="btn btn-primary btn-sm" type="button" :disabled="busy || it.status !== 'pending_review'" @click="act(it, 'approve')">{{ canExecute ? "确认执行" : "通过" }}</button>
        <button v-if="canApprove" class="btn btn-sm" type="button" :disabled="busy || it.status !== 'pending_review'" @click="act(it, 'reject')">取消</button>
        <button v-if="canExecute" class="btn btn-primary btn-sm" type="button" :disabled="busy || it.status !== 'approved'" @click="act(it, 'execute')">执行</button>
        <button v-if="canRollback" class="btn btn-danger btn-sm" type="button" :disabled="busy || !['executed','execution_failed','execution_partial'].includes(it.status)" @click="act(it, 'rollback')">回滚</button>
        <button class="btn btn-ghost btn-sm" type="button" @click="$emit('open', it)">详情</button>
      </div>
    </div>
    <div class="row" style="justify-content:space-between;margin-top:8px;align-items:center">
      <p class="muted" style="margin:0;font-size:12px">{{ canExecute ? "确认执行即人审通过并写入官方沙盒回执，不会改真实 OTA 价格或对外发送回复。已自动执行与人审后执行都会留下检查点，可回滚。" : "通过后进入已审批队列，不会立即执行写入。具备执行权限的账号随后可以执行或回滚。" }}</p>
      <button v-if="canApprove" class="btn btn-primary" type="button" :disabled="busy || !pendingCount" @click="approveAll">{{ canExecute ? "批量确认全部" : "批量通过全部" }}</button>
    </div>
  </div>
</template>
<script setup>
import { computed, ref } from "vue";
import { api } from "../api/client";
import { yen } from "../utils/format";
import { can } from "../utils/permissions";
import StatusBadge from "./StatusBadge.vue";

const props = defineProps({
  strategies: { type: Array, default: () => [] },
  reviews: { type: Array, default: () => [] },
});
const emit = defineEmits(["refresh", "open"]);
const busy = ref(false);
const error = ref("");
const canApprove = computed(() => can("approve"));
const canExecute = computed(() => can("execute"));
const canRollback = computed(() => can("rollback"));

const items = computed(() => {
  const list = [];
  for (const s of props.strategies) {
    if (!["pending_review", "approved", "executed", "execution_failed", "execution_partial"].includes(s.status)) continue;
    const title = s.kind === "campaign"
      ? (s.campaign_advice?.campaign_name ? `活动建议：${s.campaign_advice.campaign_name}` : "活动参与建议")
      : `${s.room_name || "房型"} ${s.recommended_price ? "调至 " + yen(s.recommended_price) : "定价建议"}`;
    const desc = s.kind === "pricing"
      ? `${s.date_from} 至 ${s.date_to} · 当前 ${yen(s.current_price)} · 风险 ${s.risk_level || "—"}`
      : (s.campaign_advice?.scope || "活动建议") + ` · ${s.date_from || ""} 至 ${s.date_to || ""}`;
    const badge = s.status === "executed" ? (s.auto_executed || s.reviewer === "system:auto_execute" ? "auto_executed" : "human_executed") : s.status;
    list.push({ key: "s-" + s.id, type: "strategy", id: s.id, status: s.status, badge, title, desc, ico: s.kind === "campaign" ? "green" : "", mark: s.kind === "campaign" ? "活" : "¥", raw: s });
  }
  for (const r of props.reviews) {
    if (!["pending_review", "executed", "execution_failed", "execution_partial"].includes(r.reply_status)) continue;
    list.push({
      key: "r-" + r.id,
      type: "review",
      id: r.id,
      status: r.reply_status,
      title: `评价回复 · ${r.guest_name || "客人"}（${r.rating} 分）`,
      desc: (r.title || r.content || "").slice(0, 60),
      ico: "purple",
      mark: "评",
      raw: r,
    });
  }
  return list;
});
const pendingCount = computed(() => items.value.filter((i) => i.status === "pending_review").length);

async function act(it, decision) {
  busy.value = true;
  error.value = "";
  try {
    if (it.type === "strategy") {
      if (decision === "rollback") await api.rollbackStrategy(it.id, "撤销本次沙盒执行");
      else if (decision === "execute") await api.executeStrategy(it.id);
      else await api.reviewStrategy(it.id, decision, decision === "approve" && canExecute.value ? "确认执行" : "");
    } else {
      if (decision === "rollback") await api.rollbackReply(it.id, "撤销沙盒回复");
      else if (decision === "execute") await api.executeReply(it.id);
      else await api.reviewReply(it.id, decision, decision === "approve" && canExecute.value ? "确认执行" : "");
    }
    emit("refresh");
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function approveAll() {
  if (!confirm(canExecute.value ? "将人审通过全部待确认项，并写入官方沙盒回执。确定继续？" : "将通过全部待确认项（不会立即执行写入）。确定继续？")) return;
  busy.value = true;
  error.value = "";
  try {
    for (const it of items.value.filter((i) => i.status === "pending_review")) {
      if (it.type === "strategy") await api.reviewStrategy(it.id, "approve", "批量确认");
      else await api.reviewReply(it.id, "approve", "批量确认");
    }
    emit("refresh");
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

defineExpose({ pendingCount });
</script>
