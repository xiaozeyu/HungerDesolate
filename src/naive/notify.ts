import { createDiscreteApi, darkTheme, lightTheme, zhCN } from "naive-ui";
import { computed } from "vue";
import { useTheme } from "../composables/useTheme";
import { buildThemeOverrides } from "./theme";

const { theme } = useTheme();
const configProviderProps = computed(() => {
  const dark = theme.value === "dark";
  return { theme: dark ? darkTheme : lightTheme, themeOverrides: buildThemeOverrides(dark), locale: zhCN };
});
const { notification } = createDiscreteApi(["notification"], {
  configProviderProps,
  notificationProviderProps: { placement: "top-right", max: 3 },
});

export function notifySuccess(title: string, content?: string) {
  notification.success({ title, content, duration: 3000, keepAliveOnHover: true });
}
export function notifyError(title: string, content?: string) {
  notification.error({ title, content, duration: 5000, keepAliveOnHover: true });
}
export function notifyInfo(title: string, content?: string) {
  notification.info({ title, content, duration: 3500, keepAliveOnHover: true });
}
export { notification };
