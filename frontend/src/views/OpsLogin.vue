<template>
  <div class="login-page">
    <form class="login-card" @submit.prevent="submit">
      <div class="brand" style="padding:0;min-height:auto;margin-bottom:4px">
        <div class="brand-logo"><Icon name="logo" :size="18" /></div>
        <div class="brand-text">
          <div class="brand-name">OIRO</div>
          <div class="brand-sub">内部运营后台</div>
        </div>
      </div>
      <h1>内部运营后台</h1>
      <div class="sub">按岗位进入开通、契约、员工或只读状态工作台。酒店账号不能进入此页。</div>
      <p v-if="error" class="error">{{ error }}</p>
      <label>运维账号</label>
      <input v-model="username" autocomplete="username" />
      <label>密码</label>
      <input v-model="password" type="password" autocomplete="current-password" />
      <button class="btn btn-primary" style="width:100%;margin-top:8px" :disabled="loading" type="submit">
        {{ loading ? "登录中…" : "进入运维端" }}
      </button>
      <div class="hint">
        演示内部账号（非酒店账号）：<br />
        管理员 ops / ops123<br />
        合同 ops_contract / contract123<br />
        系统运维 ops_sysops / sysops123<br />
        运营 ops_operations / operations123<br />
        客服 ops_support / support123<br />
        产品设计不是可登录岗位。官方契约由合同岗安装，示例契约不能安装，也不会编造生产环境 URL。<br />
        <router-link class="link" to="/login">返回酒店端登录</router-link>
      </div>
    </form>
  </div>
</template>
<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api, setToken } from "../api/client";
import Icon from "../components/Icon.vue";

const username = ref("ops");
const password = ref("ops123");
const error = ref("");
const loading = ref(false);
const router = useRouter();

async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const data = await api.opsLogin(username.value, password.value);
    setToken(data.access_token, "ops");
    router.push("/ops");
  } catch (e) {
    error.value = e.message || "登录失败";
  } finally {
    loading.value = false;
  }
}
</script>
