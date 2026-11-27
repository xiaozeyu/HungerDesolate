<script setup lang="ts">
import { NButton, NSpin } from "naive-ui";
import { notifyError } from "../naive/notify";

withDefaults(defineProps<{
  title: string;
  target: string;
  stage: string;
  statusLabel: string;
  tone?: "success" | "warning" | "danger" | "muted";
  elapsed: string;
  error?: string | null;
  running?: boolean;
}>(), { tone: "muted", error: null, running: false });
</script>

<template>
  <article class="task-row" :class="`tone-${tone}`">
    <span class="task-row-marker" aria-hidden="true"/>
    <div class="task-row-main">
      <span class="task-row-kicker">{{ title }}</span>
      <strong>{{ target }}</strong>
      <p>{{ stage }}</p>
      <NButton v-if="error" text size="tiny" @click="notifyError(`${title}未完成`, error, 0)">错误详情</NButton>
    </div>
    <div class="task-row-meta">
      <span class="task-row-status"><NSpin v-if="running" size="small"/>{{ statusLabel }}</span>
      <span class="task-row-time mono">{{ elapsed }}</span>
      <slot name="actions"/>
    </div>
  </article>
</template>
