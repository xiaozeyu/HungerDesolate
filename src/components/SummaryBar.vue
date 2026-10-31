<script setup lang="ts">
export interface SummaryItem {
  label: string;
  value: string | number;
  detail?: string;
  tone?: "default" | "success" | "warning" | "danger";
}
withDefaults(defineProps<{ items: SummaryItem[]; label?: string }>(), { label: "页面摘要" });
</script>

<template>
  <section class="summary-bar" :aria-label="label">
    <div v-for="item in items" :key="item.label" class="summary-item" :class="`tone-${item.tone ?? 'default'}`">
      <span class="summary-value">{{ item.value }}</span>
      <span class="summary-label">{{ item.label }}</span>
      <small v-if="item.detail" class="summary-detail">{{ item.detail }}</small>
    </div>
  </section>
</template>
