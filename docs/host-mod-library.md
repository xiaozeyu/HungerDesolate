# 主机级 MOD 库

- 更新时间：2026-10-30
- 状态：主机级 MOD 内容库已接入；备份策略仍保持现有实现

## 1. 设计边界

主机级 MOD 库只共享**已下载的 Workshop 内容**，不共享实例的启用状态、配置和部署结果。

| 数据 | 归属 |
| --- | --- |
| Workshop ID、内容摘要、来源 handle、下载时间、大小 | 主机 MOD 库 |
| 下载/更新任务 | 主机 MOD 库 |
| 实例启用哪些 MOD、启用状态、配置项 | 实例 |
| 实例 MOD Release、modoverrides、活动 Release | 实例 |
| 应用、停止、重建、回滚 | 实例 |

## 2. 目录

```text
/var/lib/famine-hub/
  mods/
    prepared/<workshopId>/<contentDigest>/
      manifest.json
      content/
    staging/<taskId>/
  host-mod-tasks/<operationId>/
  instances/<instanceId>/
    desired/mods.json
    deployed-mods.json
    mods/releases/<modsReleaseId>/
```

主库内容由 root 管理，内容目录只读；实例应用时复制到实例 Release，并设置运行 UID。

## 3. 当前流程

### 主机添加 MOD

```text
主机 → MOD 库 → 添加 MOD → 输入 Workshop ID
→ 查询 Workshop 元数据（图片、名称、作者、可用状态）
→ 选择条目并创建主机级准备任务
→ SteamCMD 下载到 mods/prepared
→ 主机库列表可用
```

### 实例选择 MOD

```text
实例 → MOD → 主机库 → 选择版本 → 保存到实例期望
→ 应用
```

实例保存的是 `artifactDigest` 引用，不复制内容；应用时从主机库复制到实例 Release。主机库中存在同一 Workshop 的多个版本时，实例可选择新版本并保存，随后“应用更改”。

## 4. 后端接口

主机级：

- `list_host_mods`
- `delete_host_mod`
- `prepare_host_mods`
- `get_host_mod_task`
- `list_host_mod_tasks`

任务目标使用 `HostTarget { serverId, hostId }`，任务目录为 `host-mod-tasks`。

实例级接口保持：

- `list_instance_mods`
- `save_instance_mods`
- `apply_instance_mods`

`mods-apply` 和 `instance-mods` 只读取主机库。

## 5. 删除限制

删除主机 MOD 版本前会检查：

- 各实例 `desired/mods.json`
- 各实例 `deployed-mods.json`
- 各实例活动 MOD Release manifest

仍被引用的版本返回 `HOST_MOD_IN_USE`，不会删除。备份归档中的引用尚未纳入检查，因为本轮明确不修改备份代码。

## 6. 备份策略

按后续要求，备份将改为引用主机库：

- 备份只记录实例的 MOD 引用，不重复保存 MOD 内容。
- 恢复时检查主机库是否存在对应 MOD。
- 采用主机库当前最新版本，而不是备份中的旧版本。
- 主机库已删除该 MOD 时，恢复前提示用户“MOD 缺失，恢复可能导致世界崩溃”，但不阻止恢复。

实现前需要补齐：

- 备份 metadata 记录 `workshopId + contentDigest/版本`；
- 恢复预检读取主机库最新版本并返回差异；
- 缺失时返回可确认的 warning，而不是直接拒绝；
- 恢复任务使用选定的主机库版本；

关键点：由于备份不再保存活动 MOD Release 内容，恢复不能直接恢复备份中的 MOD Release。恢复流程需要：

1. 读取备份中的实例 MOD 期望列表和配置；
2. 对每个 Workshop 选择主机库最新内容摘要；
3. 从主机库重建实例 MOD Release、`modoverrides.lua` 和活动 Release 引用；
4. 更新 `desired-mods`/`deployed-mods` 到新摘要；
5. 对主机库缺失的 MOD 给出 warning，并允许用户继续；
6. 继续恢复世界存档和运行容器。

如果主机库版本比备份版本新，MOD 配置 schema 或行为可能变化；恢复预检需要展示版本差异和风险，但不能阻止用户。

当前实现已经按上述方向接入：备份不再保存活动 MOD Release 内容，metadata 记录 MOD 引用；恢复读取主机库最新内容并重建 MOD Release，缺失时返回 `modsWarnings`，仍允许恢复。

## 7. 验证

```text
python3 scripts/test-host-mods.py
python3 scripts/test-host-mods-download.py
python3 scripts/test-mods-download.py
python3 scripts/test-mods-apply.py
python3 scripts/test-instance-mods.py
```

前端：`npm run check`  
Rust：`cargo fmt --check`、`cargo clippy --all-targets -- -D warnings`、`cargo test`
