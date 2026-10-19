<script setup lang="ts">
import { DialogRoot, DialogPortal, DialogOverlay, DialogContent, DialogTitle, DialogDescription, DialogClose } from "reka-ui";
import { X } from "@lucide/vue";
defineProps<{open: boolean; title: string; description?: string}>();
const emit = defineEmits<{"update:open": [value: boolean]}>();
</script>
<template>
  <DialogRoot :open="open" @update:open="emit('update:open', $event)">
    <DialogPortal>
      <DialogOverlay class="dialog-overlay"/>
      <DialogContent class="dialog-content" v-bind="description ? {} : { 'aria-describedby': undefined }" @keydown.esc="emit('update:open', false)">
        <header class="dialog-header">
          <div><DialogTitle class="dialog-title">{{title}}</DialogTitle><DialogDescription v-if="description" class="dialog-description">{{description}}</DialogDescription></div>
          <DialogClose as-child><button class="icon-button dialog-close-button" type="button" aria-label="关闭"><X :size="18"/></button></DialogClose>
        </header>
        <div class="dialog-body"><slot/></div>
        <footer v-if="$slots.footer" class="dialog-footer"><slot name="footer"/></footer>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
