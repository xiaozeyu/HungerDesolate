import type { InstanceTarget } from "../types/contracts";
import { shallowRef } from "vue";
export const activeContext = shallowRef<{
    target: InstanceTarget | null;
    epoch: number;
}>({ target: null, epoch: 0 });
export function activateContext(target: InstanceTarget | null): void {
    activeContext.value = { target, epoch: activeContext.value.epoch + 1 };
}
