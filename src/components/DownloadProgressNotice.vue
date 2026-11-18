<script setup lang="ts">
import { NButton, NProgress } from "naive-ui";
import { formatBytes } from "../lib/environmentView";

defineProps<{
  name: string;
  progress: {
    status: "running" | "succeeded" | "interrupted" | "failed";
    bytesTransferred: number;
    sizeBytes: number | null;
    error: string;
  };
}>();
defineEmits<{ retry: []; close: [] }>();
</script>

<template>
  <div class="download-progress">
    <div class="download-progress-file mono" :title="name">{{ name }}</div>
    <NProgress
      type="line"
      :percentage="progress.sizeBytes ? Math.min(100, Math.round(progress.bytesTransferred * 100 / progress.sizeBytes)) : 0"
      :processing="progress.status === 'running'"
      :status="progress.status === 'succeeded' ? 'success' : progress.status === 'running' ? 'default' : 'error'"
      :show-indicator="false"
    />
    <div class="download-progress-meta">
      <span>{{ progress.status === "succeeded" ? "下载完成" : progress.status === "running" ? "正在下载" : "下载中断" }}</span>
      <span>{{ formatBytes(progress.bytesTransferred) }}<template v-if="progress.sizeBytes"> / {{ formatBytes(progress.sizeBytes) }}</template></span>
    </div>
    <p v-if="progress.error" class="download-progress-error">{{ progress.error }}</p>
    <div class="download-progress-actions">
      <NButton v-if="progress.status === 'interrupted' || progress.status === 'failed'" size="tiny" @click="$emit('retry')">重试</NButton>
      <NButton size="tiny" quaternary @click="$emit('close')">关闭</NButton>
    </div>
  </div>
</template>

<style scoped>
.download-progress { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.download-progress-file { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.download-progress-meta { display: flex; justify-content: space-between; gap: 12px; color: var(--text-secondary); font-size: 11px; }
.download-progress-error { color: var(--danger); font-size: 11px; line-height: 1.5; }
.download-progress-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
