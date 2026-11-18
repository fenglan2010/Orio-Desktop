<template>
  <div>
    <div class="section-title">本机模型密钥</div>
    <p class="muted">DeepSeek、通义等网关密钥保存在本机，不会提交到仓库或显示在审计中。留空表示清除已保存项；已保存的密钥显示为掩码。</p>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="saved" class="notice notice-ok">{{ saved }}</p>
    <div v-for="row in rows" :key="row.id" class="subcard" style="margin-bottom:10px">
      <b>{{ row.label }}</b>
      <div class="form-grid" style="margin-top:8px">
        <div class="field">
          <label>API Key</label>
          <input v-model="draft[row.id].api_key" type="password" autocomplete="new-password" :placeholder="row.configured ? '已保存（********）；输入新值可覆盖' : '可选'" />
        </div>
        <div v-if="row.showUrl" class="field">
          <label>Base URL</label>
          <input v-model="draft[row.id].base_url" :placeholder="row.base_url || '默认'" />
        </div>
        <div v-if="row.showModel" class="field">
          <label>Model</label>
          <input v-model="draft[row.id].model" :placeholder="row.model || '默认'" />
        </div>
      </div>
    </div>
    <button class="btn btn-primary" type="button" :disabled="busy || !canSave" @click="save">{{ busy ? "保存中…" : "保存模型密钥" }}</button>
  </div>
</template>
<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { api } from "../api/client";
import { can } from "../utils/permissions";

const VENDOR_META = {
  deepseek: { label: "DeepSeek", showUrl: false, showModel: false, key: "deepseek_api_key" },
  qwen: { label: "通义千问", showUrl: false, showModel: false, key: "qwen_api_key" },
  openai: { label: "OpenAI（境外，需单独开启）", showUrl: false, showModel: false, key: "openai_api_key" },
  gemini: { label: "Gemini（境外）", showUrl: false, showModel: false, key: "gemini_api_key" },
  grok: { label: "Grok（境外）", showUrl: false, showModel: false, key: "grok_api_key" },
  self_hosted: { label: "自建开源模型（OpenAI 兼容）", showUrl: true, showModel: true, key: "self_hosted_api_key" },
};

const rows = ref([]);
const draft = reactive({});
const busy = ref(false);
const saved = ref("");
const error = ref("");
const canSave = computed(() => can("update_policy"));

function sync(data) {
  const vendors = data?.vendors || {};
  rows.value = Object.keys(VENDOR_META).map((id) => ({
    id,
    label: VENDOR_META[id].label,
    showUrl: VENDOR_META[id].showUrl,
    showModel: VENDOR_META[id].showModel,
    configured: vendors[id]?.configured,
    base_url: vendors[id]?.base_url,
    model: vendors[id]?.model,
  }));
  for (const id of Object.keys(VENDOR_META)) {
    draft[id] = { api_key: "", base_url: vendors[id]?.base_url || "", model: vendors[id]?.model || "" };
  }
}

async function load() {
  const cfg = await api.localConfig();
  sync(cfg.model_keys);
}

async function save() {
  busy.value = true;
  error.value = "";
  saved.value = "";
  try {
    const vendors = {};
    for (const id of Object.keys(VENDOR_META)) {
      const meta = VENDOR_META[id];
      const block = {};
      const d = draft[id] || {};
      if (d.api_key) block[meta.key] = d.api_key;
      if (meta.showUrl && d.base_url) block.self_hosted_base_url = d.base_url;
      if (meta.showModel && d.model) block.self_hosted_model = d.model;
      if (Object.keys(block).length) vendors[id] = block;
    }
    const res = await api.saveLocalModelKeys({ vendors });
    sync(res);
    saved.value = "模型密钥已保存到本机。";
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

onMounted(() => {
  load().catch((e) => {
    error.value = e.message;
  });
});
</script>