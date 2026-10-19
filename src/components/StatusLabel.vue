<script setup lang="ts">
withDefaults(defineProps<{
    tone?: "success" | "warning" | "danger" | "muted";
    label: string;
}>(), { tone: "muted" });
</script>

<template>
  <span class="status-label">
    <span class="status-dot" :class="tone" />
    {{ label }}
  </span>
</template>
