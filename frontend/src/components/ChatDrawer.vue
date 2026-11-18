<template>
  <div v-if="open" class="backdrop" @click="$emit('close')"></div>
  <aside v-if="open" class="drawer">
    <div class="drawer-head">
      <div>
        <h2>决策对话</h2>
        <div class="muted" style="font-size:12px">可查询数据、解释预警、追问策略。助手不会直接调价或发送回复。</div>
      </div>
      <button type="button" class="icon-btn" aria-label="关闭" title="关闭" @click="$emit('close')"><Icon name="close" /></button>
    </div>
    <div class="drawer-body" style="display:flex;flex-direction:column">
      <div class="chat-log" ref="logEl" style="flex:1;min-height:200px">
        <div v-for="m in messages" :key="m.id" class="bubble" :class="m.role">{{ m.content }}</div>
        <div v-if="!messages.length" class="empty">可以问「为什么排名掉了」「该不该跟价」或「差评怎么回」。</div>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
    </div>
    <form class="drawer-foot" style="display:flex" @submit.prevent="send">
      <input v-model="text" class="input" style="flex:1" placeholder="输入问题" />
      <button class="btn btn-primary" :disabled="busy || !text.trim()" type="submit">发送</button>
    </form>
  </aside>
</template>
<script setup>
import { nextTick, ref, watch } from "vue";
import { api } from "../api/client";
import Icon from "./Icon.vue";

const props = defineProps({ open: Boolean });
defineEmits(["close"]);

const messages = ref([]);
const text = ref("");
const busy = ref(false);
const error = ref("");
const logEl = ref(null);

async function load() {
  const data = await api.chatHistory();
  messages.value = data.messages || [];
  await nextTick();
  if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight;
}
async function send() {
  const content = text.value.trim();
  if (!content) return;
  busy.value = true;
  error.value = "";
  messages.value.push({ id: "tmp-user", role: "user", content });
  text.value = "";
  try {
    await api.chat(content);
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
watch(
  () => props.open,
  async (v) => {
    if (!v) return;
    try { await load(); } catch (e) { error.value = e.message; }
  }
);
</script>
