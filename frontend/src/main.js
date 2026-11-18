import { createApp } from "vue";
import App from "./App.vue";
import router from "./router";
import { applyAppearance, loadAppearance } from "./utils/prefs";
import "./styles/main.css";

applyAppearance(loadAppearance());
if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    applyAppearance(loadAppearance());
  });
}

createApp(App).use(router).mount("#app");
