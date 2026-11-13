import { NButton, createDiscreteApi, darkTheme, lightTheme, zhCN } from "naive-ui";
import { computed, h } from "vue";
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
const { notification: bottomNotification } = createDiscreteApi(["notification"], {
  configProviderProps,
  notificationProviderProps: { placement: "bottom-right", max: 3 },
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
export function notifyBottomSuccess(title: string, content?: string) {
  bottomNotification.success({ title, content, duration: 3500, closable: true, keepAliveOnHover: true });
}
export function notifyBottomError(title: string, content?: string) {
  bottomNotification.error({ title, content, duration: 5000, closable: true, keepAliveOnHover: true });
}
export function notifyBottomInfo(title: string, content?: string) {
  bottomNotification.info({ title, content, duration: 3500, closable: true, keepAliveOnHover: true });
}
export function notifySuccessAction(title: string, content: string | undefined, actionLabel: string, onAction: () => void) {
  notification.success({
    title,
    content: content ?? "",
    duration: 6000,
    keepAliveOnHover: true,
    action: () => h(NButton, { size: "tiny", text: true, type: "primary", onClick: onAction }, () => actionLabel),
  });
}
export { notification };
