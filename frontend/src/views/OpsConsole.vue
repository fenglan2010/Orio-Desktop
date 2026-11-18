<template>
  <div class="ops-page">
    <header class="ops-top">
      <div class="brand" style="padding:0;min-height:auto">
        <div class="brand-logo"><Icon name="logo" :size="18" /></div>
        <div class="brand-text">
          <div class="brand-name">OIRO 内部运营后台</div>
          <div class="brand-sub">按岗位分工的工作台</div>
        </div>
      </div>
      <div class="row" style="gap:8px">
        <span class="chip">{{ me.display_name || "内部员工" }} · {{ me.role_label || "" }}</span>
        <button class="btn btn-sm" type="button" @click="logout">退出</button>
      </div>
    </header>

    <p class="muted" style="margin:0 0 16px">
      商务电子合同、官方 OTA / PMS 契约和线上写入是分开的步骤。电子签与支付供应商尚未接通；示例契约会被拒绝，本仓库不编造生产环境 URL。界面会按当前岗位显示可操作区块，权限以服务端为准。
    </p>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="saved" class="notice notice-ok">{{ saved }}</p>

    <div class="row" style="gap:8px;flex-wrap:wrap;margin-bottom:16px">
      <button
        v-for="s in visibleSections"
        :key="s.id"
        class="btn btn-sm"
        :class="{ 'btn-primary': section === s.id }"
        type="button"
        @click="section = s.id"
      >{{ s.label }}</button>
    </div>

    <div v-if="needsHotel && !hotelId" class="empty">请选择酒店。</div>

    <div v-if="needsHotel" class="field" style="max-width:420px;margin-bottom:16px">
      <label>选择酒店</label>
      <select v-model.number="hotelId" :disabled="!hotels.length">
        <option v-for="h in hotels" :key="h.id" :value="h.id">{{ h.name }}（{{ h.city }}）· {{ h.product_status || (h.product_enabled ? "已开通" : "尚未开通") }}</option>
      </select>
    </div>

    <!-- 商务开通 -->
    <template v-if="section === 'onboarding'">
      <div class="card card-pad" style="margin-bottom:16px" v-if="can('initiate_onboarding')">
        <b>发起开通</b>
        <p class="muted" style="margin:6px 0 8px">由运营（兼 BD）发起，并与酒店签署电子合同。管理员不能跳过合同与付款直接开通产品权限。</p>
        <div class="form-grid">
          <div class="field"><label>酒店名称</label><input v-model="initiateForm.name" placeholder="新酒店名称" /></div>
          <div class="field"><label>城市</label><input v-model="initiateForm.city" placeholder="城市" /></div>
        </div>
        <button class="btn btn-primary btn-sm" style="margin-top:8px" type="button" :disabled="busy === 'initiate'" @click="initiateNew">发起开通</button>
      </div>

      <div v-if="onboarding" class="card card-pad" style="margin-bottom:16px">
        <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:8px">
          <div>
            <b>开通进度</b>
            <div class="row" style="margin-top:4px;flex-wrap:wrap;gap:6px">
              <span class="tag" :class="onboarding.product_enabled ? 'success' : ''">{{ onboarding.product_status }}</span>
              <span class="tag">{{ onboarding.onboarding_status_label }}</span>
              <span class="tag">供应商 {{ vendorLabel(onboarding.vendor_state) }}</span>
            </div>
          </div>
        </div>
        <div class="grid-3" style="margin-top:12px">
          <div class="subcard"><div class="muted">当前状态</div><b>{{ onboarding.onboarding_status_label }}</b></div>
          <div class="subcard"><div class="muted">审批进度</div><b>{{ onboarding.approval_level || 0 }} / {{ onboarding.approval_required || 2 }}</b></div>
          <div class="subcard"><div class="muted">发起人</div><b>{{ onboarding.initiated_by || "—" }}</b></div>
        </div>
        <p v-if="onboarding.last_error" class="muted" style="margin:8px 0 0">{{ onboarding.last_error }}</p>
        <p class="muted" style="margin:8px 0 0">流程：运营发起并签署 → 合同岗逐级审批 → 双方合同生效 → 确认付款 → 开通产品权限。官方 OTA / PMS 契约安装是开通之后的另一步，不要和商务电子合同混在一起。</p>
        <div class="row" style="margin-top:12px;flex-wrap:wrap;gap:8px">
          <button v-if="can('initiate_onboarding')" class="btn btn-sm" type="button" :disabled="busy==='onboarding'" @click="runOnboarding('startSigning')">发起签署</button>
          <button v-if="can('initiate_onboarding') || can('review_onboarding')" class="btn btn-sm" type="button" :disabled="busy==='onboarding'" @click="runOnboarding('submitApproval')">提交审批</button>
          <button v-if="can('review_onboarding')" class="btn btn-sm" type="button" :disabled="busy==='onboarding'" @click="runOnboarding('approve')">逐级审批</button>
          <button v-if="can('review_onboarding')" class="btn btn-sm" type="button" :disabled="busy==='onboarding'" @click="runOnboarding('markPayment')">进入待付款</button>
          <button v-if="can('confirm_payment')" class="btn btn-sm" type="button" :disabled="busy==='onboarding'" @click="runOnboarding('confirmPayment')">确认付款</button>
          <button v-if="can('enable_product')" class="btn btn-primary btn-sm" type="button" :disabled="busy==='onboarding'" @click="runOnboarding('enableProduct')">开通产品权限</button>
        </div>
      </div>

      <div v-if="can('install_contracts')" class="card card-pad">
        <b>官方电子合同契约（预留）</b>
        <p class="muted" style="margin:6px 0 8px">以后部署了电子合同供应商，在这里安装正式（非示例）契约、保存官方凭证并打开出站开关即可启用。安装本身不会出站。酒店不能配置地址或密钥。电子签与支付通道均尚未接通。</p>
        <div class="grid-3" style="margin-top:8px">
          <div class="subcard"><div class="muted">契约</div><b>{{ esign.contract_enabled ? "已启用" : "未启用" }}</b></div>
          <div class="subcard"><div class="muted">契约主机</div><b>{{ esign.contract_host || "—" }}</b></div>
          <div class="subcard"><div class="muted">出站开关</div><b>{{ esign.esign_enabled ? "已开" : "未开" }}</b></div>
        </div>
        <div class="row" style="justify-content:space-between;margin:10px 0">
          <div>
            <b>电子合同出站开关</b>
            <div class="muted">默认关闭。打开后仍须已启用正式契约且有凭证，才会在发起签署时出站。</div>
          </div>
          <button type="button" class="toggle" :class="{ on: esign.esign_enabled }" :disabled="busy==='esign'" @click="toggleEsignFlag">
            <i></i>
          </button>
        </div>
        <div class="form-grid">
          <div class="field">
            <label>官方接口 Key</label>
            <input v-model="esignDraft.key" type="password" autocomplete="new-password" :placeholder="esign.has_official_credentials ? '已保存，不回显；留空则保持' : '可选，保存后不回显'" />
          </div>
          <div class="field">
            <label>官方接口 Secret</label>
            <input v-model="esignDraft.secret" type="password" autocomplete="new-password" :placeholder="esign.has_official_credentials ? '已保存，不回显；留空则保持' : '可选，保存后不回显'" />
          </div>
        </div>
        <button class="btn btn-sm" style="margin-top:8px" type="button" :disabled="busy==='esign'" @click="saveEsignCreds">保存官方接口凭证</button>
        <div style="margin-top:12px">
          <label>契约 JSON</label>
          <textarea v-model="esignDraft.contract_json" rows="8" style="width:100%;font-family:ui-monospace,monospace;font-size:12px" placeholder='{"platform":"esign","base_url":"https://…","auth":"header_api_key","actions":{"create_agreement":{"method":"POST","path":"/…"}}}'></textarea>
        </div>
        <div class="row" style="margin-top:8px;gap:8px">
          <input type="file" accept="application/json,.json" @change="onEsignFile" />
          <button class="btn btn-sm" type="button" :disabled="busy==='esign'" @click="validateEsign">校验</button>
          <button class="btn btn-primary btn-sm" type="button" :disabled="busy==='esign'" @click="installEsign">安装</button>
          <button class="btn btn-sm" type="button" :disabled="busy==='esign' || !esign.contract_enabled" @click="uninstallEsign">卸载</button>
        </div>
      </div>
    </template>

    <!-- 酒店状态（只读，含写入失败） -->
    <template v-if="section === 'hotels'">
      <div class="card card-pad" style="margin-bottom:16px">
        <b>连接与开通状态</b>
        <p class="muted" style="margin:6px 0 8px">只读查看本店开通状态、渠道和 PMS 连接。官方密钥与契约主机仅对有权限的岗位显示。</p>
        <div class="grid-3">
          <div class="subcard"><div class="muted">产品权限</div><b>{{ currentHotel.product_status || (currentHotel.product_enabled ? "已开通" : "尚未开通") }}</b></div>
          <div class="subcard"><div class="muted">开通状态</div><b>{{ (onboarding && onboarding.onboarding_status_label) || "—" }}</b></div>
          <div class="subcard"><div class="muted">城市</div><b>{{ currentHotel.city || "—" }}</b></div>
        </div>
      </div>
      <div v-for="ch in channels" :key="ch.platform" class="card card-pad" style="margin-bottom:16px">
        <b>{{ ch.label || ch.platform }}</b>
        <div class="row" style="margin-top:4px;flex-wrap:wrap;gap:6px">
          <StatusBadge :value="ch.status" :map="statusMap" />
          <span class="tag" :class="ch.contract_enabled ? 'success' : ''">契约 {{ ch.contract_enabled ? "已启用" : "未启用" }}</span>
        </div>
        <div v-if="can('request_write_flag')" class="row" style="justify-content:space-between;margin:10px 0">
          <div>
            <b>线上写入开关（双人确认）</b>
            <div class="muted">{{ ch.write_flag_pending ? ("待 " + waitingLabel(ch.write_flag_pending) + " 确认") : "合同与管理员需各确认一次后才会生效。" }}</div>
          </div>
          <button type="button" class="toggle" :class="{ on: ch.write_flag_enabled }" :disabled="busy === ch.platform" @click="toggleWrite(ch)">
            <i></i>
          </button>
        </div>
      </div>
      <div v-for="ch in pmsConnections" :key="ch.kind" class="card card-pad" style="margin-bottom:16px">
        <b>{{ ch.label || (ch.kind === "crs" ? "CRS" : "PMS") }}</b>
        <div class="row" style="margin-top:4px;flex-wrap:wrap;gap:6px">
          <StatusBadge :value="ch.status" :map="statusMap" />
          <span class="tag" :class="ch.contract_enabled ? 'success' : ''">契约 {{ ch.contract_enabled ? "已启用" : "未启用" }}</span>
        </div>
      </div>
      <div class="card card-pad">
        <b>写入失败</b>
        <p class="muted" style="margin:6px 0 8px">只显示失败摘要，不包含拟写入正文或密钥。</p>
        <div v-if="!failures.length" class="muted">暂无写入失败记录。</div>
        <div v-for="f in failures" :key="f.id" class="subcard" style="margin-top:8px">
          <b>{{ f.platform }} · {{ f.action }}</b>
          <div class="muted">{{ f.status }} · 尝试 {{ f.attempt_count }} 次 · {{ f.last_error || f.refuse_reason || "—" }}</div>
        </div>
      </div>
    </template>

    <!-- 官方 OTA -->
    <template v-if="section === 'ota'">
      <p class="muted" style="margin:0 0 16px">为本店安装合作方提供的官方 OTA 契约。酒店只需授权本店账号和密码。安装本身不会出站；示例契约会被拒绝。</p>
      <div v-for="ch in channels" :key="ch.platform" class="card card-pad" style="margin-bottom:16px">
        <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:8px">
          <div>
            <b>{{ ch.label }}</b>
            <div class="row" style="margin-top:4px;flex-wrap:wrap;gap:6px">
              <StatusBadge :value="ch.status" :map="statusMap" />
              <span class="tag" :class="ch.contract_enabled ? 'success' : ''">契约 {{ ch.contract_enabled ? "已启用" : "未启用" }}</span>
              <span class="tag" :class="ch.write_flag_enabled ? 'success' : ''">写入开关 {{ ch.write_flag_enabled ? "已开" : "未开" }}</span>
              <span v-if="ch.live_ready" class="tag success">可 live 写入</span>
            </div>
          </div>
        </div>
        <div class="grid-3" style="margin-top:12px">
          <div class="subcard"><div class="muted">契约来源</div><b>{{ sourceLabel(ch) }}</b></div>
          <div class="subcard"><div class="muted">契约主机</div><b>{{ ch.contract_host || "—" }}</b></div>
          <div class="subcard"><div class="muted">已开放动作</div><b>{{ (ch.action_labels || ch.open_actions || []).join("、") || "—" }}</b></div>
        </div>
        <div class="grid-3" style="margin-top:12px">
          <div class="subcard"><div class="muted">酒店已授权账号</div><b>{{ ch.has_hotel_credentials ? "是" : "否" }}</b></div>
          <div class="subcard"><div class="muted">官方接口凭证</div><b>{{ ch.has_official_credentials ? "已配置（不回显）" : "未配置" }}</b></div>
          <div class="subcard"><div class="muted">最近校验</div><b>{{ ch.last_verified || "—" }}</b></div>
        </div>
        <div class="subcard" style="margin-top:12px;background:var(--surface)">
          <b>写入开关与官方接口凭证</b>
          <p class="muted" style="margin:6px 0 8px">线上写入须合同与管理员双人确认，任何一方不能单独打开。官方接口密钥保存后不再回显。</p>
          <div class="row" style="justify-content:space-between;margin:10px 0">
            <div>
              <b>该渠道写入开关</b>
              <div class="muted">{{ ch.write_flag_pending ? ("待对方确认（" + waitingLabel(ch.write_flag_pending) + "）") : "点击后进入待确认，不会单独出站。" }}</div>
            </div>
            <button type="button" class="toggle" :class="{ on: ch.write_flag_enabled }" :disabled="busy === ch.platform" @click="toggleWrite(ch)">
              <i></i>
            </button>
          </div>
          <div class="form-grid">
            <div class="field">
              <label>官方接口 Key</label>
              <input v-model="drafts[ch.platform].key" type="password" autocomplete="new-password" :placeholder="ch.has_official_credentials ? '已保存，不回显；留空则保持' : '可选，保存后不回显'" />
            </div>
            <div class="field">
              <label>官方接口 Secret</label>
              <input v-model="drafts[ch.platform].secret" type="password" autocomplete="new-password" :placeholder="ch.has_official_credentials ? '已保存，不回显；留空则保持' : '可选，保存后不回显'" />
            </div>
          </div>
          <button class="btn btn-sm" style="margin-top:8px" type="button" :disabled="busy === ch.platform" @click="saveOfficialCreds(ch)">保存官方接口凭证</button>
        </div>
        <div class="subcard" style="margin-top:12px">
          <b>安装官方契约</b>
          <p class="muted" style="margin:6px 0 8px">粘贴或上传合作方提供的契约 JSON，先校验再安装。文档示例（example: true）会被拒绝。</p>
          <label>契约 JSON</label>
          <textarea v-model="drafts[ch.platform].contract_json" rows="8" style="width:100%;font-family:ui-monospace,monospace;font-size:12px" placeholder='{"platform":"ctrip","base_url":"https://…","auth":"header_api_key","actions":{"apply_price":{"method":"POST","path":"/…"}}}'></textarea>
          <div class="row" style="margin-top:8px;gap:8px">
            <input type="file" accept="application/json,.json" @change="onContractFile(ch, $event)" />
            <button class="btn btn-sm" type="button" :disabled="busy === ch.platform" @click="validateContract(ch)">校验</button>
            <button class="btn btn-primary btn-sm" type="button" :disabled="busy === ch.platform" @click="installContract(ch)">安装</button>
            <button class="btn btn-sm" type="button" :disabled="busy === ch.platform || !ch.contract_enabled" @click="uninstallContract(ch)">{{ ch.contract_removable ? "卸载设置契约" : "卸载" }}</button>
          </div>
        </div>
      </div>
    </template>

    <!-- 官方 PMS/CRS -->
    <template v-if="section === 'pms'">
      <p class="muted" style="margin:0 0 16px">为选定酒店安装合作方提供的正式 PMS 或 CRS 契约。这是预留接入，不是已对接某一家 PMS 产品，也不包含轻量 PMS。</p>
      <div v-for="ch in pmsConnections" :key="ch.kind" class="card card-pad" style="margin-bottom:16px">
        <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:8px">
          <div>
            <b>{{ ch.label || (ch.kind === "crs" ? "CRS" : "PMS") }}</b>
            <div class="row" style="margin-top:4px;flex-wrap:wrap;gap:6px">
              <StatusBadge :value="ch.status" :map="statusMap" />
              <span class="tag" :class="ch.contract_enabled ? 'success' : ''">契约 {{ ch.contract_enabled ? "已启用" : "未启用" }}</span>
            </div>
          </div>
        </div>
        <div class="grid-3" style="margin-top:12px">
          <div class="subcard"><div class="muted">契约来源</div><b>{{ sourceLabel(ch) }}</b></div>
          <div class="subcard"><div class="muted">契约主机</div><b>{{ ch.contract_host || "—" }}</b></div>
          <div class="subcard"><div class="muted">已开放动作</div><b>{{ (ch.action_labels || ch.open_actions || []).join("、") || "—" }}</b></div>
        </div>
        <div class="grid-3" style="margin-top:12px">
          <div class="subcard"><div class="muted">酒店已授权账号</div><b>{{ ch.has_hotel_credentials ? "是" : "否" }}</b></div>
          <div class="subcard"><div class="muted">官方接口凭证</div><b>{{ ch.has_official_credentials ? "已配置（不回显）" : "未配置" }}</b></div>
          <div class="subcard"><div class="muted">最近校验</div><b>{{ ch.last_verified || "—" }}</b></div>
        </div>
        <div class="subcard" style="margin-top:12px;background:var(--surface)">
          <b>读取开关与官方接口凭证</b>
          <p class="muted" style="margin:6px 0 8px">读取开关可由合同岗直接配置。线上写入仍须与管理员双人确认。密钥保存后不回显。</p>
          <div class="row" style="justify-content:space-between;margin:10px 0">
            <div>
              <b>读取开关</b>
              <div class="muted">打开后仍须已启用官方契约且有凭证，采集才会出站。</div>
            </div>
            <button type="button" class="toggle" :class="{ on: ch.read_flag_enabled }" :disabled="busy === ch.kind" @click="togglePmsRead(ch)">
              <i></i>
            </button>
          </div>
          <div class="row" style="justify-content:space-between;margin:10px 0">
            <div>
              <b>线上写入开关（双人确认）</b>
              <div class="muted">{{ ch.write_flag_pending ? ("待对方确认（" + waitingLabel(ch.write_flag_pending) + "）") : "任何一方不能单独打开。" }}</div>
            </div>
            <button type="button" class="toggle" :class="{ on: ch.write_flag_enabled }" :disabled="busy === ch.kind" @click="togglePmsWrite(ch)">
              <i></i>
            </button>
          </div>
          <div class="form-grid">
            <div class="field">
              <label>官方接口 Key</label>
              <input v-model="pmsDrafts[ch.kind].key" type="password" autocomplete="new-password" :placeholder="ch.has_official_credentials ? '已保存，不回显；留空则保持' : '可选，保存后不回显'" />
            </div>
            <div class="field">
              <label>官方接口 Secret</label>
              <input v-model="pmsDrafts[ch.kind].secret" type="password" autocomplete="new-password" :placeholder="ch.has_official_credentials ? '已保存，不回显；留空则保持' : '可选，保存后不回显'" />
            </div>
          </div>
          <button class="btn btn-sm" style="margin-top:8px" type="button" :disabled="busy === ch.kind" @click="savePmsOfficialCreds(ch)">保存官方接口凭证</button>
        </div>
        <div class="subcard" style="margin-top:12px">
          <b>安装官方 PMS/CRS 契约</b>
          <p class="muted" style="margin:6px 0 8px">不要填写本仓库编造的生产主机。文档示例会被拒绝。</p>
          <label>契约 JSON</label>
          <textarea v-model="pmsDrafts[ch.kind].contract_json" rows="8" style="width:100%;font-family:ui-monospace,monospace;font-size:12px" placeholder='{"platform":"pms","base_url":"https://…","auth":"header_api_key","actions":{"pull_inventory":{"method":"GET","path":"/…"}}}'></textarea>
          <div class="row" style="margin-top:8px;gap:8px">
            <input type="file" accept="application/json,.json" @change="onPmsContractFile(ch, $event)" />
            <button class="btn btn-sm" type="button" :disabled="busy === ch.kind" @click="validatePmsContract(ch)">校验</button>
            <button class="btn btn-primary btn-sm" type="button" :disabled="busy === ch.kind" @click="installPmsContract(ch)">安装</button>
            <button class="btn btn-sm" type="button" :disabled="busy === ch.kind || !ch.contract_enabled" @click="uninstallPmsContract(ch)">{{ ch.contract_removable ? "卸载设置契约" : "卸载" }}</button>
          </div>
        </div>
      </div>
    </template>

    <!-- 员工 -->
    <template v-if="section === 'staff'">
      <div class="card card-pad" style="margin-bottom:16px">
        <div class="row" style="justify-content:space-between">
          <b>内部员工账号</b>
          <button class="btn btn-primary btn-sm" type="button" @click="startCreateStaff">+ 添加员工</button>
        </div>
        <p class="muted" style="margin:6px 0 8px">只有管理员可以创建、改岗或紧急停用。产品设计和财务计费不是本轮可登录岗位。</p>
        <div class="subcard" v-for="u in staff" :key="u.id" style="margin-top:8px">
          <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:8px">
            <div>
              <b>{{ u.display_name }}</b>
              <span class="muted"> · {{ u.username }}</span>
              <div class="row" style="margin-top:4px;gap:6px">
                <span class="tag success">{{ u.role_label }}</span>
                <span class="tag" :class="u.disabled ? '' : 'success'">{{ u.disabled ? "已停用" : "启用中" }}</span>
              </div>
            </div>
            <div class="row" style="flex-shrink:0">
              <button class="btn btn-sm" type="button" @click="startEditStaff(u)">编辑</button>
              <button v-if="!u.disabled" class="btn btn-sm" type="button" @click="toggleStaff(u, true)">停用</button>
              <button v-else class="btn btn-primary btn-sm" type="button" @click="toggleStaff(u, false)">启用</button>
            </div>
          </div>
        </div>
        <div v-if="staffForm" class="subcard" style="background:var(--primary-soft);margin-top:12px">
          <b>{{ staffForm.id ? "编辑员工" : "添加员工" }}</b>
          <div class="form-grid" style="margin-top:10px">
            <div class="field">
              <label>用户名</label>
              <input v-model="staffForm.username" :disabled="!!staffForm.id" placeholder="3–32 位字母数字" />
            </div>
            <div class="field">
              <label>显示名</label>
              <input v-model="staffForm.display_name" />
            </div>
            <div class="field">
              <label>岗位</label>
              <select v-model="staffForm.role">
                <option value="ops_admin">管理员</option>
                <option value="ops_contract">合同</option>
                <option value="ops_sysops">系统运维</option>
                <option value="ops_operations">运营</option>
                <option value="ops_support">客服</option>
              </select>
            </div>
            <div class="field">
              <label>{{ staffForm.id ? "重置密码（留空则不改）" : "密码" }}</label>
              <input v-model="staffForm.password" type="password" autocomplete="new-password" />
            </div>
          </div>
          <div class="row" style="margin-top:10px">
            <button class="btn btn-primary" type="button" :disabled="busy==='staff'" @click="saveStaff">保存</button>
            <button class="btn btn-sm" type="button" @click="staffForm = null">取消</button>
          </div>
        </div>
      </div>
      <div class="card card-pad" v-if="matrix.rows">
        <b>岗位权限矩阵</b>
        <div class="muted" style="margin:6px 0 8px">权限由服务端强制执行，前端只负责隐藏不可用区块。</div>
        <div style="overflow:auto">
          <table class="data-table" style="width:100%;font-size:12px;border-collapse:collapse">
            <thead>
              <tr>
                <th style="text-align:left;padding:6px">能力</th>
                <th v-for="r in matrix.roles" :key="r.id" style="padding:6px">{{ r.label }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in matrix.rows" :key="row.id">
                <td style="padding:6px">{{ row.label }}</td>
                <td v-for="r in matrix.roles" :key="r.id" style="padding:6px;text-align:center">{{ row.cells[r.id] ? "是" : "—" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <!-- 审计 -->
    <template v-if="section === 'audit'">
      <div class="card card-pad">
        <b>操作审计</b>
        <p class="muted" style="margin:6px 0 8px">审计不含官方密钥或完整契约正文。</p>
        <div v-if="!auditEntries.length" class="muted">暂无记录。</div>
        <div v-for="row in auditEntries" :key="row.id" class="subcard" style="margin-top:8px">
          <b>{{ row.action }}</b>
          <div class="muted">{{ row.actor }} · {{ row.created_at || "" }} · {{ row.reason }}</div>
        </div>
      </div>
    </template>

    <!-- 系统状态 -->
    <template v-if="section === 'system'">
      <div class="card card-pad">
        <b>系统与任务状态</b>
        <p class="muted" style="margin:6px 0 8px">不展示连接串、酒店密码或官方密钥。系统运维岗不能安装商务或 API 契约。</p>
        <div class="grid-3" v-if="systemStatus">
          <div class="subcard"><div class="muted">环境</div><b>{{ systemStatus.env }}</b></div>
          <div class="subcard"><div class="muted">数据库</div><b>{{ systemStatus.database }}</b></div>
          <div class="subcard"><div class="muted">任务队列</div><b>{{ systemStatus.job_queue }}</b></div>
          <div class="subcard"><div class="muted">工作流引擎</div><b>{{ systemStatus.workflow_engine }}</b></div>
          <div class="subcard"><div class="muted">采集间隔</div><b>{{ systemStatus.ingest_interval_seconds }} 秒</b></div>
          <div class="subcard"><div class="muted">境外模型</div><b>{{ systemStatus.gateway && systemStatus.gateway.overseas_enabled ? "已开" : "关闭" }}</b></div>
        </div>
        <div v-if="systemStatus && systemStatus.gateway" style="margin-top:12px">
          <div class="subcard" v-for="ad in systemStatus.gateway.adapters || []" :key="ad.vendor">
            <b>{{ ad.vendor }}</b>
            <div class="muted">{{ ad.role }} · {{ ad.status }}{{ ad.model ? " · " + ad.model : "" }}</div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api, setToken } from "../api/client";
import Icon from "../components/Icon.vue";
import StatusBadge from "../components/StatusBadge.vue";

const router = useRouter();
const me = ref({});
const hotels = ref([]);
const hotelId = ref(0);
const channels = ref([]);
const pmsConnections = ref([]);
const onboarding = ref(null);
const esign = ref({});
const failures = ref([]);
const staff = ref([]);
const matrix = ref({});
const auditEntries = ref([]);
const systemStatus = ref(null);
const drafts = reactive({});
const pmsDrafts = reactive({});
const esignDraft = reactive({ contract_json: "", key: "", secret: "" });
const initiateForm = reactive({ name: "", city: "" });
const staffForm = ref(null);
const busy = ref("");
const error = ref("");
const saved = ref("");
const section = ref("");
const statusMap = {
  disconnected: "未连接",
  credentials_missing: "未连接",
  authorized: "已连接",
  error: "连接异常",
};

const visibleSections = computed(() => me.value.sections || []);
const needsHotel = computed(() => ["onboarding", "hotels", "ota", "pms"].includes(section.value));
const currentHotel = computed(() => hotels.value.find((h) => h.id === hotelId.value) || {});

function can(cap) {
  return !!(me.value.permissions && me.value.permissions[cap]);
}

function sourceLabel(ch) {
  if (!ch || !ch.contract_enabled) return "未配置";
  return { env_file: "服务器文件", settings: "已安装（设置）", none: "未配置" }[ch.contract_source] || ch.contract_source || "—";
}

function vendorLabel(state) {
  return { not_configured: "未配置", waiting_vendor: "等待供应商", ready: "已就绪", disabled: "开关关闭" }[state] || state || "未配置";
}

function waitingLabel(pending) {
  if (!pending) return "";
  return pending.waiting_for === "ops_admin" ? "管理员" : pending.waiting_for === "ops_contract" ? "合同" : (pending.waiting_for || "对方");
}

function ensureDraft(platform) {
  if (!drafts[platform]) drafts[platform] = { contract_json: "", key: "", secret: "" };
}
function ensurePmsDraft(kind) {
  if (!pmsDrafts[kind]) pmsDrafts[kind] = { contract_json: "", key: "", secret: "" };
}

async function loadHotels() {
  const data = await api.opsHotels();
  hotels.value = data.hotels || [];
  if (!hotelId.value && hotels.value.length) hotelId.value = hotels.value[0].id;
}

async function loadChannels() {
  channels.value = [];
  pmsConnections.value = [];
  if (!hotelId.value || (!can("view_channels") && !can("install_contracts"))) return;
  try {
    const data = await api.opsChannels(hotelId.value);
    channels.value = data.channels || [];
    for (const ch of channels.value) ensureDraft(ch.platform);
  } catch (e) {
    if (can("view_channels") || can("install_contracts")) throw e;
  }
  try {
    const pms = await api.opsPms(hotelId.value);
    pmsConnections.value = pms.connections || [];
    for (const ch of pmsConnections.value) ensurePmsDraft(ch.kind);
  } catch (e) {
    if (can("install_contracts")) throw e;
  }
}

async function loadOnboarding() {
  onboarding.value = null;
  esign.value = {};
  if (!hotelId.value || !can("view_onboarding")) return;
  onboarding.value = await api.opsOnboarding(hotelId.value);
  if (can("install_contracts") || can("view_channel_hosts")) {
    try { esign.value = onboarding.value.esign || (await api.opsEsign(hotelId.value)); } catch { esign.value = {}; }
  }
}

async function loadFailures() {
  failures.value = [];
  if (!hotelId.value || !can("view_write_failures")) return;
  const data = await api.opsWriteFailures(hotelId.value);
  failures.value = data.failures || [];
}

async function loadStaff() {
  if (!can("manage_staff")) return;
  const data = await api.opsStaff();
  staff.value = data.staff || [];
  matrix.value = data.matrix || {};
}

async function loadAudit() {
  if (!can("view_audit")) return;
  const data = await api.opsAudit(hotelId.value || undefined);
  auditEntries.value = data.entries || [];
}

async function loadSystem() {
  if (!can("view_system")) return;
  systemStatus.value = await api.opsSystem();
}

async function loadSection() {
  error.value = "";
  if (needsHotel.value) {
    if (!hotels.value.length) await loadHotels();
    await loadChannels();
    if (section.value === "onboarding") await loadOnboarding();
    if (section.value === "hotels") {
      await loadOnboarding();
      await loadFailures();
    }
  }
  if (section.value === "staff") await loadStaff();
  if (section.value === "audit") await loadAudit();
  if (section.value === "system") await loadSystem();
}

async function initiateNew() {
  busy.value = "initiate";
  error.value = "";
  saved.value = "";
  try {
    const res = await api.opsInitiateOnboarding({ name: initiateForm.name, city: initiateForm.city });
    initiateForm.name = "";
    initiateForm.city = "";
    await loadHotels();
    hotelId.value = res.hotel.id;
    onboarding.value = res;
    saved.value = "已发起开通，待签署与审批。付款确认前不会开通产品权限。";
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = "";
  }
}

async function runOnboarding(kind) {
  if (!hotelId.value) return;
  busy.value = "onboarding";
  error.value = "";
  saved.value = "";
  try {
    const fn = {
      startSigning: api.opsStartSigning,
      submitApproval: api.opsSubmitApproval,
      approve: api.opsApproveOnboarding,
      markPayment: api.opsMarkPaymentPending,
      confirmPayment: api.opsConfirmPayment,
      enableProduct: api.opsEnableProduct,
    }[kind];
    onboarding.value = await fn(hotelId.value);
    saved.value = "状态已更新：" + (onboarding.value.onboarding_status_label || "");
    await loadHotels();
  } catch (e) {
    error.value = e.message;
    await loadOnboarding().catch(() => {});
  } finally {
    busy.value = "";
  }
}

async function toggleEsignFlag() {
  busy.value = "esign";
  error.value = "";
  try {
    esign.value = await api.opsEsignEnableFlag(hotelId.value, !esign.value.esign_enabled);
    saved.value = "电子合同出站开关已更新，不会单独出站。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}

async function saveEsignCreds() {
  busy.value = "esign";
  error.value = "";
  try {
    const payload = {};
    if (esignDraft.key) payload.key = esignDraft.key;
    if (esignDraft.secret) payload.secret = esignDraft.secret;
    if (!payload.key && !payload.secret) throw new Error("请输入要保存的官方接口凭证，或留空保持原值");
    esign.value = await api.opsEsignOfficialCredentials(hotelId.value, payload);
    esignDraft.key = "";
    esignDraft.secret = "";
    saved.value = "官方接口凭证已保存，不再回显。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}

async function onEsignFile(ev) {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  esignDraft.contract_json = await file.text();
  saved.value = "已读入 " + file.name + "，请先校验再安装。";
}

function parseEsign() {
  if (!esignDraft.contract_json.trim()) throw new Error("请先粘贴或上传契约 JSON");
  return JSON.parse(esignDraft.contract_json);
}

async function validateEsign() {
  busy.value = "esign";
  error.value = "";
  try {
    const res = await api.opsValidateEsignContract(hotelId.value, parseEsign());
    saved.value = res.message || "契约校验通过，尚未保存，也未出站。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}

async function installEsign() {
  busy.value = "esign";
  error.value = "";
  try {
    esign.value = await api.opsInstallEsignContract(hotelId.value, parseEsign());
    esignDraft.contract_json = "";
    saved.value = esign.value.message || "官方电子合同契约已安装。安装本身不会出站。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}

async function uninstallEsign() {
  busy.value = "esign";
  error.value = "";
  try {
    esign.value = await api.opsUninstallEsignContract(hotelId.value);
    saved.value = esign.value.message || "已卸载官方电子合同契约。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}

function parseDraftContract(ch) {
  const raw = (drafts[ch.platform] && drafts[ch.platform].contract_json) || "";
  if (!raw.trim()) throw new Error("请先粘贴或上传契约 JSON");
  return JSON.parse(raw);
}
async function onContractFile(ch, ev) {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  ensureDraft(ch.platform);
  drafts[ch.platform].contract_json = await file.text();
  saved.value = "已读入 " + file.name + "，请先校验再安装。";
}
async function validateContract(ch) {
  busy.value = ch.platform; error.value = "";
  try {
    const res = await api.opsValidateContract(hotelId.value, ch.platform, parseDraftContract(ch));
    saved.value = res.message || "契约校验通过，尚未保存，也未出站。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}
async function installContract(ch) {
  busy.value = ch.platform; error.value = "";
  try {
    await api.opsInstallContract(hotelId.value, ch.platform, parseDraftContract(ch));
    drafts[ch.platform].contract_json = "";
    await loadChannels();
    saved.value = "官方契约已安装。安装本身不会出站。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}
async function uninstallContract(ch) {
  busy.value = ch.platform; error.value = "";
  try {
    await api.opsUninstallContract(hotelId.value, ch.platform);
    await loadChannels();
    saved.value = "已卸载官方契约。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}
async function toggleWrite(ch) {
  busy.value = ch.platform; error.value = "";
  try {
    const res = await api.opsWriteFlag(hotelId.value, ch.platform, !ch.write_flag_enabled);
    saved.value = res.message || "写入开关已更新。";
    await loadChannels();
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}
async function saveOfficialCreds(ch) {
  busy.value = ch.platform; error.value = "";
  try {
    const payload = {};
    if (drafts[ch.platform].key) payload.key = drafts[ch.platform].key;
    if (drafts[ch.platform].secret) payload.secret = drafts[ch.platform].secret;
    if (!payload.key && !payload.secret) throw new Error("请输入要保存的官方接口凭证，或留空保持原值");
    await api.opsOfficialCredentials(hotelId.value, ch.platform, payload);
    drafts[ch.platform].key = "";
    drafts[ch.platform].secret = "";
    await loadChannels();
    saved.value = "官方接口凭证已保存，不再回显。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}

function parsePmsDraftContract(ch) {
  const raw = (pmsDrafts[ch.kind] && pmsDrafts[ch.kind].contract_json) || "";
  if (!raw.trim()) throw new Error("请先粘贴或上传契约 JSON");
  return JSON.parse(raw);
}
async function onPmsContractFile(ch, ev) {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  ensurePmsDraft(ch.kind);
  pmsDrafts[ch.kind].contract_json = await file.text();
  saved.value = "已读入 " + file.name + "，请先校验再安装。";
}
async function validatePmsContract(ch) {
  busy.value = ch.kind; error.value = "";
  try {
    const res = await api.opsValidatePmsContract(hotelId.value, ch.kind, parsePmsDraftContract(ch));
    saved.value = res.message || "契约校验通过，尚未保存，也未出站。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}
async function installPmsContract(ch) {
  busy.value = ch.kind; error.value = "";
  try {
    await api.opsInstallPmsContract(hotelId.value, ch.kind, parsePmsDraftContract(ch));
    pmsDrafts[ch.kind].contract_json = "";
    await loadChannels();
    saved.value = "官方 PMS/CRS 契约已安装。安装本身不会出站。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}
async function uninstallPmsContract(ch) {
  busy.value = ch.kind; error.value = "";
  try {
    await api.opsUninstallPmsContract(hotelId.value, ch.kind);
    await loadChannels();
    saved.value = "已卸载官方 PMS/CRS 契约。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}
async function togglePmsRead(ch) {
  busy.value = ch.kind; error.value = "";
  try {
    await api.opsPmsReadFlag(hotelId.value, ch.kind, !ch.read_flag_enabled);
    await loadChannels();
    saved.value = "读取开关已更新，不会单独出站。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}
async function togglePmsWrite(ch) {
  busy.value = ch.kind; error.value = "";
  try {
    const res = await api.opsPmsWriteFlag(hotelId.value, ch.kind, !ch.write_flag_enabled);
    saved.value = res.message || "写入开关已更新。";
    await loadChannels();
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}
async function savePmsOfficialCreds(ch) {
  busy.value = ch.kind; error.value = "";
  try {
    const payload = {};
    if (pmsDrafts[ch.kind].key) payload.key = pmsDrafts[ch.kind].key;
    if (pmsDrafts[ch.kind].secret) payload.secret = pmsDrafts[ch.kind].secret;
    if (!payload.key && !payload.secret) throw new Error("请输入要保存的官方接口凭证，或留空则保持原值");
    await api.opsPmsOfficialCredentials(hotelId.value, ch.kind, payload);
    pmsDrafts[ch.kind].key = "";
    pmsDrafts[ch.kind].secret = "";
    await loadChannels();
    saved.value = "官方接口凭证已保存，不再回显。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}

function startCreateStaff() {
  staffForm.value = { username: "", display_name: "", role: "ops_support", password: "" };
}
function startEditStaff(u) {
  staffForm.value = { id: u.id, username: u.username, display_name: u.display_name, role: u.role, password: "" };
}
async function saveStaff() {
  busy.value = "staff"; error.value = "";
  try {
    if (staffForm.value.id) {
      const payload = { display_name: staffForm.value.display_name, role: staffForm.value.role };
      if (staffForm.value.password) payload.password = staffForm.value.password;
      await api.opsUpdateStaff(staffForm.value.id, payload);
    } else {
      await api.opsCreateStaff(staffForm.value);
    }
    staffForm.value = null;
    await loadStaff();
    saved.value = "员工账号已保存。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}
async function toggleStaff(u, disabled) {
  busy.value = "staff"; error.value = "";
  try {
    await api.opsUpdateStaff(u.id, { disabled });
    await loadStaff();
    saved.value = disabled ? "账号已停用。" : "账号已启用。";
  } catch (e) { error.value = e.message; }
  finally { busy.value = ""; }
}

function logout() {
  setToken("");
  router.push("/ops/login");
}

watch(section, () => { loadSection().catch((e) => { error.value = e.message; }); });
watch(hotelId, () => { if (needsHotel.value) loadSection().catch((e) => { error.value = e.message; }); });

onMounted(async () => {
  try {
    me.value = await api.opsMe();
    const first = (me.value.sections && me.value.sections[0] && me.value.sections[0].id) || "hotels";
    if (can("view_hotels") || can("view_onboarding") || can("install_contracts")) {
      await loadHotels();
    }
    section.value = first;
  } catch (e) {
    error.value = e.message;
  }
});
</script>
