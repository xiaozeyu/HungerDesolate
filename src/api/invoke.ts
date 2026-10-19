import { invoke, isTauri } from "@tauri-apps/api/core";
export async function call<T>(command: string, request?: unknown): Promise<T> {
    if (!isTauri())
        throw new Error("当前为浏览器界面预览，请通过桌面开发命令使用本地后端");
    return invoke<T>(command, request === undefined ? undefined : { request });
}
