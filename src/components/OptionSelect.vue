<script setup lang="ts">
import { computed, ref } from "vue";
import { Check, ChevronDown } from "@lucide/vue";
import { SelectRoot, SelectTrigger, SelectValue, SelectIcon, SelectPortal, SelectContent, SelectViewport, SelectItem, SelectItemText, SelectItemIndicator } from "reka-ui";

export interface OptionSelectItem {
  label: string;
  value: string;
  disabled?: boolean;
}

const props = defineProps<{
  modelValue: string;
  options: readonly OptionSelectItem[];
  label: string;
  disabled?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const open = ref(false);
// Reka reserves an empty value for clearing the selection. Encode every value
// so the MOD default option can keep its public empty-string value.
const encodeValue = (value: string) => `option:${value}`;
const selectedValue = computed(() => encodeValue(props.modelValue));

function update(value: unknown) {
  if (typeof value !== "string") return;
  const option = props.options.find(option => encodeValue(option.value) === value);
  if (option && !option.disabled) emit("update:modelValue", option.value);
}
</script>

<template>
  <SelectRoot v-model:open="open" :model-value="selectedValue" :disabled="props.disabled" @update:model-value="update">
    <SelectTrigger class="option-select-trigger" :class="{ open }" :aria-label="props.label">
      <SelectValue placeholder="请选择"/>
      <SelectIcon><ChevronDown :size="15" class="option-select-chevron"/></SelectIcon>
    </SelectTrigger>
    <SelectPortal>
      <SelectContent class="option-select-menu" position="popper" :side-offset="4" :collision-padding="8" :aria-label="props.label">
        <SelectViewport>
          <SelectItem v-for="option in props.options" :key="option.value" :value="encodeValue(option.value)" :disabled="option.disabled"
            class="option-select-option">
            <SelectItemText>{{ option.label }}</SelectItemText>
            <SelectItemIndicator><Check :size="14"/></SelectItemIndicator>
          </SelectItem>
        </SelectViewport>
      </SelectContent>
    </SelectPortal>
  </SelectRoot>
</template>

<style>
.option-select-trigger { display: flex; align-items: center; justify-content: space-between; gap: 10px; width: 100%; min-height: 34px; padding: 0 10px; border: 1px solid var(--border); border-radius: 2px; background: transparent; color: var(--text); font-size: 12px; text-align: left; }
.option-select-trigger:hover { border-color: var(--border-strong); }
.option-select-trigger.open { border-color: var(--text); }
.option-select-chevron { display: block; flex: 0 0 auto; color: var(--text-tertiary); transition: transform 140ms ease; }
.option-select-trigger.open .option-select-chevron { transform: rotate(180deg); }
.option-select-menu { z-index: 120; min-width: var(--reka-select-trigger-width); max-height: min(250px, var(--reka-select-content-available-height)); overflow: auto; padding: 4px; border: 1px solid var(--border-strong); background: var(--surface); box-shadow: var(--shadow); }
.option-select-option { display: flex; align-items: center; justify-content: space-between; gap: 10px; min-height: 30px; padding: 5px 8px; border-radius: 1px; color: var(--text-secondary); font-size: 12px; text-align: left; outline: none; cursor: pointer; }
.option-select-option[data-highlighted], .option-select-option[data-state="checked"] { background: var(--surface-hover); color: var(--text); }
.option-select-option[data-disabled] { opacity: .45; cursor: not-allowed; }
.option-select-option svg { color: var(--accent); flex: 0 0 auto; }
</style>
