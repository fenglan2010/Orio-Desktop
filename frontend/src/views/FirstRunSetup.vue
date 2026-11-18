<template>
  <div class="login-page">
    <form class="login-card" style="max-width:640px" @submit.prevent="submit">
      <h1>开通本店</h1>
      <p class="muted">正式安装包首次使用：请填写<strong>本酒店</strong>真实档案、管理员账号与房型。不会预装演示酒店或演示账号。</p>
      <p v-if="error" class="error">{{ error }}</p>

      <div class="section-title">酒店信息</div>
      <div class="form-grid">
        <div class="field" style="grid-column:1/-1"><label>酒店名称 *</label><input v-model="hotel.name" required /></div>
        <div class="field"><label>城市</label><input v-model="hotel.city" placeholder="例如：杭州" /></div>
        <div class="field"><label>定位/标签</label><input v-model="hotel.positioning" placeholder="精品商务酒店" /></div>
        <div class="field" style="grid-column:1/-1"><label>地址</label><input v-model="hotel.address" /></div>
        <div class="field"><label>主 OTA 渠道</label>
          <select v-model="hotel.platform">
            <option value="ctrip">携程</option>
            <option value="meituan">美团</option>
            <option value="fliggy">飞猪</option>
          </select>
        </div>
      </div>

      <div class="section-title" style="margin-top:16px">管理员账号</div>
      <div class="form-grid">
        <div class="field"><label>用户名 *</label><input v-model="admin.username" autocomplete="username" required /></div>
        <div class="field"><label>显示名</label><input v-model="admin.display_name" /></div>
        <div class="field" style="grid-column:1/-1"><label>密码 *</label><input v-model="admin.password" type="password" autocomplete="new-password" required /></div>
      </div>

      <div class="section-title" style="margin-top:16px">
        房型档案 *
        <button class="btn btn-sm" type="button" @click="addRoom">+ 添加房型</button>
      </div>
      <p class="muted">无 PMS 或尚未接通 API 时，请在本机维护房型。字段含代码、名称、数量、基础价与位置（楼栋/楼层/区域）。后续可在「系统设置」或 L3「房态维护」中修改。</p>
      <div class="subcard" v-for="(r, idx) in rooms" :key="idx" style="margin-top:10px">
        <div class="row" style="justify-content:space-between;margin-bottom:8px">
          <b>房型 {{ idx + 1 }}</b>
          <button v-if="rooms.length > 1" class="btn btn-sm" type="button" @click="removeRoom(idx)">删除</button>
        </div>
        <div class="form-grid">
          <div class="field"><label>房型代码 *</label><input v-model="r.code" placeholder="DK" required /></div>
          <div class="field"><label>名称 *</label><input v-model="r.name" required /></div>
          <div class="field"><label>房间数量 *</label><input v-model.number="r.inventory" type="number" min="1" required /></div>
          <div class="field"><label>基础价（元）*</label><input v-model.number="r.base_price" type="number" min="1" required /></div>
          <div class="field" style="grid-column:1/-1"><label>位置 *</label><input v-model="r.location" placeholder="例如：A栋3-5层" required /></div>
        </div>
      </div>

      <details style="margin-top:16px">
        <summary class="muted" style="cursor:pointer">可选：本机连接（可稍后在系统设置中配置）</summary>
        <p class="muted">模型密钥仅存本机；OTA 需官方契约 JSON 的<strong>本地绝对路径</strong>；PMS 仅填写厂商提供的 HTTPS 基址。未配置时主界面显示真实空状态，不会用演示数据冒充。</p>
        <div class="field" style="margin-top:10px">
          <label>DeepSeek API Key（可选）</label>
          <input v-model="optDeepseek" type="password" autocomplete="off" />
        </div>
        <div class="form-grid" style="margin-top:8px">
          <div class="field"><label>携程账号（可选）</label><input v-model="optCtrip.account" /></div>
          <div class="field"><label>携程密码（可选）</label><input v-model="optCtrip.password" type="password" /></div>
          <div class="field" style="grid-column:1/-1"><label>携程契约 JSON 路径（可选）</label><input v-model="optCtrip.contract_file_path" /></div>
        </div>
        <div class="form-grid" style="margin-top:8px">
          <div class="field" style="grid-column:1/-1"><label>PMS API 基址（可选）</label><input v-model="optPms.api_endpoint" placeholder="https://…" /></div>
        </div>
      </details>

      <button class="btn btn-primary" style="width:100%;margin-top:16px" type="submit" :disabled="busy">
        {{ busy ? "创建中…" : "完成开通并进入工作台" }}
      </button>
    </form>
  </div>
</template>
<script setup>
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, setToken } from "../api/client";
const router = useRouter();
const busy = ref(false);
const error = ref("");
const hotel = ref({ name: "", city: "", address: "", positioning: "", platform: "ctrip" });
const admin = ref({ username: "", password: "", display_name: "" });
const rooms = ref([{ code: "", name: "", inventory: 10, base_price: 399, location: "" }]);
const optDeepseek = ref("");
const optCtrip = ref({ account: "", password: "", contract_file_path: "" });
const optPms = ref({ api_endpoint: "" });

function addRoom() {
  rooms.value.push({ code: "", name: "", inventory: 10, base_price: 399, location: "" });
}
function removeRoom(idx) {
  rooms.value.splice(idx, 1);
}

async function submit() {
  busy.value = true;
  error.value = "";
  try {
    const data = await api.completeFirstRun({
      hotel_name: hotel.value.name,
      city: hotel.value.city,
      address: hotel.value.address,
      positioning: hotel.value.positioning,
      platform: hotel.value.platform,
      admin_username: admin.value.username,
      admin_password: admin.value.password,
      admin_display_name: admin.value.display_name,
      room_types: rooms.value.map((r) => ({
        code: r.code,
        name: r.name,
        inventory: r.inventory,
        base_price: r.base_price,
        location: r.location,
      })),
      model_keys: optDeepseek.value ? { vendors: { deepseek: { deepseek_api_key: optDeepseek.value } } } : undefined,
      ota: optCtrip.value.account || optCtrip.value.contract_file_path
        ? [{ platform: "ctrip", ...optCtrip.value }]
        : [],
      pms: optPms.value.api_endpoint ? [{ kind: "pms", api_endpoint: optPms.value.api_endpoint }] : [],
    });
    setToken(data.access_token);
    router.replace("/dashboard");
  } catch (e) {
    error.value = e.message || "开通失败";
  } finally {
    busy.value = false;
  }
}

onMounted(async () => {
  try {
    const st = await api.installStatus();
    if (st.blocked) {
      router.replace("/install");
      return;
    }
    if (!st.initial_setup_required) router.replace("/login");
  } catch {
    /* backend starting */
  }
});
</script>