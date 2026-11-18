<template>
  <span class="tag" :class="cls">{{ label }}</span>
</template>
<script setup>
import { computed } from "vue";
const props = defineProps({ value: String, map: Object });
const dict = {
  pending_review: "待人审",
  pending: "待处理",
  approved: "已通过",
  rejected: "已拒绝",
  executed: "已执行",
  auto_executed: "已自动执行",
  human_executed: "人审后执行",
  rolled_back: "已回滚",
  writing: "写入中",
  accepted: "已接受",
  partial: "部分成功",
  failed: "失败",
  timeout: "超时",
  reconciled: "已对账",
  execution_failed: "写入失败",
  execution_partial: "部分写入",
  write_failed: "写入失败",
  reconcile_mismatch: "对账不一致",
  open: "未处理",
  acknowledged: "已确认",
  high: "高风险",
  medium: "中风险",
  low: "低风险",
  rank_drop: "排名下跌",
  conversion_drop: "转化异常",
  price_inversion: "价格倒挂",
  competitor_cut: "竞对降价",
  negative_review: "差评",
  structural: "结构性问题",
  short_term: "短期波动",
  pricing: "定价建议",
  campaign: "活动建议",
  review_reply: "评价回复",
  disconnected: "未连接",
  credentials_missing: "缺少凭证",
  authorized: "已授权",
  error: "连接异常",
  sandbox_official: "官方沙盒",
  live: "真实回执",
  refused: "已拒绝",
};

const label = computed(() => (props.map && props.map[props.value]) || dict[props.value] || props.value || "—");
const cls = computed(() => props.value || "");
</script>
