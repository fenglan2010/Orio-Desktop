<template>
  <svg viewBox="0 0 280 260" class="chart" style="height: 240px">
    <polygon v-for="(g, i) in grids" :key="i" :points="g" fill="none" stroke="currentColor" stroke-opacity="0.12" />
    <line v-for="(a, i) in axes" :key="'a'+i" x1="140" y1="130" :x2="a.x" :y2="a.y" stroke="currentColor" stroke-opacity="0.12" />
    <polygon :points="shape" fill="#3b82f6" fill-opacity="0.28" stroke="#2563eb" stroke-width="2" />
    <text v-for="(a, i) in axes" :key="'t'+i" :x="a.tx" :y="a.ty" font-size="11" fill="currentColor" opacity="0.7" text-anchor="middle">{{ a.label }}</text>
  </svg>
</template>
<script setup>
import { computed } from "vue";
const props = defineProps({
  axesData: { type: Array, default: () => [] },
});
const cx = 140, cy = 130, r = 86;
function pt(i, n, ratio) {
  const a = -Math.PI / 2 + (i / n) * Math.PI * 2;
  return { x: cx + r * ratio * Math.cos(a), y: cy + r * ratio * Math.sin(a) };
}
const n = computed(() => props.axesData.length || 1);
const grids = computed(() => [0.33, 0.66, 1].map((ratio) =>
  props.axesData.map((_, i) => {
    const p = pt(i, n.value, ratio);
    return `${p.x},${p.y}`;
  }).join(" ")
));
const axes = computed(() =>
  props.axesData.map((d, i) => {
    const p = pt(i, n.value, 1);
    const t = pt(i, n.value, 1.22);
    return { ...p, tx: t.x, ty: t.y + 4, label: d.label };
  })
);
const shape = computed(() =>
  props.axesData
    .map((d, i) => {
      const p = pt(i, n.value, Math.max(0, Math.min(1, Number(d.value || 0))));
      return `${p.x},${p.y}`;
    })
    .join(" ")
);
</script>
<style scoped>
.chart { width: 100%; display: block; color: var(--text); }
</style>
