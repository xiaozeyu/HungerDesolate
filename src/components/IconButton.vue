<script setup lang="ts">
import { NButton, NTooltip } from "naive-ui";

withDefaults(defineProps<{
  label: string;
  disabled?: boolean;
  tone?: "default" | "danger";
}>(), { disabled: false, tone: "default" });
defineEmits<{ click: [] }>();
</script>

<template>
  <NTooltip>
    <template #trigger>
      <span class="app-icon-button-wrap">
        <NButton quaternary circle class="app-icon-button" :class="{ 'app-icon-button--danger': tone === 'danger' }" :aria-label="label" :disabled="disabled" @click="$emit('click')">
          <slot/>
        </NButton>
      </span>
    </template>
    {{ label }}
  </NTooltip>
</template>

<style scoped>
.app-icon-button-wrap { display: inline-flex; }
</style>
