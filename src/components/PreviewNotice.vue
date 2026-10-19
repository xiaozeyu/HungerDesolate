<script setup lang="ts">
import { Info } from "@lucide/vue";
import { useWorkspace } from "../composables/useWorkspace";
const { preview } = useWorkspace();
</script>

<template>
  <div v-if="preview" class="preview-notice" role="status">
    <Info :size="16"/>
    <span>
      演示数据
    </span>
  </div>
</template>
