import type { TaskSnapshot } from "../types/contracts";
import { sameTaskTarget } from "./navigationGuard.ts";
const finalStates = new Set(["succeeded", "failed", "cancelled"]);
export function mergeTask(current: TaskSnapshot | undefined, next: TaskSnapshot): TaskSnapshot {
    if (!current)
        return next;
    if (current.taskId !== next.taskId || !sameTaskTarget(current.target, next.target)
        || current.operationId !== next.operationId || next.sequence <= current.sequence
        || finalStates.has(current.status))
        return current;
    return next;
}
