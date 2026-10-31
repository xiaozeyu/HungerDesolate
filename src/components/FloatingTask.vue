<script setup lang="ts">
import { ref } from "vue";
import { ChevronDown, FileText } from "@lucide/vue";
withDefaults(defineProps<{ label?: string }>(), { label: "任务进度" });
const open = ref(true);
</script>

<template>
  <aside class="floating-task" :class="{ collapsed: !open }" aria-label="任务进度">
    <header class="floating-task-header">
      <span class="floating-task-kicker"><span class="floating-task-dot"/>{{ label }}</span>
      <span class="floating-task-actions">
        <RouterLink class="floating-task-link" to="/tasks" title="打开任务记录"><FileText :size="14"/></RouterLink>
        <button class="icon-button" type="button" :aria-label="open ? '收起任务进度' : '展开任务进度'" :aria-expanded="open" @click="open = !open">
          <ChevronDown :size="15" :class="{ rotated: !open }"/>
        </button>
      </span>
    </header>
    <div v-show="open" class="floating-task-body"><slot/></div>
  </aside>
</template>
