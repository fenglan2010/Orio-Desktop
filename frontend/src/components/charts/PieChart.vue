<template>
  <div class="pie-wrap">
    <svg viewBox="0 0 200 200" class="pie">
      <path v-for="(s, i) in slices" :key="i" :d="s.d" :fill="s.color" stroke="var(--surface)" stroke-width="2" />
      <circle cx="100" cy="100" r="46" fill="var(--surface)" />
    </svg>
    <ul class="pie-leg">
      <li v-for="(s, i) in items" :key="i">
        <i :style="{ background: s.color }"></i>
        <span>{{ s.name }} {{ s.pct }}%</span>
        <b v-if="s.sub">{{ s.sub }}</b>
      </li>
    </ul>
  </div>
</template>
<script setup>
import { computed } from "vue";
const props = defineProps({
  items: { type: Array, default: () => [] },
});
const colors = ["#3b82f6", "#34d399", "#f59e0b", "#f87171", "#a78bfa", "#22d3ee"];
const items = computed(() => {
  const total = props.items.reduce((s, x) => s + Number(x.value || 0), 0) || 1;
  return props.items.map((x, i) => ({
    ...x,
    color: x.color || colors[i % colors.length],
    pct: Math.round((Number(x.value || 0) / total) * 100),
  }));
});
function arc(cx, cy, r, a0, a1) {
  const p0 = [cx + r * Math.cos(a0), cy + r * Math.sin(a0)];
  const p1 = [cx + r * Math.cos(a1), cy + r * Math.sin(a1)];
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M ${cx} ${cy} L ${p0[0]} ${p0[1]} A ${r} ${r} 0 ${large} 1 ${p1[0]} ${p1[1]} Z`;
}
const slices = computed(() => {
  const total = items.value.reduce((s, x) => s + Number(x.value || 0), 0) || 1;
  let a = -Math.PI / 2;
  return items.value.map((x) => {
    const sweep = (Number(x.value || 0) / total) * Math.PI * 2;
    const d = arc(100, 100, 90, a, a + Math.max(sweep, 0.01));
    a += sweep;
    return { d, color: x.color };
  });
});
</script>
<style scoped>
.pie-wrap { display: grid; grid-template-columns: 180px 1fr; gap: 8px; align-items: center; }
.pie { width: 180px; height: 180px; }
.pie-leg { list-style: none; margin: 0; padding: 0; font-size: 12px; }
.pie-leg li { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
.pie-leg i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.pie-leg b { margin-left: auto; font-variant-numeric: tabular-nums; }
@media (max-width: 700px) { .pie-wrap { grid-template-columns: 1fr; } .pie { margin: 0 auto; } }
</style>
