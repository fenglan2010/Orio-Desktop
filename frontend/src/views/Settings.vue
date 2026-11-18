<template>
    <div class="page-head">
      <div>
        <h1>系统设置</h1>
        <p>管理账号、角色权限、酒店信息、本机模型密钥、渠道连接与显示偏好。OTA/PMS 官方契约与 API 由你在本机自行配置；开源/桌面模式不把密钥托管到远端服务器。PMS/CRS 为官方 API 预留接入，不包含可单独售卖的轻量 PMS。</p>
      </div>
      <span class="tag" :class="productEnabled ? 'success' : ''">{{ productEnabled ? "已开通" : "尚未开通" }}</span>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="saved" class="notice notice-ok">{{ saved }}</p>

    <div class="settings-layout">
      <nav class="settings-nav">
        <button v-for="s in sections" :key="s.id" type="button" :class="{ active: section === s.id }" @click="setSection(s.id)">
          <Icon :name="s.icon" :size="16" /> {{ s.label }}
        </button>
      </nav>

      <div class="card card-pad">
        <!-- accounts -->
        <template v-if="section === 'accounts'">
          <div class="section-title">账号管理</div>
          <p class="muted">当前登录账号来自认证接口。酒店管理员可以添加、编辑、重置密码或停用本酒店账号；不能停用或降级唯一的管理员。停用优先于删除。</p>
          <h3 style="font-size:14px">当前登录</h3>
          <div class="form-grid">
            <div class="field"><label>用户名</label><input :value="me.username" disabled /></div>
            <div class="field"><label>显示名</label><input :value="me.display_name" disabled /></div>
            <div class="field"><label>角色</label><input :value="roleLabel(me.role)" disabled /></div>
            <div class="field"><label>所属酒店</label><input :value="me.hotel_name" disabled /></div>
          </div>
          <div class="section-title" style="margin-top:16px">
            酒店账号
            <button v-if="can('manage_users')" class="btn btn-primary btn-sm" type="button" @click="startCreate">+ 添加账号</button>
          </div>
          <p v-if="!can('manage_users')" class="muted">仅酒店管理员可以管理账号。你目前只能查看自己的登录信息。</p>
          <div v-if="can('manage_users')" class="subcard" v-for="u in users" :key="u.id">
            <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:8px">
              <div>
                <b>{{ u.display_name }}</b>
                <span class="muted"> · {{ u.username }}</span>
                <div class="row" style="margin-top:4px;gap:6px">
                  <StatusBadge :value="u.role" :map="roleMap" />
                  <span class="tag" :class="u.disabled ? '' : 'success'">{{ u.disabled ? "已停用" : "启用中" }}</span>
                </div>
              </div>
              <div class="row" style="flex-shrink:0">
                <button class="btn btn-sm" type="button" @click="startEdit(u)">编辑</button>
                <button v-if="!u.disabled" class="btn btn-sm" type="button" @click="toggleUser(u, true)">停用</button>
                <button v-else class="btn btn-primary btn-sm" type="button" @click="toggleUser(u, false)">启用</button>
              </div>
            </div>
          </div>
          <div v-if="userForm" class="subcard" style="background:var(--primary-soft);margin-top:12px">
            <b>{{ userForm.id ? "编辑账号" : "添加账号" }}</b>
            <div class="form-grid" style="margin-top:10px">
              <div class="field">
                <label>用户名</label>
                <input v-model="userForm.username" :disabled="!!userForm.id" placeholder="3–32 位字母数字" />
              </div>
              <div class="field">
                <label>显示名</label>
                <input v-model="userForm.display_name" />
              </div>
              <div class="field">
                <label>角色</label>
                <select v-model="userForm.role">
                  <option value="admin">酒店管理员</option>
                  <option value="revenue_manager">收益经理</option>
                  <option value="approver">调价审批人</option>
                  <option value="analyst">运营观察</option>
                </select>
              </div>
              <div class="field">
                <label>{{ userForm.id ? "重置密码（留空则不改）" : "密码" }}</label>
                <input v-model="userForm.password" type="password" autocomplete="new-password" />
              </div>
            </div>
            <div class="row" style="margin-top:10px">
              <button class="btn btn-primary" type="button" :disabled="savingUser" @click="saveUser">{{ savingUser ? "保存中…" : "保存" }}</button>
              <button class="btn btn-sm" type="button" @click="userForm = null">取消</button>
            </div>
          </div>
        </template>

        <!-- hotel -->
        <template v-else-if="section === 'hotel'">
          <div class="section-title">酒店信息</div>
          <p class="muted">以下档案来自演示数据与看板接口，本轮为只读展示。</p>
          <div class="form-grid">
            <div class="field"><label>酒店名称</label><input :value="hotel.name || me.hotel_name || ''" disabled /></div>
            <div class="field"><label>城市</label><input :value="hotel.city || me.city || ''" disabled /></div>
            <div class="field" style="grid-column:1 / -1"><label>地址</label><input :value="hotel.address || me.address || ''" disabled /></div>
            <div class="field"><label>定位</label><input :value="hotel.positioning || me.positioning || ''" disabled /></div>
            <div class="field"><label>数据来源</label><input value="携程 · 模拟读取 / 官方沙盒写入" disabled /></div>
          </div>
          <div class="space"></div>
          <div class="section-title">房型与价格约束</div>
          <p class="muted">上下限会在生成调价建议时生效。修改后只影响之后新生成的策略，不会改已有待审方案，也不会写入真实 OTA。</p>
          <template v-for="r in rooms" :key="r.id"><div class="subcard" v-if="constraintDraft[r.id]">
            <div class="row" style="justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:10px">
              <div>
                <b>{{ r.name }}</b>
                <div class="muted">{{ r.inventory }} 间 · 基础价 {{ yen(r.base_price) }}</div>
              </div>
              <div class="row" style="gap:8px;align-items:flex-end">
                <div class="field" style="margin:0">
                  <label>最低价</label>
                  <input type="number" min="1" step="1" v-model.number="constraintDraft[r.id].min_price" :disabled="!can('update_constraints')" />
                </div>
                <div class="field" style="margin:0">
                  <label>最高价</label>
                  <input type="number" min="1" step="1" v-model.number="constraintDraft[r.id].max_price" :disabled="!can('update_constraints')" />
                </div>
              </div>
            </div>
          </div>
          </template>
          <div class="row" style="margin-top:12px">
            <button class="btn btn-primary" type="button" :disabled="savingConstraints || !rooms.length || !can('update_constraints')" @click="saveConstraints">
              {{ savingConstraints ? "保存中…" : "保存价格约束" }}
            </button>
          </div>
        </template>

        <!-- local -->
        <template v-else-if="section === 'local'">
          <LocalModelKeys />
        </template>

        <!-- ota -->
        <template v-else-if="section === 'ota'">
          <ChannelConnections :items="channels" @refresh="load" />
        </template>

        <!-- pms -->
        <template v-else-if="section === 'pms'">
          <PmsConnections :items="pmsConnections" @refresh="load" />
        </template>

        <!-- permissions -->
        <template v-else-if="section === 'permissions'">
          <div class="section-title">权限设置</div>
          <div class="subcard" style="background:var(--primary-soft)">
            <b>执行策略</b>
            <p v-if="!can('update_policy')" class="muted">仅酒店管理员可以修改执行策略，避免收益经理自行打开 live 或自动执行。</p>
            <p style="margin:6px 0">默认关闭自动执行，行为与第一阶段相同：调价、活动和评价回复都要人审。开启后，仅低风险、非节假日、调价幅度低于阈值的方案可自动执行；中高风险和评价回复仍必须人审。默认写入为官方沙盒回执。酒店只需授权本店 OTA 账号和密码；官方契约和接口由运维端配置。若改为 live，须渠道已授权且运维端已启用官方契约并打开写入开关，否则会拒绝且不会出站。评价回复仍不会自动对外发布。</p>
            <div class="row" style="justify-content:space-between;margin:10px 0">
              <div>
                <b>低风险自动执行</b>
                <div class="muted">默认关闭。开启后仍受风险、节假日和调价幅度约束。</div>
              </div>
              <button type="button" class="toggle" :class="{ on: policy.auto_execute_enabled }" :disabled="savingPolicy || !can('update_policy')" @click="policy.auto_execute_enabled = !policy.auto_execute_enabled">
                <i></i>
              </button>
            </div>
            <div class="row" style="justify-content:space-between;margin:10px 0">
              <div>
                <b>节假日必须人审</b>
                <div class="muted">入住窗口覆盖 2026–2027 年法定节假日时，不自动执行。</div>
              </div>
              <button type="button" class="toggle" :class="{ on: policy.holiday_requires_review }" :disabled="savingPolicy || !can('update_policy')" @click="policy.holiday_requires_review = !policy.holiday_requires_review">
                <i></i>
              </button>
            </div>
            <div class="form-grid" style="margin-top:8px">
              <div class="field">
                <label>调价人审阈值（%）</label>
                <input type="number" min="1" max="15" step="1" v-model.number="policy.price_change_human_threshold_pct" :disabled="!can('update_policy')" />
                <div class="muted">默认 15%，可更严，不可高于 15%。</div>
              </div>
              <div class="field">
                <label>回滚策略</label>
                <select v-model="policy.rollback_policy" :disabled="!can('update_policy')">
                  <option value="manual">人工回滚</option>
                  <option value="auto_on_write_failure">写入失败时自动回退</option>
                </select>
              </div>
              <div class="field">
                <label>自动执行最高风险</label>
                <input value="低风险（本增量不可上调）" disabled />
              </div>
              <div class="field">
                <label>写入模式</label>
                <select v-model="policy.write_mode" :disabled="!can('update_policy')">
                  <option value="sandbox">官方沙盒（默认）</option>
                  <option value="live">live（需授权，且运维端已启用契约与写入开关）</option>
                </select>
                <div class="muted">演示默认沙盒。打开 live 仍要等运维端已启用官方契约，否则会安全拒绝。</div>
              </div>
            </div>
            <div class="row" style="margin-top:12px">
              <button class="btn btn-primary" type="button" :disabled="savingPolicy || !can('update_policy')" @click="savePolicy">
                {{ savingPolicy ? "保存中…" : "保存执行策略" }}
              </button>
            </div>
          </div>
          <div class="section-title" style="margin-top:16px">功能权限矩阵</div>
          <p class="muted">矩阵由服务器定义，按角色固定授权，不提供自定义编辑。下列为当前酒店账号的真实权限，不是示意。</p>
          <div class="subcard" v-for="role in roleCards" :key="role.id">
            <div>
              <b>{{ role.label }}</b>
              <div class="muted">访问层级：{{ (role.pages || []).join("、") || "—" }}</div>
              <div class="muted" style="margin-top:4px">
                成员：
                <template v-if="(role.users || []).length">
                  <span v-for="(u, i) in role.users" :key="u.id">{{ u.display_name }}（{{ u.username }}）<span v-if="u.disabled">·已停用</span><span v-if="i < role.users.length - 1">、</span></span>
                </template>
                <span v-else>暂无</span>
              </div>
            </div>
            <div class="level-pills" style="margin-top:10px">
              <span v-for="lv in ['L1','L2','L3','L4','L5']" :key="lv" :class="(role.pages || []).includes(lv) ? 'lv-on-' + lv.toLowerCase() : 'lv-off'">{{ lv }}</span>
            </div>
          </div>
          <table class="data" style="margin-top:12px">
            <thead>
              <tr>
                <th>功能</th>
                <th v-for="r in (permissionMatrix.roles || [])" :key="r.id">{{ r.label }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in (permissionMatrix.rows || [])" :key="row.id">
                <td>{{ row.label }}</td>
                <td v-for="r in (permissionMatrix.roles || [])" :key="r.id">{{ row.cells && row.cells[r.id] ? "✓" : "—" }}</td>
              </tr>
            </tbody>
          </table>
        </template>

        <!-- notify -->
        <template v-else-if="section === 'notify'">
          <div class="section-title">通知设置</div>
          <p class="muted">预警通知偏好保存在本机，用于控制提醒开关的演示，不会发送邮件或短信。</p>
          <div class="subcard" v-for="n in notifyItems" :key="n.key">
            <div class="row" style="justify-content:space-between">
              <div>
                <b>{{ n.title }}</b>
                <div class="muted">{{ n.desc }}</div>
              </div>
              <button type="button" class="toggle" :class="{ on: notify[n.key] }" @click="toggleNotify(n.key)">
                <i></i>
              </button>
            </div>
          </div>
        </template>

        <!-- appearance -->
        <template v-else-if="section === 'appearance'">
          <div class="section-title">外观设置</div>
          <p class="muted">自定义界面主题和显示密度，设置保存在本机。</p>
          <div class="field-label" style="margin-bottom:8px">主题模式</div>
          <div class="theme-pick">
            <button type="button" :class="{ active: appearance.theme === 'light' }" @click="appearance.theme = 'light'">
              <div><Icon name="sun" :size="22" /></div>浅色模式
            </button>
            <button type="button" :class="{ active: appearance.theme === 'dark' }" @click="appearance.theme = 'dark'">
              <div><Icon name="moon" :size="22" /></div>深色模式
            </button>
            <button type="button" :class="{ active: appearance.theme === 'auto' }" @click="appearance.theme = 'auto'">
              <div><Icon name="auto" :size="22" /></div>自动
            </button>
          </div>
          <div class="field" style="margin-top:16px;max-width:280px">
            <label>语言设置</label>
            <select class="input" disabled><option>简体中文</option></select>
          </div>
          <div class="field-label" style="margin:8px 0">显示密度</div>
          <button type="button" class="density-opt" :class="{ active: appearance.density === 'comfortable' }" @click="appearance.density = 'comfortable'">
            <input type="radio" :checked="appearance.density === 'comfortable'" readOnly />
            <div><b>舒适</b><span>更多空白空间，适合大屏幕</span></div>
          </button>
          <button type="button" class="density-opt" :class="{ active: appearance.density === 'standard' }" @click="appearance.density = 'standard'">
            <input type="radio" :checked="appearance.density === 'standard'" readOnly />
            <div><b>标准</b><span>平衡的显示密度</span></div>
          </button>
          <button type="button" class="density-opt" :class="{ active: appearance.density === 'compact' }" @click="appearance.density = 'compact'">
            <input type="radio" :checked="appearance.density === 'compact'" readOnly />
            <div><b>紧凑</b><span>显示更多内容，节省空间</span></div>
          </button>
          <div class="row" style="justify-content:flex-end;margin-top:12px">
            <button class="btn btn-primary" type="button" @click="applyAppear">应用更改</button>
          </div>
        </template>
      </div>
    </div>
</template>

<script setup>
import { computed, inject, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "../components/Icon.vue";
import ChannelConnections from "../components/ChannelConnections.vue";
import PmsConnections from "../components/PmsConnections.vue";
import LocalModelKeys from "../components/LocalModelKeys.vue";
import { api } from "../api/client";
import { roleLabel, yen } from "../utils/format";
import { can } from "../utils/permissions";
import StatusBadge from "../components/StatusBadge.vue";
import { loadAppearance, loadNotify, saveAppearance, saveNotify } from "../utils/prefs";

const sections = [
  { id: "accounts", label: "账号管理", icon: "user" },
  { id: "hotel", label: "酒店信息", icon: "building" },
  { id: "local", label: "本机配置", icon: "settings" },
  { id: "ota", label: "OTA渠道", icon: "link" },
  { id: "pms", label: "PMS集成", icon: "pms" },
  { id: "permissions", label: "权限设置", icon: "shield" },
  { id: "notify", label: "通知设置", icon: "bell" },
  { id: "appearance", label: "外观设置", icon: "palette" },
];
const roleMap = { admin: "酒店管理员", revenue_manager: "收益经理", approver: "调价审批人", analyst: "运营观察" };
const notifyItems = [
  { key: "rank_drop", title: "排名下跌", desc: "搜索排名明显后移时提醒" },
  { key: "price_inversion", title: "价格倒挂", desc: "我方挂牌价持续高于竞对中位" },
  { key: "conversion_drop", title: "转化异常", desc: "转化率走弱时提醒" },
  { key: "negative_review", title: "差评", desc: "低分评价进入待回复" },
  { key: "competitor_cut", title: "竞对降价", desc: "竞对价格明显下调" },
  { key: "daily_digest", title: "每日摘要", desc: "每日运营数据汇总（仅本机开关）" },
  { key: "write_failed", title: "写入失败", desc: "官方沙盒写入失败、超时或部分成功时提醒" },
  { key: "reconcile_mismatch", title: "对账不一致", desc: "拟写入、回执与策略状态不一致时提醒" },
];

const route = useRoute();
const router = useRouter();
const shell = inject("appShell", { me: {} });
const me = computed(() => shell.me || {});
const section = ref("accounts");
const hotel = ref({});
const rooms = ref([]);
const constraintDraft = ref({});
const savingConstraints = ref(false);
const policy = ref({
  auto_execute_enabled: false,
  auto_execute_max_risk: "low",
  holiday_requires_review: true,
  price_change_human_threshold_pct: 15,
  rollback_policy: "manual",
  write_mode: "sandbox",
});
const savingPolicy = ref(false);
const channels = ref([]);
const productEnabled = ref(true);
const pmsConnections = ref([]);
const users = ref([]);
const roleCards = ref([]);
const permissionMatrix = ref({ roles: [], rows: [] });
const userForm = ref(null);
const savingUser = ref(false);
const notify = ref(loadNotify());
const appearance = ref(loadAppearance());
const saved = ref("");
const error = ref("");

function setSection(id) {
  section.value = id;
  router.replace({ path: "/settings", query: { section: id } });
}
function toggleNotify(key) {
  notify.value = saveNotify({ ...notify.value, [key]: !notify.value[key] });
  saved.value = "通知偏好已保存在本机。";
}
function applyAppear() {
  appearance.value = saveAppearance(appearance.value);
  saved.value = "外观已应用。";
}
function syncConstraints(list) {
  const draft = {};
  for (const room of list || []) {
    draft[room.id] = { min_price: room.min_price, max_price: room.max_price };
  }
  constraintDraft.value = draft;
}

function startCreate() {
  userForm.value = { id: null, username: "", display_name: "", role: "revenue_manager", password: "" };
  saved.value = "";
  error.value = "";
}
function startEdit(u) {
  userForm.value = { id: u.id, username: u.username, display_name: u.display_name, role: u.role, password: "" };
  saved.value = "";
  error.value = "";
}
async function saveUser() {
  savingUser.value = true;
  error.value = "";
  saved.value = "";
  try {
    const form = userForm.value || {};
    if (form.id) {
      const payload = { display_name: form.display_name, role: form.role };
      if (form.password) payload.password = form.password;
      await api.updateUser(form.id, payload);
      saved.value = "账号已更新。";
    } else {
      await api.createUser({ username: form.username, display_name: form.display_name, role: form.role, password: form.password });
      saved.value = "账号已创建。";
    }
    userForm.value = null;
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    savingUser.value = false;
  }
}
async function toggleUser(u, disable) {
  error.value = "";
  saved.value = "";
  try {
    if (disable) await api.disableUser(u.id);
    else await api.enableUser(u.id);
    saved.value = disable ? "账号已停用。" : "账号已启用。";
    await load();
  } catch (e) {
    error.value = e.message;
  }
}
async function saveConstraints() {
  savingConstraints.value = true;
  error.value = "";
  saved.value = "";
  try {
    for (const room of rooms.value) {
      const draft = constraintDraft.value[room.id];
      if (!draft) continue;
      await api.updateRoomConstraints(room.id, {
        min_price: draft.min_price,
        max_price: draft.max_price,
      });
    }
    await load();
    saved.value = "价格约束已保存，仅对之后新生成的策略生效。";
  } catch (e) {
    error.value = e.message;
  } finally {
    savingConstraints.value = false;
  }
}
async function savePolicy() {
  savingPolicy.value = true;
  error.value = "";
  saved.value = "";
  try {
    const res = await api.updateSettings({
      execution_policy: {
        auto_execute_enabled: !!policy.value.auto_execute_enabled,
        auto_execute_max_risk: "low",
        holiday_requires_review: !!policy.value.holiday_requires_review,
        price_change_human_threshold_pct: policy.value.price_change_human_threshold_pct,
        rollback_policy: policy.value.rollback_policy,
        write_mode: policy.value.write_mode || "sandbox",
      },
    });
    policy.value = { ...policy.value, ...(res.execution_policy || {}) };
    saved.value = "执行策略已保存。默认关闭时所有动作仍须人审。";
  } catch (e) {
    error.value = e.message;
  } finally {
    savingPolicy.value = false;
  }
}
async function load() {
  const [s, r, cfg] = await Promise.all([api.summary(), api.rooms(), api.settings()]);
  hotel.value = s.hotel || {};
  rooms.value = r.rooms || [];
  syncConstraints(rooms.value);
  if (cfg.execution_policy) policy.value = { ...policy.value, ...cfg.execution_policy };
  channels.value = cfg.channels || [];
  pmsConnections.value = cfg.pms_connections || [];
  productEnabled.value = cfg.product_enabled !== false;
  permissionMatrix.value = cfg.permission_matrix || { roles: [], rows: [] };
  roleCards.value = cfg.roles || [];
  if (can("manage_users")) {
    try {
      const listed = await api.users();
      users.value = listed.users || [];
    } catch (e) {
      users.value = [];
    }
  } else {
    users.value = [];
  }
}
onMounted(async () => {
  if (sections.some((s) => s.id === route.query.section)) section.value = route.query.section;
  try { await load(); } catch (e) { error.value = e.message; }
});
watch(() => route.query.section, (v) => { if (sections.some((s) => s.id === v)) section.value = v; });
</script>
