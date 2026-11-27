import { createDiscreteApi, darkTheme, lightTheme, zhCN } from "naive-ui";
import { computed, h } from "vue";
import type { UserError } from "../lib/commandError";
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
export type ErrorContent = string | Pick<UserError, "message" | "recoverySteps">;

export function errorContent(error?: ErrorContent | null): string {
  if (typeof error === "string") return error.trim();
  return error ? [error.message, ...(error.recoverySteps ?? [])].filter(Boolean).join("\n") : "";
}

export function notifyError(title: string, error?: ErrorContent, duration = 5000) {
  const content = errorContent(error);
  notification.error({
    title: content ? title : () => h("span", { role: "alert" }, title),
    content: content ? () => h("span", { role: "alert" }, content) : undefined,
    duration,
    closable: true,
    keepAliveOnHover: true,
  });
}
export function notifyInfo(title: string, content?: string) {
  notification.info({ title, content, duration: 3500, keepAliveOnHover: true });
}
export { notification };
