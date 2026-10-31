<script setup lang="ts">
export interface TaskPanelError {
  message?: string;
  code?: string;
}
const props = withDefaults(defineProps<{
  title: string;
  statusLabel: string;
  stageLabel: string;
  tone?: "running" | "success" | "danger" | "warning" | "muted";
  queuePosition?: number | null;
  progress?: string | null;
  error?: TaskPanelError | null;
  cancellable?: boolean;
  cancelLabel?: string;
  busy?: boolean;
}>(), {
  tone: "running",
  queuePosition: null,
  progress: null,
  error: null,
  cancellable: false,
  cancelLabel: "取消任务",
  busy: false,
});
defineEmits<{ cancel: [] }>();
</script>

<template>
  <section class="task-panel" :class="`tone-${props.tone}`" aria-live="polite">
    <span class="task-panel-marker" aria-hidden="true"/>
    <div class="task-panel-copy">
      <span class="task-panel-kicker">任务</span>
      <strong class="task-panel-title">{{ props.title }}</strong>
      <p class="task-panel-stage">
        {{ props.stageLabel }}
        <span v-if="props.queuePosition"> · 排队第 {{ props.queuePosition }} 位</span>
      </p>
      <p v-if="props.error?.message" class="task-panel-error">
        {{ props.error.message }}
        <span v-if="props.error.code" class="mono">{{ props.error.code }}</span>
      </p>
      <slot/>
    </div>
    <div class="task-panel-side">
      <span class="task-panel-status">{{ props.statusLabel }}</span>
      <span v-if="props.progress" class="task-panel-progress mono">{{ props.progress }}</span>
      <button v-if="props.cancellable" class="button subtle" type="button" :disabled="props.busy" @click="$emit('cancel')">
        {{ props.busy ? "提交中…" : props.cancelLabel }}
      </button>
      <slot name="actions"/>
    </div>
  </section>
</template>
