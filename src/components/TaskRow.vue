<script setup lang="ts">
withDefaults(defineProps<{
  title: string;
  target: string;
  stage: string;
  statusLabel: string;
  tone?: "success" | "warning" | "danger" | "muted";
  elapsed: string;
  error?: string | null;
}>(), { tone: "muted", error: null });
</script>

<template>
  <article class="task-row" :class="`tone-${tone}`">
    <span class="task-row-marker" aria-hidden="true"/>
    <div class="task-row-main">
      <span class="task-row-kicker">{{ title }}</span>
      <strong>{{ target }}</strong>
      <p>{{ stage }}</p>
      <small v-if="error">{{ error }}</small>
    </div>
    <div class="task-row-meta">
      <span class="task-row-status">{{ statusLabel }}</span>
      <span class="task-row-time mono">{{ elapsed }}</span>
      <slot name="actions"/>
    </div>
  </article>
</template>
