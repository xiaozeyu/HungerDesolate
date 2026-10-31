import { computed, shallowRef } from "vue";
import { mergeTask } from "../lib/taskReducer";
import { loadTasks, saveTasks } from "../lib/taskStorage";
import { queryClient } from "../lib/queryClient";
import type { TaskSnapshot } from "../types/contracts";
const tasks = shallowRef<ReadonlyMap<string, TaskSnapshot>>(new Map(loadTasks().map(task => [task.taskId, task])));
const terminalStatuses = new Set(["succeeded", "failed", "cancelled", "interrupted"]);
function invalidateForTask(task: TaskSnapshot) {
    queryClient.invalidate(`instances:${task.target.serverId}:`);
    if (!("hostId" in task.target)) return;
    queryClient.invalidate(`releases:${task.target.serverId}:${task.target.hostId}:`);
    queryClient.invalidate(`hostmods:${task.target.serverId}:${task.target.hostId}:`);
    queryClient.invalidate(`hostenv:${task.target.serverId}:`);
    if ("instanceId" in task.target) {
        queryClient.invalidate(`overview:${task.target.serverId}:${task.target.hostId}:${task.target.instanceId}:`);
        queryClient.invalidate(`diagnostics:${task.target.serverId}:${task.target.hostId}:${task.target.instanceId}:`);
    }
}
export function useTasks() {
    function receive(snapshot: TaskSnapshot) {
        const next = new Map(tasks.value);
        const merged = mergeTask(next.get(snapshot.taskId), snapshot);
        next.set(snapshot.taskId, merged);
        tasks.value = next;
        saveTasks([...next.values()]);
        if (terminalStatuses.has(merged.status)) invalidateForTask(merged);
    }
    return { tasks: computed(() => [...tasks.value.values()]), receive };
}
