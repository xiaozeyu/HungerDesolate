import { ref } from "vue";

const open = ref(false);

export function useTaskDrawer() {
  return {
    open,
    openDrawer() { open.value = true; },
    closeDrawer() { open.value = false; },
    toggleDrawer() { open.value = !open.value; },
  };
}
