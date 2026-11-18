<template>
  <div>
    <div class="section-title">
      PMS/CRS 官方接口
      <span class="muted" style="font-weight:500">账号密码保存后不再回显</span>
    </div>
    <p class="muted" style="margin-top:0">
      这是官方 PMS/CRS API 预留接入，不是内置的轻量 PMS，也不包含房态工作台。
      酒店填写本店账号与密码（如需授权），并在本机配置官方契约 JSON 路径与 API 地址（由厂商/运维提供，本仓库不编造生产 URL）。
      这里只显示连接状态、正式契约是否已启用，以及已开放的动作。未启用正式契约时视为未连接，不会出站，也不会把演示数据改写成虚构的厂商回包。
    </p>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="saved" class="notice notice-ok">{{ saved }}</p>
    <div v-for="ch in connections" :key="ch.kind" class="subcard" style="margin-bottom:12px">
      <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:8px">
        <div>
          <b>{{ ch.label }}</b>
          <div class="row" style="margin-top:4px;flex-wrap:wrap;gap:6px">
            <StatusBadge :value="ch.status" :map="statusMap" />
            <span class="tag" :class="ch.contract_enabled ? 'success' : ''">官方契约 {{ ch.contract_enabled ? "已启用" : "未启用" }}</span>
          </div>
        </div>
        <div v-if="canManage" class="row" style="flex-shrink:0">
          <button class="btn btn-sm" type="button" :disabled="busy === ch.kind" @click="verify(ch)">校验</button>
          <button class="btn btn-sm" type="button" :disabled="busy === ch.kind || ch.status === 'disconnected'" @click="disconnect(ch)">断开</button>
        </div>
      </div>
      <div class="grid-3" style="margin-top:12px">
        <div class="subcard" style="background:var(--surface)"><div class="muted">最近校验</div><b>{{ ch.last_verified ? formatTime(ch.last_verified) : "尚未校验" }}</b></div>
        <div class="subcard" style="background:var(--surface)"><div class="muted">官方契约</div><b>{{ ch.contract_enabled ? "已启用" : "未启用" }}</b></div>
        <div class="subcard" style="background:var(--surface)"><div class="muted">已开放动作</div><b>{{ actionText(ch) }}</b></div>
      </div>
      <p v-if="ch.last_error" class="muted" style="margin:8px 0 0">{{ ch.last_error }}</p>
      <p v-if="ch.hook_note" class="muted" style="margin:8px 0 0">{{ ch.hook_note }}</p>
      <div v-if="canManage" class="form-grid" style="margin-top:12px">
        <div class="field">
          <label>账号</label>
          <input v-model="drafts[ch.kind].account" autocomplete="off" :placeholder="ch.has_credentials ? '已保存，不回显；留空则保持原账号' : '本店在该系统的账号（如需授权）'" />
        </div>
        <div class="field">
          <label>密码</label>
          <input v-model="drafts[ch.kind].password" type="password" autocomplete="new-password" :placeholder="ch.has_credentials ? '已保存，不回显；留空则保持原密码' : '保存后不再回显'" />
        </div>
        <div class="field" style="grid-column:1 / -1">
          <label>官方契约 JSON 路径（本机）</label>
          <input v-model="drafts[ch.kind].contract_file_path" autocomplete="off" :placeholder="ch.contract_file_path || '本机绝对路径'" />
        </div>
        <div class="field" style="grid-column:1 / -1">
          <label>官方 API 地址</label>
          <input v-model="drafts[ch.kind].api_endpoint" autocomplete="off" :placeholder="ch.api_endpoint || 'https://…（由你方提供）'" />
        </div>
      </div>
      <div v-if="canManage" class="row" style="margin-top:10px">
        <button class="btn btn-primary btn-sm" type="button" :disabled="busy === ch.kind" @click="save(ch)">
          {{ busy === ch.kind ? "保存中…" : (ch.status === "disconnected" ? "连接并保存" : "保存连接") }}
        </button>
        <button class="btn btn-sm" type="button" :disabled="busy === ch.kind" @click="saveLocal(ch)">保存本机配置</button>
      </div>
    </div>
    <div class="notice">未由运维端启用正式（非示例）契约前，这里不会显示某一家 PMS 产品已集成。</div>
  </div>
