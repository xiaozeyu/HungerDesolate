import { computed, shallowRef } from "vue";
import { mergeTask } from "../lib/taskReducer";
import { loadTasks, saveTasks } from "../lib/taskStorage";
import { queryClient } from "../lib/queryClient";
import type { ServerSummary, TaskSnapshot } from "../types/contracts";
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
        const current = tasks.value.get(snapshot.taskId);
        const merged = mergeTask(current, snapshot);
        if (merged === current) return;
        const next = new Map(tasks.value);
        next.set(snapshot.taskId, merged);
        if (terminalStatuses.has(merged.status)) invalidateForTask(merged);
        tasks.value = next;
        saveTasks([...next.values()]);
    }
    function discard(taskId: string) {
        if (!tasks.value.has(taskId)) return;
        const next = new Map(tasks.value);
        next.delete(taskId);
        tasks.value = next;
        saveTasks([...next.values()]);
    }
    function restoreUpdateTargets(servers: ServerSummary[]) {
        let next: Map<string, TaskSnapshot> | undefined;
        for (const task of tasks.value.values()) {
            const target = task.target;
            // Older DST update receipts omitted the connection ID inside the task.
            if (task.operation !== "instanceUpdate" || terminalStatuses.has(task.status)
                || !target || !("instanceId" in target) || !target.hostId || target.serverId !== undefined) continue;
            const matches = servers.filter(server => server.hostId === target.hostId);
            if (matches.length !== 1) continue;
            next ??= new Map(tasks.value);
            next.set(task.taskId, { ...task, target: { ...target, serverId: matches[0].serverId } });
        }
        if (next) {
            tasks.value = next;
            saveTasks([...next.values()]);
        }
    }
    return { tasks: computed(() => [...tasks.value.values()]), receive, discard, restoreUpdateTargets };
}
