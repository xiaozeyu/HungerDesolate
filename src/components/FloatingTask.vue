<script setup lang="ts">
import { ref } from "vue";
import { ChevronDown, FileText } from "@lucide/vue";
import { NCard, NCollapseTransition } from "naive-ui";
import IconButton from "./IconButton.vue";
import { useTaskDrawer } from "../composables/useTaskDrawer";

withDefaults(defineProps<{ label?: string }>(), { label: "任务进度" });
const open = ref(true);
const { openDrawer } = useTaskDrawer();
</script>

<template>
  <NCard class="floating-task" :class="{ collapsed: !open }" size="small" :bordered="true" role="region" aria-label="任务进度">
    <template #header>
      <span class="floating-task-kicker"><span class="floating-task-dot"/>{{ label }}</span>
    </template>
    <template #header-extra>
      <span class="floating-task-actions">
        <IconButton label="打开任务记录" @click="openDrawer"><FileText :size="14"/></IconButton>
        <IconButton :label="open ? '收起任务进度' : '展开任务进度'" @click="open = !open">
          <ChevronDown :size="15" :class="{ rotated: !open }"/>
        </IconButton>
      </span>
    </template>
    <NCollapseTransition :show="open">
      <div class="floating-task-body"><slot/></div>
    </NCollapseTransition>
  </NCard>
</template>
