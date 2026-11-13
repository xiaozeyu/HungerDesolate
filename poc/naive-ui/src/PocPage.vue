<script setup lang="ts">
import { computed, h, ref, watch } from "vue";
import {
  NButton,
  NCheckbox,
  NConfigProvider,
  NDataTable,
  NInput,
  NModal,
  NSelect,
  NSpin,
  createDiscreteApi,
  darkTheme,
  lightTheme,
  type DataTableColumns,
  type SelectOption,
} from "naive-ui";
import { ArrowRight, ServerCog, Trash2 } from "@lucide/vue";
import AppDialog from "../../../src/components/AppDialog.vue";
import StatusLabel from "../../../src/components/StatusLabel.vue";
import { buildThemeOverrides } from "./theme";

type Tone = "success" | "warning" | "danger" | "muted";

interface Row {
  id: string;
  name: string;
  mode: string;
  caves: string;
  tone: Tone;
  status: string;
  players: string;
  build: string;
  synced: string;
  manifest: boolean;
}

const rows: Row[] = [
  { id: "a", name: "生存一服", mode: "生存", caves: "启用", tone: "success", status: "运行中", players: "4 / 6", build: "681465", synced: "2026-11-13 14:22", manifest: true },
  { id: "b", name: "洞穴挑战", mode: "生存", caves: "启用", tone: "warning", status: "未核实", players: "未知 / 6", build: "未知", synced: "—", manifest: true },
  { id: "c", name: "测试服", mode: "荒野", caves: "关闭", tone: "muted", status: "已停止", players: "—", build: "681465", synced: "2026-11-12 09:10", manifest: true },
  { id: "d", name: "长期档", mode: "无尽", caves: "启用", tone: "danger", status: "配置异常", players: "未知 / 8", build: "681230", synced: "2026-11-10 18:45", manifest: false },
  { id: "e", name: "朋友联机", mode: "生存", caves: "关闭", tone: "success", status: "运行中", players: "2 / 4", build: "681465", synced: "2026-11-13 15:01", manifest: true },
];
const rowKey = (row: Row) => row.id;

const dark = ref(localStorage.getItem("fh-theme") === "dark");
watch(dark, value => {
  document.documentElement.dataset.theme = value ? "dark" : "light";
  localStorage.setItem("fh-theme", value ? "dark" : "light");
}, { immediate: true });

const naiveTheme = computed(() => (dark.value ? darkTheme : lightTheme));
const themeOverrides = computed(() => buildThemeOverrides(dark.value));

const { message, notification, loadingBar } = createDiscreteApi(["message", "notification", "loadingBar"], {
  configProviderProps: computed(() => ({ theme: naiveTheme.value, themeOverrides: themeOverrides.value })),
  messageProviderProps: { placement: "top-right", max: 3 },
  notificationProviderProps: { placement: "top-right", max: 3 },
});

const columns = computed<DataTableColumns<Row>>(() => [
  {
    title: "名称",
    key: "name",
    render: row => h("div", null, [
      h("div", { class: "table-primary" }, [h(ServerCog, { size: 17 }), h("strong", row.name)]),
      row.manifest ? null : h("small", { class: "table-sub" }, "配置未完成"),
    ]),
  },
  { title: "模式", key: "mode" },
  { title: "洞穴", key: "caves" },
  { title: "状态", key: "status", render: row => h(StatusLabel, { tone: row.tone, label: row.status }) },
  { title: "玩家", key: "players" },
  { title: "版本", key: "build", render: row => h("span", { class: "mono" }, row.build) },
  { title: "最近运行采集", key: "synced", render: row => h("span", { class: "muted-text" }, row.synced) },
  {
    title: "",
    key: "actions",
    align: "right",
    render: row => h("div", { class: "row-actions" }, [
      h("button", {
        class: "icon-button",
        type: "button",
        title: "预览删除专服",
        "aria-label": `预览删除专服 ${row.name}`,
      }, [h(Trash2, { size: 15 })]),
      h("button", { class: "text-button", type: "button" }, ["进入 ", h(ArrowRight, { size: 15 })]),
    ]),
  },
]);

