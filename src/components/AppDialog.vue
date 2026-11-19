<script setup lang="ts">
import { computed } from "vue";
import { NModal } from "naive-ui";

const props = withDefaults(defineProps<{
  open: boolean;
  title: string;
  description?: string;
  size?: "sm" | "md" | "lg";
  eyebrow?: string;
  dismissible?: boolean;
}>(), { size: "md", dismissible: true });
const emit = defineEmits<{ "update:open": [value: boolean] }>();

const width = computed(() => props.size === "sm"
  ? "min(440px, calc(100vw - 48px))"
  : props.size === "lg"
    ? "min(920px, calc(100vw - 48px))"
    : "min(680px, calc(100vw - 48px))");
</script>

<template>
  <NModal :show="open" preset="card" :bordered="false" :closable="dismissible" :mask-closable="dismissible" :close-on-esc="dismissible" :aria-label="title" :style="{ width }"
    @update:show="emit('update:open', $event)">
    <template #header>
      <div v-if="eyebrow" class="eyebrow">{{ eyebrow }}</div>
      <div class="dialog-title">{{ title }}</div>
      <p v-if="description" class="dialog-description">{{ description }}</p>
    </template>
    <slot/>
    <template v-if="$slots.footer" #footer><slot name="footer"/></template>
  </NModal>
</template>
