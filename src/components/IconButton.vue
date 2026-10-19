<script setup lang="ts">
import { TooltipProvider, TooltipRoot, TooltipTrigger, TooltipPortal, TooltipContent } from "reka-ui";
defineProps<{
    label: string;
    disabled?: boolean;
}>();
defineEmits<{
    click: [
    ];
}>();
</script>

<template>
  <TooltipProvider>
    <TooltipRoot>
      <TooltipTrigger as-child>
        <button class="icon-button" :aria-label="label" :disabled="disabled" @click="$emit('click')">
          <slot/>
        </button>
      </TooltipTrigger>
      <TooltipPortal>
        <TooltipContent class="app-tooltip" :side-offset="4">
          {{label}}
        </TooltipContent>
      </TooltipPortal>
    </TooltipRoot>
  </TooltipProvider>
</template>