</template>
<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { api } from "../api/client";
import StatusBadge from "./StatusBadge.vue";
import { can } from "../utils/permissions";

const props = defineProps({
  items: { type: Array, default: null },
});
const emit = defineEmits(["refresh"]);

const canManage = computed(() => can("manage_channels"));
const statusMap = {
  disconnected: "未连接",
  credentials_missing: "未连接",
  authorized: "已连接",
  error: "连接异常",
};
const localConnections = ref([]);
const drafts = reactive({});
const busy = ref("");
const error = ref("");
const saved = ref("");

const connections = computed(() => props.items || localConnections.value || []);

function ensureDraft(kind) {
  if (!drafts[kind]) drafts[kind] = { account: "", password: "", contract_file_path: "", api_endpoint: "" };
}

function actionText(ch) {
  const labels = ch.open_actions || ch.supported_actions || [];
  return labels.length ? labels.join("、") : "—";
}

function formatTime(value) {
  if (!value) return "";
  const d = new Date(value.endsWith("Z") || value.includes("+") ? value : value + "Z");
  if (Number.isNaN(d.getTime())) return value;
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function syncDrafts() {
  for (const ch of connections.value) {
    ensureDraft(ch.kind);
    drafts[ch.kind].account = "";
    drafts[ch.kind].password = "";
    if (!drafts[ch.kind].contract_file_path && ch.contract_file_path) {
      drafts[ch.kind].contract_file_path = ch.contract_file_path;
    }
    if (!drafts[ch.kind].api_endpoint && ch.api_endpoint) {
      drafts[ch.kind].api_endpoint = ch.api_endpoint;
    }
  }
}

async function saveLocal(ch) {
  busy.value = ch.kind;
  error.value = "";
  saved.value = "";
  try {
    const draft = drafts[ch.kind] || {};
    await api.savePmsLocal(ch.kind, {
      contract_file_path: draft.contract_file_path || "",
      api_endpoint: draft.api_endpoint || "",
    });
    saved.value = "PMS/CRS 本机路径与 API 地址已保存。";
    emit("refresh");
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = "";
  }
}

async function loadLocal() {
  if (props.items) return;
  const data = await api.pms();
  localConnections.value = data.connections || [];
}

async function save(ch) {
  busy.value = ch.kind;
  error.value = "";
  saved.value = "";
  try {
    const draft = drafts[ch.kind] || {};
    const payload = {};
    if (draft.account) payload.account = draft.account;
    if (draft.password) payload.password = draft.password;
    if (!payload.account && !payload.password && !ch.has_credentials) {
      throw new Error("请输入账号和密码");
    }
    await api.connectPms(ch.kind, payload);
    drafts[ch.kind].account = "";
    drafts[ch.kind].password = "";
    await loadLocal();
    emit("refresh");
    saved.value = "账号已保存，密码不再回显。";
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = "";
  }
}

async function verify(ch) {
  busy.value = ch.kind;
  error.value = "";
  saved.value = "";
  try {
    const res = await api.verifyPms(ch.kind);
    await loadLocal();
    emit("refresh");
    saved.value = res.last_error || "已完成校验。未启用正式契约或开关未打开时，不会出站。";
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = "";
  }
}

async function disconnect(ch) {
  busy.value = ch.kind;
  error.value = "";
  saved.value = "";
  try {
    await api.disconnectPms(ch.kind);
    await loadLocal();
    emit("refresh");
    saved.value = "已断开并清除已保存的账号密码。";
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = "";
  }
}

watch(() => props.items, syncDrafts, { immediate: true });
onMounted(async () => {
  try {
    await loadLocal();
    syncDrafts();
  } catch (e) {
    error.value = e.message;
  }
});
</script>
