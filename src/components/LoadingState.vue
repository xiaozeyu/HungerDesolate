<script setup lang="ts">
import { NSpin } from "naive-ui";
withDefaults(defineProps<{ description?: string; compact?: boolean }>(), {
  description: "读取中…",
  compact: false,
});
</script>

<template>
  <div class="loading-state" :class="{ compact }" role="status">
    <NSpin size="small" :description="description"/>
  </div>
</template>

<style scoped>
.loading-state { display: grid; place-items: center; min-height: 240px; padding: 32px 0; color: var(--text-secondary); border-top: 1px solid var(--border); font-size: 12px; }
.loading-state.compact { min-height: 120px; padding: 16px 0; border-top: 0; }
</style>
