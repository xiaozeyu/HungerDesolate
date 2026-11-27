<script setup lang="ts">
import { computed } from "vue";
import { NButton } from "naive-ui";
import ErrorNotice from "./ErrorNotice.vue";
import { notifyError } from "../naive/notify";
import type { CommandError } from "../types/contracts";

type TaskPanelError = Pick<CommandError, "message" | "code" | "recoverySteps">;
const props = withDefaults(defineProps<{
  title: string;
  statusLabel: string;
  stageLabel: string;
  tone?: "running" | "success" | "danger" | "warning" | "muted";
  queuePosition?: number | null;
  progress?: string | null;
  error?: TaskPanelError | null;
  errorDetail?: string;
  cancellable?: boolean;
  cancelLabel?: string;
  busy?: boolean;
  dock?: boolean;
}>(), {
  tone: "running",
  queuePosition: null,
  progress: null,
  error: null,
  cancellable: false,
  cancelLabel: "取消任务",
  busy: false,
  dock: false,
});
defineEmits<{ cancel: [] }>();

const details = computed(() => props.error ? {
  message: props.errorDetail || [props.error.message, props.error.code].filter(Boolean).join(" · "),
  recoverySteps: props.error.recoverySteps,
} : null);
</script>

<template>
  <section class="task-panel" :class="`tone-${props.tone}`" aria-live="polite">
    <span class="task-panel-marker" aria-hidden="true"/>
    <div class="task-panel-copy">
      <span v-if="!props.dock" class="task-panel-kicker">任务</span>
      <strong class="task-panel-title">{{ props.title }}</strong>
      <p class="task-panel-stage">
        {{ props.stageLabel }}
        <span v-if="props.queuePosition"> · 排队第 {{ props.queuePosition }} 位</span>
      </p>
      <ErrorNotice :title="`${props.title}未完成`" :error="details"/>
      <slot/>
    </div>
    <div class="task-panel-side">
      <span v-if="!props.dock" class="task-panel-status">{{ props.statusLabel }}</span>
      <span v-if="props.progress" class="task-panel-progress mono">{{ props.progress }}</span>
      <NButton v-if="details" text size="tiny" @click="notifyError(`${props.title}未完成`, details, 0)">错误详情</NButton>
      <NButton v-if="props.cancellable" size="small" :disabled="props.busy" @click="$emit('cancel')">
        {{ props.busy ? "提交中…" : props.cancelLabel }}
      </NButton>
      <slot name="actions"/>
    </div>
  </section>
</template>
