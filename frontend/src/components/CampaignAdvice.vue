<template>
  <section v-if="advice" class="campaign-advice">
    <div class="statline">
      <div>结论<br><b>{{ advice.recommendation_label || joinLabel }}</b></div>
      <div>主评活动<br><b>{{ advice.campaign_name || "渠道活动" }}</b></div>
      <div>范围<br><b>{{ advice.scope || "—" }}</b></div>
      <div>置信度<br><b>{{ pct(advice.confidence ?? confidence, 0) }}</b></div>
    </div>
    <p v-if="advice.reason">{{ advice.reason }}</p>
    <p v-if="advice.risk" class="muted">风险：{{ advice.risk }}</p>
    <p v-if="advice.alternative" class="muted">替代：{{ advice.alternative }}</p>
    <div v-if="impact" class="impact">
      按此建议的预期影响：出租率 {{ signed(impact.occupancy_delta_pct) }} 个百分点，
      ADR {{ signed(impact.adr_delta_pct) }}%，
      窗口营收约 {{ yen(impact.revenue_delta) }}
      <span v-if="impact.note">。{{ impact.note }}</span>
    </div>

    <div v-if="options.length" class="section-title" style="margin-top:16px">分活动评估</div>
    <div v-for="o in options" :key="o.campaign_id || o.campaign_name" class="subcard campaign-option">
      <div class="row" style="justify-content:space-between;align-items:flex-start;gap:8px">
        <div>
          <div class="row" style="gap:8px">
            <b>{{ o.campaign_name }}</b>
            <span class="tag" :class="recClass(o.recommendation)">{{ o.recommendation_label || o.recommendation }}</span>
          </div>
          <p class="muted" style="margin:4px 0 0">
            {{ o.platform }}
            <span v-if="o.commission_uplift_pp != null"> · 佣金上浮约 {{ o.commission_uplift_pp }} 个百分点</span>
            <span v-if="o.typical_discount_pct != null"> · 活动价约 {{ o.typical_discount_pct }}%</span>
          </p>
        </div>
        <span v-if="o.scope" class="muted" style="text-align:right;max-width:46%">{{ o.scope }}</span>
      </div>
      <p v-if="o.reason" style="margin:8px 0 0">{{ o.reason }}</p>
      <p v-if="o.risk" class="muted" style="margin:4px 0 0">风险：{{ o.risk }}</p>
    </div>
  </section>
</template>

<script setup>
import { computed } from "vue";
import { pct, signed, yen } from "../utils/format";

const props = defineProps({
  advice: { type: Object, default: null },
  confidence: { type: Number, default: 0 },
  expectedImpact: { type: Object, default: null },
});

const joinLabel = computed(() => (props.advice?.recommend_join ? "建议参加" : "暂不建议全量参加"));
const options = computed(() => props.advice?.options || []);
const impact = computed(() => props.expectedImpact || props.advice?.expected_impact || null);

function recClass(rec) {
  if (rec === "join") return "success";
  if (rec === "join_limited") return "warning";
  return "rejected";
}
</script>
