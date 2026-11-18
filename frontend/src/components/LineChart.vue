<template>
  <svg :viewBox="`0 0 ${width} ${height}`" class="chart" preserveAspectRatio="none">
    <defs>
      <linearGradient v-for="(s, i) in normalized" :id="`fill-${uid}-${i}`" :key="'g'+i" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" :stop-color="s.color" stop-opacity="0.28" />
        <stop offset="100%" :stop-color="s.color" stop-opacity="0.02" />
      </linearGradient>
    </defs>
    <line v-for="g in grids" :key="g.y" :x1="pad.l" :x2="width - pad.r" :y1="g.y" :y2="g.y" stroke="currentColor" stroke-opacity="0.08" />
    <template v-for="(s, i) in plotted" :key="i">
      <polygon v-if="s.area && s.areaPoints" :points="s.areaPoints" :fill="`url(#fill-${uid}-${i})`" />
      <polyline fill="none" :stroke="s.color" :stroke-width="s.width || 2" :stroke-dasharray="s.dashed ? '5 4' : undefined" :points="s.points" />
      <circle v-for="(p, j) in s.dots" :key="j" :cx="p.x" :cy="p.y" r="2.6" :fill="s.color" />
    </template>
    <text v-for="(t, i) in xlabels" :key="'x'+i" :x="t.x" :y="height - 6" font-size="10" fill="currentColor" opacity="0.45" text-anchor="middle">{{ t.text }}</text>
    <text :x="4" :y="14" font-size="10" fill="currentColor" opacity="0.45">{{ yMaxLabel }}</text>
    <text :x="4" :y="height - pad.b - 2" font-size="10" fill="currentColor" opacity="0.45">{{ yMinLabel }}</text>
  </svg>
</template>
<script setup>
import { computed } from "vue";

const props = defineProps({
  values: { type: Array, default: () => [] },
  series: { type: Array, default: () => [] },
  labels: { type: Array, default: () => [] },
  color: { type: String, default: "#155DFC" },
  invert: { type: Boolean, default: false },
  width: { type: Number, default: 560 },
  height: { type: Number, default: 220 },
  area: { type: Boolean, default: false },
});

const uid = Math.random().toString(36).slice(2, 8);
const pad = { l: 32, r: 10, t: 16, b: 24 };

const normalized = computed(() => {
  if (props.series.length) return props.series;
  if (!props.values.length) return [];
  return [{ name: "", values: props.values, color: props.color, area: props.area }];
});

const allVals = computed(() =>
  normalized.value.flatMap((s) => (s.values || []).map((v) => Number(v)).filter((v) => Number.isFinite(v)))
);
const yMin = computed(() => {
  const vals = allVals.value;
  if (!vals.length) return 0;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  if (min === max) return min > 0 ? 0 : min;
  return min;
});
const yMax = computed(() => {
  const vals = allVals.value;
  if (!vals.length) return 1;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  return max === min ? max + 1 : max;
});
const yMinLabel = computed(() => String(Math.round(yMin.value * 100) / 100));
const yMaxLabel = computed(() => String(Math.round(yMax.value * 100) / 100));

function coords(vals) {
  const span = yMax.value - yMin.value || 1;
  const w = props.width - pad.l - pad.r;
  const h = props.height - pad.t - pad.b;
  const n = (vals || []).length;
  return (vals || []).map((v, i) => {
    const x = pad.l + ((n <= 1 ? 0.5 : i / (n - 1)) * w);
    const num = Number(v);
    if (!Number.isFinite(num)) return { x, y: null };
    const ratio = (num - yMin.value) / span;
    const y = pad.t + (props.invert ? ratio : 1 - ratio) * h;
    return { x, y };
  });
}

const plotted = computed(() =>
  normalized.value.map((s) => {
    const c = coords(s.values);
    const finite = c.filter((p) => p.y != null);
    const base = props.height - pad.b;
    return {
      ...s,
      points: finite.map((p) => `${p.x},${p.y}`).join(" "),
      areaPoints: finite.length ? `${finite[0].x},${base} ` + finite.map((p) => `${p.x},${p.y}`).join(" ") + ` ${finite[finite.length - 1].x},${base}` : "",
      dots: finite,
    };
  })
);

const grids = computed(() => {
  const h = props.height - pad.t - pad.b;
  return [0, 0.33, 0.66, 1].map((r) => ({ y: pad.t + r * h }));
});

const xlabels = computed(() => {
  const labs = props.labels;
  const first = plotted.value[0];
  if (!labs.length || !first?.dots?.length) return [];
  const last = labs.length - 1;
  const idx = last <= 1 ? [0] : last === 2 ? [0, last] : [0, Math.floor(last / 2), last];
  return [...new Set(idx)].map((i) => {
    const raw = String(labs[i] || "");
    const text = raw.length > 10 ? raw.slice(5) : raw;
    return { x: first.dots[i]?.x || 0, text };
  });
});
const chartH = computed(() => props.height + "px");
</script>
<style scoped>
.chart { width: 100%; height: v-bind(chartH); display: block; color: var(--text); }
</style>
