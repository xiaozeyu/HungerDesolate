<script setup lang="ts">
import { computed } from "vue";
import { NSelect, type SelectOption } from "naive-ui";

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

const encodeValue = (value: string) => `option:${value}`;
const selectedValue = computed(() => encodeValue(props.modelValue));
const selectOptions = computed<SelectOption[]>(() => props.options.map(option => ({
  label: option.label,
  value: encodeValue(option.value),
  disabled: option.disabled,
})));
function update(value: string | number | null) {
  if (typeof value !== "string") return;
  const option = props.options.find(item => encodeValue(item.value) === value);
  if (option && !option.disabled) emit("update:modelValue", option.value);
}
</script>

<template>
  <NSelect :value="selectedValue" :options="selectOptions" :disabled="disabled"
    :input-props="{ 'aria-label': label }" @update:value="update"/>
</template>
