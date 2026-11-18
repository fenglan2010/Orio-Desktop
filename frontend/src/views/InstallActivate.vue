<template>
  <div class="login-page">
    <div class="card card-pad" style="max-width:420px;margin:48px auto">
      <h1>OIRO 安装激活</h1>
      <p class="muted">正式桌面安装包需输入应用安装密钥。开源/源码运行无需此步骤。</p>
      <p v-if="error" class="error">{{ error }}</p>
      <div class="field">
        <label>安装密钥</label>
        <input v-model="key" autocomplete="off" placeholder="OIRO-XXXX-XXXX-XXXX" />
      </div>
      <button class="btn btn-primary" type="button" :disabled="busy" @click="submit">{{ busy ? "验证中…" : "激活" }}</button>
    </div>
  </div>
</template>
<script setup>
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api/client";

const router = useRouter();
const key = ref("");
const busy = ref(false);
const error = ref("");

async function submit() {
  busy.value = true;
  error.value = "";
  try {
    await api.activateInstall(key.value);
    router.replace("/login");
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

onMounted(async () => {
  try {
    const st = await api.installStatus();
    if (!st.blocked) router.replace("/login");
  } catch {
    /* backend not ready */
  }
});
</script>