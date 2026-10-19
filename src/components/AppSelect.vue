<script setup lang="ts">
import { Check, ChevronDown } from "@lucide/vue";
import { SelectRoot, SelectTrigger, SelectValue, SelectIcon, SelectPortal, SelectContent, SelectViewport, SelectItem, SelectItemText, SelectItemIndicator } from "reka-ui";
defineProps<{
    modelValue: string;
    options: readonly {
        label: string;
        value: string;
    }[];
    label: string;
    placeholder?: string;
    disabled?: boolean;
}>();
const emit = defineEmits<{
    "update:modelValue": [
        value: string
    ];
}>();
function update(value: unknown) { if (typeof value === 'string')
    emit('update:modelValue', value); }
</script>

<template>
  <SelectRoot :model-value="modelValue || undefined" :disabled="disabled" @update:model-value="update">
    <SelectTrigger class="app-select-trigger" :aria-label="label">
      <slot name="icon"/>
      <SelectValue :placeholder="placeholder ?? '请选择'"/>
      <SelectIcon>
        <ChevronDown :size="16"/>
      </SelectIcon>
    </SelectTrigger>
    <SelectPortal>
      <SelectContent class="app-select-content" position="popper" :side-offset="4">
        <SelectViewport>
          <SelectItem v-for="option in options" :key="option.value" :value="option.value" class="app-select-item">
            <SelectItemText>
              {{option.label}}
            </SelectItemText>
            <SelectItemIndicator>
              <Check :size="15"/>
            </SelectItemIndicator>
          </SelectItem>
        </SelectViewport>
      </SelectContent>
    </SelectPortal>
  </SelectRoot>
</template>
