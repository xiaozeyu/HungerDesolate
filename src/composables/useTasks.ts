import { computed, shallowRef } from "vue";
import { mergeTask } from "../lib/taskReducer";
import type { TaskSnapshot } from "../types/contracts";
const tasks = shallowRef<ReadonlyMap<string, TaskSnapshot>>(new Map());
export function useTasks() {
    function receive(snapshot: TaskSnapshot) {
        const next = new Map(tasks.value);
        next.set(snapshot.taskId, mergeTask(next.get(snapshot.taskId), snapshot));
        tasks.value = next;
    }
    return { tasks: computed(() => [...tasks.value.values()]), receive };
}