const tableLoading = ref(false);
const spinLoading = ref(false);
const appDialogOpen = ref(false);
const naiveDialogOpen = ref(false);
const formName = ref("生存一服");
const formRelease = ref("release-681465");
const formCaves = ref(true);
const formAutostart = ref(true);
const releaseOptions: SelectOption[] = [
  { label: "681465 · 2026-11-10", value: "release-681465" },
  { label: "681230 · 2026-10-28", value: "release-681230" },
];

function simulateTableLoad() {
  if (tableLoading.value) return;
  tableLoading.value = true;
  window.setTimeout(() => { tableLoading.value = false; }, 10000);
}
function simulateSpin() {
  if (spinLoading.value) return;
  spinLoading.value = true;
  window.setTimeout(() => { spinLoading.value = false; }, 10000);
}
function showSuccess() { message.success("专服已创建"); }
function showError() { message.error("删除失败：容器仍在运行", { duration: 5000 }); }
function showNotification() {
  notification.info({ title: "任务完成", content: "构建 DST 镜像已完成，用时 4 分 12 秒。", duration: 4500 });
}
function startLoadingBar() {
  loadingBar.start();
  window.setTimeout(() => loadingBar.finish(), 1200);
}
</script>

<template>
  <NConfigProvider :theme="naiveTheme" :theme-overrides="themeOverrides">
    <div class="poc-page naive-poc">
      <header class="poc-header">
        <div>
          <div class="eyebrow">POC / Naive UI</div>
          <h1>Naive UI 主题适配</h1>
          <p class="page-description">同一份数据：现有原生表格 vs Naive DataTable；现有 AppDialog vs NModal。</p>
        </div>
        <div class="poc-actions">
          <button class="button" type="button" @click="dark = !dark">{{ dark ? "切换亮色" : "切换暗色" }}</button>
          <button class="button" type="button" @click="appDialogOpen = true">现有弹窗</button>
          <button class="button primary" type="button" @click="naiveDialogOpen = true">Naive 弹窗</button>
        </div>
      </header>

      <section class="poc-section">
        <div class="section-heading">
          <div><h2>消息与通知</h2><p>Naive 的 message / notification 使用独立宿主，位置可配。</p></div>
        </div>
        <div class="poc-actions">
          <NButton size="small" @click="showSuccess">成功消息</NButton>
          <NButton size="small" @click="showError">错误消息</NButton>
          <NButton size="small" @click="showNotification">通知</NButton>
          <NButton size="small" @click="startLoadingBar">顶部加载条</NButton>
        </div>
        <div class="poc-inline-compare">
          <p class="success-message" role="status">现有内联成功：专服已创建</p>
          <p class="error-strip" role="alert">现有内联错误：删除失败：容器仍在运行</p>
        </div>
      </section>

      <section class="poc-section">
        <div class="section-heading">
          <div><h2>表格对照</h2><p>左：现有原生 table；右：Naive DataTable，保留相同的自定义单元格。</p></div>
          <NButton size="small" @click="simulateTableLoad">模拟加载</NButton>
        </div>
        <div class="poc-compare">
          <div class="poc-compare-pane">
            <h3>现有原生表格</h3>
            <div class="table-wrap">
              <table>
                <thead><tr><th>名称</th><th>模式</th><th>洞穴</th><th>状态</th><th>玩家</th><th>版本</th><th>最近运行采集</th><th/></tr></thead>
                <tbody>
                  <tr v-for="row in rows" :key="row.id">
                    <td><div class="table-primary"><ServerCog :size="17"/><strong>{{ row.name }}</strong></div><small v-if="!row.manifest" class="table-sub">配置未完成</small></td>
                    <td>{{ row.mode }}</td>
                    <td>{{ row.caves }}</td>
                    <td><StatusLabel :tone="row.tone" :label="row.status"/></td>
                    <td>{{ row.players }}</td>
                    <td class="mono">{{ row.build }}</td>
                    <td class="muted-text">{{ row.synced }}</td>
                    <td class="table-action"><div class="row-actions"><button class="icon-button" type="button" title="预览删除专服" :aria-label="`预览删除专服 ${row.name}`"><Trash2 :size="15"/></button><button class="text-button" type="button">进入 <ArrowRight :size="15"/></button></div></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="poc-compare-pane">
            <h3>Naive DataTable</h3>
            <div class="table-wrap">
              <NDataTable :columns="columns" :data="rows" :bordered="false" :single-line="true" size="medium" :loading="tableLoading" :row-key="rowKey"/>
            </div>
          </div>
        </div>
      </section>

      <section class="poc-section">
        <div class="section-heading">
          <div><h2>加载态对照</h2><p>左：现有文本空态；右：Naive Spin。</p></div>
          <NButton size="small" @click="simulateSpin">模拟 Spin</NButton>
        </div>
        <div class="poc-compare">
          <div class="poc-compare-pane">
            <h3>现有文本加载</h3>
            <div class="empty-state" role="status"><h2>正在读取备份</h2></div>
          </div>
          <div class="poc-compare-pane">
            <h3>Naive Spin</h3>
            <NSpin :show="spinLoading" description="读取中…">
              <div class="poc-spin-box">内容区域</div>
            </NSpin>
          </div>
        </div>
      </section>

      <section class="poc-section">
        <div class="section-heading">
          <div><h2>表单控件</h2><p>NInput / NSelect / NCheckbox 的圆角、字号和边框。</p></div>
        </div>
        <div class="poc-form-grid">
          <label>专服名称<NInput v-model:value="formName" placeholder="生存一服"/></label>
          <label>DST Release<NSelect v-model:value="formRelease" :options="releaseOptions" placeholder="选择 Release"/></label>
          <label class="poc-check"><NCheckbox v-model:checked="formCaves"/>启用 Caves 分片</label>
          <label class="poc-check"><NCheckbox v-model:checked="formAutostart"/>主机启动时自动拉起容器</label>
        </div>
      </section>

      <AppDialog :open="appDialogOpen" title="新建专服" description="现有 Reka 弹窗" @update:open="appDialogOpen = $event">
        <div class="poc-form-grid">
          <label>专服名称<input v-model="formName" autocomplete="off"/></label>
          <label>DST Release<select v-model="formRelease"><option v-for="option in releaseOptions" :key="String(option.value)" :value="option.value">{{ option.label }}</option></select></label>
          <label class="poc-check"><input v-model="formCaves" type="checkbox"/>启用 Caves 分片</label>
          <label class="poc-check"><input v-model="formAutostart" type="checkbox"/>主机启动时自动拉起容器</label>
        </div>
        <template #footer>
          <button class="button" type="button" @click="appDialogOpen = false">取消</button>
          <button class="button primary" type="button">创建专服</button>
        </template>
      </AppDialog>

      <NModal v-model:show="naiveDialogOpen" preset="card" :bordered="false" closable aria-label="新建专服" :style="{ width: 'min(680px, calc(100vw - 48px))' }">
        <template #header>
          <div class="eyebrow">专服</div>
          <div class="poc-modal-title">新建专服</div>
          <p class="dialog-description">Naive UI 弹窗</p>
        </template>
        <div class="poc-form-grid">
          <label>专服名称<NInput v-model:value="formName" placeholder="生存一服"/></label>
          <label>DST Release<NSelect v-model:value="formRelease" :options="releaseOptions" placeholder="选择 Release"/></label>
          <label class="poc-check"><NCheckbox v-model:checked="formCaves"/>启用 Caves 分片</label>
          <label class="poc-check"><NCheckbox v-model:checked="formAutostart"/>主机启动时自动拉起容器</label>
        </div>
        <template #footer>
          <div class="dialog-footer-actions">
            <NButton @click="naiveDialogOpen = false">取消</NButton>
            <NButton type="primary">创建专服</NButton>
          </div>
        </template>
      </NModal>
    </div>
  </NConfigProvider>
</template>
