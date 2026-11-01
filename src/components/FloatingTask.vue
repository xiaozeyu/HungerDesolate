<script setup lang="ts">
import { ref } from "vue";
import { ChevronDown, FileText } from "@lucide/vue";
import { useTaskDrawer } from "../composables/useTaskDrawer";
withDefaults(defineProps<{ label?: string }>(), { label: "任务进度" });
const open = ref(true);
const { openDrawer } = useTaskDrawer();
</script>

<template>
  <aside class="floating-task" :class="{ collapsed: !open }" aria-label="任务进度">
    <header class="floating-task-header">
      <span class="floating-task-kicker"><span class="floating-task-dot"/>{{ label }}</span>
      <span class="floating-task-actions">
        <button class="floating-task-link" type="button" title="打开任务记录" @click="openDrawer"><FileText :size="14"/></button>
        <button class="icon-button" type="button" :aria-label="open ? '收起任务进度' : '展开任务进度'" :aria-expanded="open" @click="open = !open">
          <ChevronDown :size="15" :class="{ rotated: !open }"/>
        </button>
      </span>
    </header>
    <div v-show="open" class="floating-task-body"><slot/></div>
  </aside>
</template>
