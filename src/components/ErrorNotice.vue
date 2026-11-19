<script setup lang="ts">
import { computed } from "vue";
import type { UserError } from "../lib/commandError";

const props = withDefaults(defineProps<{ error?: UserError | string | null }>(), { error: null });
const message = computed(() => typeof props.error === "string" ? props.error : props.error?.message ?? "");
const steps = computed(() => typeof props.error === "string" || !props.error ? [] : props.error.recoverySteps);
</script>

<template>
  <div v-if="error" class="error-notice" role="alert">
    <strong>{{ message }}</strong>
    <ul v-if="steps.length"><li v-for="step in steps" :key="step">{{ step }}</li></ul>
    <slot/>
  </div>
</template>
