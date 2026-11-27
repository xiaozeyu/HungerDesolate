<script setup lang="ts">
import { watch } from "vue";
import { errorContent, notifyError, type ErrorContent } from "../naive/notify";

const props = withDefaults(defineProps<{
  title?: string;
  error?: ErrorContent | null;
  pending?: boolean;
}>(), { title: "操作失败", error: null, pending: false });

let last = "";
watch(() => [props.title, errorContent(props.error), props.pending] as const, ([title, content, pending]) => {
  if (pending) return;
  const signature = content ? `${title}\n${content}` : "";
  if (content && signature !== last) notifyError(title, content);
  last = signature;
}, { immediate: true, flush: "post" });
</script>

<template><!-- Naive UI renders the notification outside the page. --></template>
