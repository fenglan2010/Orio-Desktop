<template>
    <div class="page-head">
      <div>
        <h1>日志查看</h1>
        <p>人审通过、拒绝、回滚与模型调用都会留痕。审计不记录密钥。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportLogs"><Icon name="download" :size="14" /> 导出审计</button>
      </div>
    </div>
    <div class="tabs">
      <button type="button" :class="{ active: tab === 'audit' }" @click="tab = 'audit'">操作审计</button>
      <button type="button" :class="{ active: tab === 'models' }" @click="tab = 'models'">模型调用</button>
    </div>
    <div class="card card-pad" v-if="tab === 'audit'">
      <table class="data">
        <thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="r in logs" :key="r.id">
            <td class="muted">{{ formatDate(r.created_at) }}</td>
            <td>{{ r.actor }}</td>
            <td>{{ actionLabel(r.action) }}</td>
            <td class="code">{{ r.entity_type }} #{{ r.entity_id }}</td>
            <td>{{ r.reason || (r.after && r.after.checkpoint_id ? "ck " + shortId(r.after.checkpoint_id) : "") }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="!logs.length" class="empty">暂无审计记录。生成并人审一条策略后会出现在这里。</div>
    </div>
    <div class="card card-pad" v-else>
      <p class="muted">模型调用记录不含密钥，仅用于追溯任务是否出境或降级。</p>
      <table class="data">
        <thead><tr><th>时间</th><th>任务</th><th>厂商</th><th>出境</th><th>降级</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="r in calls" :key="r.id">
            <td class="muted">{{ formatDate(r.created_at) }}</td>
            <td>{{ r.task_type }}</td>
            <td>{{ r.vendor }} / {{ r.model }}</td>
            <td>{{ r.outbound ? "是" : "否" }}</td>
            <td>{{ r.degraded ? "是" : "否" }}</td>
            <td>{{ r.status }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="!calls.length" class="empty">暂无模型调用记录。</div>
    </div>
</template>
<script setup>
import { inject, onMounted, ref, watch } from "vue";
import Icon from "../components/Icon.vue";
import { api } from "../api/client";
import { actionLabel, downloadText, formatDate, shortId, toCsv } from "../utils/format";

const logs = ref([]);
const calls = ref([]);
const tab = ref("audit");
const shell = inject("appShell", null);

function exportLogs() {
  const rows = [["时间", "操作人", "动作", "对象", "说明"], ...logs.value.map((r) => [r.created_at, r.actor, r.action, `${r.entity_type}#${r.entity_id}`, r.reason || ""])];
  downloadText("oiro-audit.csv", toCsv(rows), "text/csv;charset=utf-8");
}
async function load() {
  const [a, m] = await Promise.all([api.audit(), api.modelCalls()]);
  logs.value = a.logs || [];
  calls.value = m.logs || [];
}
onMounted(() => load().catch(() => {}));
if (shell?.refreshTick) watch(shell.refreshTick, () => load().catch(() => {}));
</script>
