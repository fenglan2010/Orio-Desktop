<template>
  <svg :viewBox="`0 0 ${width} ${height}`" class="chart" :style="{ height: height + 'px' }">
    <line v-for="g in grids" :key="g.y" :x1="pad.l" :x2="width - pad.r" :y1="g.y" :y2="g.y" stroke="currentColor" stroke-opacity="0.08" />
    <template v-for="(b, i) in bars" :key="i">
      <rect :x="b.x" :y="b.y" :width="b.w" :height="b.h" :fill="b.color" rx="3" />
    </template>
    <text v-for="(t, i) in cats" :key="'c'+i" :x="t.x" :y="height - 6" font-size="10" fill="currentColor" opacity="0.5" text-anchor="middle">{{ t.text }}</text>
    <text :x="4" :y="14" font-size="10" fill="currentColor" opacity="0.45">{{ yMaxLabel }}</text>
  </svg>
</template>
<script setup>
import { computed } from "vue";
const props = defineProps({
  categories: { type: Array, default: () => [] },
  series: { type: Array, default: () => [] },
  width: { type: Number, default: 560 },
  height: { type: Number, default: 220 },
});
const pad = { l: 36, r: 10, t: 16, b: 26 };
const all = computed(() => props.series.flatMap((s) => s.values || []).map(Number).filter(Number.isFinite));
const yMax = computed(() => Math.max(1, ...(all.value.length ? all.value : [1])));
const yMaxLabel = computed(() => String(Math.round(yMax.value)));
const grids = computed(() => {
  const h = props.height - pad.t - pad.b;
  return [0, 0.5, 1].map((r) => ({ y: pad.t + r * h }));
});
const bars = computed(() => {
  const n = props.categories.length || 1;
  const m = props.series.length || 1;
  const inner = props.width - pad.l - pad.r;
  const group = inner / n;
  const gap = 6;
  const bw = Math.max(6, (group - gap * 2) / m - 4);
  const h = props.height - pad.t - pad.b;
  const out = [];
  props.categories.forEach((_, i) => {
    props.series.forEach((s, j) => {
      const v = Number(s.values[i] || 0);
      const bh = (v / yMax.value) * h;
      out.push({
        x: pad.l + i * group + gap + j * (bw + 4),
        y: pad.t + (h - bh),
        w: bw,
        h: Math.max(0, bh),
        color: s.color,
      });
    });
  });
  return out;
});
const cats = computed(() => {
  const n = props.categories.length || 1;
  const inner = props.width - pad.l - pad.r;
  const group = inner / n;
  return props.categories.map((c, i) => ({
    x: pad.l + i * group + group / 2,
    text: String(c).slice(0, 8),
  }));
});
</script>
<style scoped>
.chart { width: 100%; display: block; color: var(--text); }
</style>
