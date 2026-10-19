<script setup lang="ts">
defineProps<{
    eyebrow?: string;
    title: string;
}>();
</script>

<template>
  <header class="page-header">
    <div>
      <div v-if="eyebrow" class="eyebrow">
        {{ eyebrow }}
      </div>
      <h1>
        {{ title }}
      </h1>
    </div>
    <div class="header-actions">
      <slot name="actions" />
    </div>
  </header>
</template>
