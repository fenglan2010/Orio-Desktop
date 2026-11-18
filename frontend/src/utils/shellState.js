/** Survives AppLayout remounts so the footer does not flash "未知" on navigation. */
export const shellState = {
  me: { display_name: "", role: "", role_label: "", hotel_name: "", city: "", username: "", platform: "", pages: [], permissions: {} },
  health: null,
  summary: null,
  alerts: [],
  strategies: [],
};

export function clearShellMe() {
  Object.assign(shellState.me, {
    display_name: "",
    role: "",
    role_label: "",
    hotel_name: "",
    city: "",
    username: "",
    platform: "",
    pages: [],
    permissions: {},
  });
}
