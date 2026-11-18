<template>
  <div class="login-page">
    <form class="login-card" @submit.prevent="submit">
      <div class="brand" style="padding:0;min-height:auto;margin-bottom:4px">
        <div class="brand-logo"><Icon name="logo" :size="18" /></div>
        <div class="brand-text">
          <div class="brand-name">OIRO</div>
          <div class="brand-sub">AI 收益管理系统</div>
        </div>
      </div>
      <h1>智能收益优化官</h1>
      <div class="sub">站在酒店立场，对 OTA 收益决策负责。</div>
      <p v-if="error" class="error">{{ error }}</p>
      <label>账号</label>
      <input v-model="username" autocomplete="username" />
      <label>密码</label>
      <input v-model="password" type="password" autocomplete="current-password" />
      <button class="btn btn-primary" style="width:100%;margin-top:8px" :disabled="loading" type="submit">
        {{ loading ? "登录中…" : "进入工作台" }}
      </button>
      <div class="hint" v-if="showDemoHint">
        演示账号（仅源码/开发模式）：杭州西湖精品酒店 admin / admin123 等。正式安装包请使用首次开通时创建的管理员账号。
        <router-link class="link" to="/ops/login">运维端登录</router-link>
      </div>
      <div class="hint" v-else>
        使用首次开通时创建的本酒店管理员账号登录。连接与契约可在系统设置中稍后配置。
        <router-link class="link" to="/ops/login">运维端登录</router-link>
      </div>
    </form>
  </div>
</template>
<script setup>
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, setToken } from "../api/client";
import Icon from "../components/Icon.vue";

const username = ref("");
const password = ref("");
const showDemoHint = ref(false);
const error = ref("");
const loading = ref(false);
const router = useRouter();

onMounted(async () => {
  try {
    const st = await api.installStatus();
    showDemoHint.value = !st.packaged;
    if (showDemoHint.value) {
      username.value = "admin";
      password.value = "admin123";
    }
  } catch {
    showDemoHint.value = true;
    username.value = "admin";
    password.value = "admin123";
  }
});

async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const data = await api.login(username.value, password.value);
    setToken(data.access_token);
    router.push("/dashboard");
  } catch (e) {
    error.value = e.message || "登录失败";
  } finally {
    loading.value = false;
  }
}
</script>
