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
      <div class="hint">
        演示账号（模拟数据，不连接真实 OTA）：<br />
        杭州西湖精品酒店（演示）：admin / admin123（酒店管理员）· manager / manager123（收益经理）<br />
        approver / approver123（调价审批人）· analyst / analyst123（运营观察）<br />
        成都春熙商务酒店（演示）：admin_b / admin123（仅该店管理员）<br />
        <router-link class="link" to="/ops/login">运维端登录</router-link>（官方契约和接口由运维配置，请不要在酒店设置里粘贴契约）
      </div>
    </form>
  </div>
</template>
<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api, setToken } from "../api/client";
import Icon from "../components/Icon.vue";

const username = ref("admin");
const password = ref("admin123");
const error = ref("");
const loading = ref(false);
const router = useRouter();

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
