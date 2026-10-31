<script setup lang="ts">
defineProps<{
  eyebrow?: string;
  title: string;
  description?: string;
}>();
</script>

<template>
  <header class="page-header">
    <div class="page-heading">
      <div v-if="eyebrow" class="eyebrow">{{ eyebrow }}</div>
      <div class="title-line">
        <h1>{{ title }}</h1>
        <slot name="status"/>
      </div>
      <p v-if="description" class="page-description">{{ description }}</p>
    </div>
    <div class="header-actions">
      <slot name="actions"/>
    </div>
  </header>
</template>
