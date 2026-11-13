import { createApp } from "vue";
import { isTauri } from "@tauri-apps/api/core";
import App from "./App.vue";
import { router } from "./router";
import { useWorkspace } from "./composables/useWorkspace";
import "./style.css";
import "./naive/overrides.css";

if (!isTauri() && new URLSearchParams(location.search).has("pilot")) useWorkspace().setPreview(true);

createApp(App).use(router).mount("#app");
