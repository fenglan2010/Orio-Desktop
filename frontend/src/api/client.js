const TOKEN_KEY = "oiro_token";
const SCOPE_KEY = "oiro_scope";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}
export function getScope() {
  return localStorage.getItem(SCOPE_KEY) || "";
}
export function setToken(token, scope = "hotel") {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
    if (scope) localStorage.setItem(SCOPE_KEY, scope);
  } else {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(SCOPE_KEY);
  }
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401) {
    const scope = getScope();
    setToken("");
    if (!path.includes("/login")) {
      window.location.hash = scope === "ops" ? "#/ops/login" : "#/login";
    }
  }
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!resp.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail : data?.message || resp.statusText;
    throw new Error(msg || "请求失败");
  }
  return data;
}

export const api = {
  login: (username, password) =>
    request("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  me: () => request("/api/auth/me"),
  health: () => request("/api/health"),
  installStatus: () => request("/api/install/status"),
  activateInstall: (install_key) =>
    request("/api/install/activate", { method: "POST", body: JSON.stringify({ install_key }) }),
  localConfig: () => request("/api/local-config"),
  saveLocalModelKeys: (payload) =>
    request("/api/local-config/model-keys", { method: "PUT", body: JSON.stringify(payload) }),
  saveOtaContractPath: (platform, payload) =>
    request(`/api/local-config/ota/${platform}/contract-path`, { method: "PUT", body: JSON.stringify(payload) }),
  savePmsLocal: (kind, payload) =>
    request(`/api/local-config/pms/${kind}`, { method: "PUT", body: JSON.stringify(payload) }),
  summary: () => request("/api/dashboard/summary"),
  timeseries: () => request("/api/dashboard/timeseries"),
  competitors: () => request("/api/dashboard/competitors"),
  rooms: () => request("/api/dashboard/rooms"),
  updateRoomConstraints: (id, payload) =>
    request(`/api/dashboard/rooms/${id}/constraints`, { method: "PUT", body: JSON.stringify(payload) }),
  alerts: () => request("/api/alerts"),
  diagnose: (id) => request(`/api/alerts/${id}/diagnose`, { method: "POST" }),
  diagnoseDate: (date) => request(`/api/diagnosis?date=${encodeURIComponent(date)}`),
  ackAlert: (id) => request(`/api/alerts/${id}/ack`, { method: "POST" }),
  strategies: () => request("/api/strategies"),
  campaigns: () => request("/api/campaigns"),
  generate: (payload = {}) =>
    request("/api/strategies/generate", { method: "POST", body: JSON.stringify(payload) }),
  reviewStrategy: (id, decision, comment = "") =>
    request(`/api/strategies/${id}/review`, { method: "POST", body: JSON.stringify({ decision, comment }) }),
  rollbackStrategy: (id, reason = "") =>
    request(`/api/strategies/${id}/rollback`, { method: "POST", body: JSON.stringify({ reason }) }),
  reviews: () => request("/api/reviews"),
  draftReply: (id) => request(`/api/reviews/${id}/draft`, { method: "POST" }),
  reviewReply: (id, decision, comment = "") =>
    request(`/api/reviews/${id}/review`, { method: "POST", body: JSON.stringify({ decision, comment }) }),
  rollbackReply: (id, comment = "") =>
    request(`/api/reviews/${id}/rollback`, { method: "POST", body: JSON.stringify({ decision: "rollback", comment }) }),
  chatHistory: () => request("/api/chat/history"),
  chat: (message) => request("/api/chat", { method: "POST", body: JSON.stringify({ message }) }),
  memories: (params = "") => request("/api/memories" + (params ? "?" + params : "")),
  memory: (id) => request(`/api/memories/${id}`),
  industryPatterns: (params = "") => request("/api/memories/patterns" + (params ? "?" + params : "")),
  evolution: () => request("/api/evolution"),
  evolutionEngine: () => request("/api/evolution/engine"),
  toggleEvolutionEngine: (enabled) =>
    request("/api/evolution/engine", { method: "POST", body: JSON.stringify({ enabled }) }),
  toggleEvolutionSandbox: (sandbox_mode) =>
    request("/api/evolution/sandbox", { method: "POST", body: JSON.stringify({ sandbox_mode }) }),
  evolutionGraph: () => request("/api/evolution/graph"),
  evolutionRl: () => request("/api/evolution/rl"),
  inventEvolutionStrategies: () => request("/api/evolution/invent", { method: "POST" }),
  audit: () => request("/api/audit"),
  modelCalls: () => request("/api/audit/model-calls"),
  workflow: (id) => request(`/api/audit/workflows/${id}`),
  settings: () => request("/api/settings"),
  updateSettings: (payload) => request("/api/settings", { method: "PUT", body: JSON.stringify(payload) }),
  writes: (params = "") => request("/api/writes" + (params ? "?" + params : "")),
  jobs: (params = "") => request("/api/jobs" + (params ? "?" + params : "")),
  writeJob: (id) => request(`/api/writes/${id}`),
  retryWrite: (id) => request(`/api/writes/${id}/retry`, { method: "POST" }),
  reconcileWrite: (id) => request(`/api/writes/${id}/reconcile`, { method: "POST" }),
  reconcileWrites: () => request("/api/writes/reconcile", { method: "POST" }),
  channels: () => request("/api/channels"),
  channel: (platform) => request(`/api/channels/${platform}`),
  connectChannel: (platform, payload) => request(`/api/channels/${platform}`, { method: "PUT", body: JSON.stringify(payload) }),
  verifyChannel: (platform) => request(`/api/channels/${platform}/verify`, { method: "POST" }),
  disconnectChannel: (platform) => request(`/api/channels/${platform}/disconnect`, { method: "POST" }),
  pms: () => request("/api/pms"),
  pmsConnection: (kind) => request(`/api/pms/${kind}`),
  connectPms: (kind, payload) => request(`/api/pms/${kind}`, { method: "PUT", body: JSON.stringify(payload) }),
  verifyPms: (kind) => request(`/api/pms/${kind}/verify`, { method: "POST" }),
  disconnectPms: (kind) => request(`/api/pms/${kind}/disconnect`, { method: "POST" }),
  opsLogin: (username, password) =>
    request("/api/ops/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  opsMe: () => request("/api/ops/auth/me"),
  opsHotels: () => request("/api/ops/hotels"),
  opsChannels: (hotelId) => request(`/api/ops/hotels/${hotelId}/channels`),
  opsChannel: (hotelId, platform) => request(`/api/ops/hotels/${hotelId}/channels/${platform}`),
  opsValidateContract: (hotelId, platform, payload) =>
    request(`/api/ops/hotels/${hotelId}/channels/${platform}/contract/validate`, { method: "POST", body: JSON.stringify(payload) }),
  opsInstallContract: (hotelId, platform, payload) =>
    request(`/api/ops/hotels/${hotelId}/channels/${platform}/contract`, { method: "PUT", body: JSON.stringify(payload) }),
  opsUninstallContract: (hotelId, platform) =>
    request(`/api/ops/hotels/${hotelId}/channels/${platform}/contract`, { method: "DELETE" }),
  opsWriteFlag: (hotelId, platform, enabled) =>
    request(`/api/ops/hotels/${hotelId}/channels/${platform}/write-flag`, { method: "PUT", body: JSON.stringify({ enabled }) }),
  opsOfficialCredentials: (hotelId, platform, payload) =>
    request(`/api/ops/hotels/${hotelId}/channels/${platform}/official-credentials`, { method: "PUT", body: JSON.stringify(payload) }),
  opsPms: (hotelId) => request(`/api/ops/hotels/${hotelId}/pms`),
  opsPmsConnection: (hotelId, kind) => request(`/api/ops/hotels/${hotelId}/pms/${kind}`),
  opsValidatePmsContract: (hotelId, kind, payload) =>
    request(`/api/ops/hotels/${hotelId}/pms/${kind}/contract/validate`, { method: "POST", body: JSON.stringify(payload) }),
  opsInstallPmsContract: (hotelId, kind, payload) =>
    request(`/api/ops/hotels/${hotelId}/pms/${kind}/contract`, { method: "PUT", body: JSON.stringify(payload) }),
  opsUninstallPmsContract: (hotelId, kind) =>
    request(`/api/ops/hotels/${hotelId}/pms/${kind}/contract`, { method: "DELETE" }),
  opsPmsReadFlag: (hotelId, kind, enabled) =>
    request(`/api/ops/hotels/${hotelId}/pms/${kind}/read-flag`, { method: "PUT", body: JSON.stringify({ enabled }) }),
  opsPmsWriteFlag: (hotelId, kind, enabled) =>
    request(`/api/ops/hotels/${hotelId}/pms/${kind}/write-flag`, { method: "PUT", body: JSON.stringify({ enabled }) }),
  opsPmsOfficialCredentials: (hotelId, kind, payload) =>
    request(`/api/ops/hotels/${hotelId}/pms/${kind}/official-credentials`, { method: "PUT", body: JSON.stringify(payload) }),
  opsRoles: () => request("/api/ops/roles"),
  opsStaff: () => request("/api/ops/staff"),
  opsCreateStaff: (payload) => request("/api/ops/staff", { method: "POST", body: JSON.stringify(payload) }),
  opsUpdateStaff: (id, payload) => request(`/api/ops/staff/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  opsOnboardingList: () => request("/api/ops/onboarding"),
  opsOnboarding: (hotelId) => request(`/api/ops/hotels/${hotelId}/onboarding`),
  opsInitiateOnboarding: (payload) => request("/api/ops/onboarding/initiate", { method: "POST", body: JSON.stringify(payload) }),
  opsStartSigning: (hotelId) => request(`/api/ops/hotels/${hotelId}/onboarding/start-signing`, { method: "POST" }),
  opsSubmitApproval: (hotelId) => request(`/api/ops/hotels/${hotelId}/onboarding/submit-approval`, { method: "POST" }),
  opsApproveOnboarding: (hotelId) => request(`/api/ops/hotels/${hotelId}/onboarding/approve`, { method: "POST" }),
  opsMarkPaymentPending: (hotelId) => request(`/api/ops/hotels/${hotelId}/onboarding/mark-payment-pending`, { method: "POST" }),
  opsConfirmPayment: (hotelId) => request(`/api/ops/hotels/${hotelId}/onboarding/confirm-payment`, { method: "POST" }),
  opsEnableProduct: (hotelId) => request(`/api/ops/hotels/${hotelId}/onboarding/enable-product`, { method: "POST" }),
  opsEsign: (hotelId) => request(`/api/ops/hotels/${hotelId}/esign`),
  opsValidateEsignContract: (hotelId, payload) =>
    request(`/api/ops/hotels/${hotelId}/esign/contract/validate`, { method: "POST", body: JSON.stringify(payload) }),
  opsInstallEsignContract: (hotelId, payload) =>
    request(`/api/ops/hotels/${hotelId}/esign/contract`, { method: "PUT", body: JSON.stringify(payload) }),
  opsUninstallEsignContract: (hotelId) =>
    request(`/api/ops/hotels/${hotelId}/esign/contract`, { method: "DELETE" }),
  opsEsignOfficialCredentials: (hotelId, payload) =>
    request(`/api/ops/hotels/${hotelId}/esign/official-credentials`, { method: "PUT", body: JSON.stringify(payload) }),
  opsEsignEnableFlag: (hotelId, enabled) =>
    request(`/api/ops/hotels/${hotelId}/esign/enable-flag`, { method: "PUT", body: JSON.stringify({ enabled }) }),
  opsWriteFailures: (hotelId) => request(`/api/ops/hotels/${hotelId}/write-failures`),
  opsAudit: (hotelId) => request("/api/ops/audit" + (hotelId ? `?hotel_id=${hotelId}` : "")),
  opsSystem: () => request("/api/ops/system"),
  executeStrategy: (id) => request(`/api/strategies/${id}/execute`, { method: "POST" }),
  executeReply: (id) => request(`/api/reviews/${id}/execute`, { method: "POST" }),
  users: () => request("/api/users"),
  userMatrix: () => request("/api/users/matrix"),
  createUser: (payload) => request("/api/users", { method: "POST", body: JSON.stringify(payload) }),
  updateUser: (id, payload) => request(`/api/users/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  disableUser: (id) => request(`/api/users/${id}/disable`, { method: "POST" }),
  enableUser: (id) => request(`/api/users/${id}/enable`, { method: "POST" }),
};

