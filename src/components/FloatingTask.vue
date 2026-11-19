<script setup lang="ts">
import { computed, onBeforeUnmount, ref, useId, watch } from "vue";
import { ChevronDown, FileText, X } from "@lucide/vue";
import { NCard, NCollapseTransition, NProgress } from "naive-ui";
import IconButton from "./IconButton.vue";
import { useTaskDrawer } from "../composables/useTaskDrawer";

type Tone = "running" | "success" | "danger" | "warning" | "muted";

const props = withDefaults(defineProps<{
  label?: string;
  tone?: Tone;
  summary?: string;
  progress?: number | null;
  dismissible?: boolean;
}>(), {
  label: "任务进度",
  tone: "running",
  summary: "",
  progress: null,
  dismissible: false,
});
const emit = defineEmits<{ dismiss: [] }>();

const open = ref(true);
const bodyId = useId();
const { openDrawer } = useTaskDrawer();
let collapseTimer: ReturnType<typeof setTimeout> | undefined;

function cancelAutoCollapse() {
  if (collapseTimer) clearTimeout(collapseTimer);
  collapseTimer = undefined;
}
function toggle() {
  cancelAutoCollapse();
  open.value = !open.value;
}
watch(() => props.tone, tone => {
  cancelAutoCollapse();
  if (tone === "success") collapseTimer = setTimeout(() => { open.value = false; }, 5000);
}, { immediate: true });
onBeforeUnmount(cancelAutoCollapse);

const progressValue = computed(() => Math.min(100, Math.max(0, Math.round(props.progress ?? 0))));
const progressStatus = computed<"default" | "success" | "error">(() =>
  props.tone === "danger" ? "error" : props.tone === "success" ? "success" : "default");
function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && open.value) {
    event.stopPropagation();
    open.value = false;
  }
}
</script>

<template>
  <NCard class="floating-task" :class="[`tone-${props.tone}`, { collapsed: !open }]" size="small" :bordered="true" role="region" :aria-label="props.label" @keydown="onKeydown">
    <template #header>
      <button type="button" class="floating-task-toggle" :aria-expanded="open" :aria-controls="bodyId" @click="toggle">
        <span class="floating-task-dot" aria-hidden="true"/>
        <span class="floating-task-kicker">{{ props.label }}</span>
        <span v-if="props.summary" class="floating-task-summary">{{ props.summary }}</span>
      </button>
    </template>
    <template #header-extra>
      <span class="floating-task-actions">
        <IconButton label="打开任务记录" @click="openDrawer"><FileText :size="14"/></IconButton>
        <IconButton :label="open ? '收起任务进度' : '展开任务进度'" @click="toggle">
          <ChevronDown :size="15" :class="{ rotated: !open }"/>
        </IconButton>
        <IconButton v-if="props.dismissible" label="关闭任务进度" @click="emit('dismiss')"><X :size="15"/></IconButton>
      </span>
    </template>
    <div v-if="props.progress !== null" class="floating-task-progress" role="progressbar" :aria-valuenow="progressValue" aria-valuemin="0" aria-valuemax="100">
      <NProgress type="line" :percentage="progressValue" :show-indicator="false" :height="2" :status="progressStatus"/>
    </div>
    <NCollapseTransition :show="open">
      <div :id="bodyId" class="floating-task-body"><slot/></div>
    </NCollapseTransition>
  </NCard>
</template>
