<script setup lang="ts">
import { computed } from "vue";
import { NSelect, type SelectOption } from "naive-ui";

const props = defineProps<{
  modelValue: string;
  options: readonly {
    label: string;
    value: string;
  }[];
  label: string;
  placeholder?: string;
  disabled?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();

const selectOptions = computed<SelectOption[]>(() => props.options.map(option => ({ label: option.label, value: option.value })));
function update(value: string | number | null) {
  emit("update:modelValue", value == null ? "" : String(value));
}
</script>

<template>
  <NSelect :value="modelValue || null" :options="selectOptions" :placeholder="placeholder ?? '请选择'" :disabled="disabled"
    :input-props="{ 'aria-label': label }" @update:value="update"/>
</template>
