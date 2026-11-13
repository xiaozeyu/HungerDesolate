<script setup lang="ts">
defineProps<{
  eyebrow?: string;
  title: string;
  description?: string;
}>();
</script>

<template>
  <h1 class="visually-hidden">{{ title }}</h1>
  <header v-if="$slots.actions || $slots.status" class="page-toolbar">
    <div class="page-toolbar-leading"><slot name="status"/></div>
    <div class="header-actions"><slot name="actions"/></div>
  </header>
</template>
