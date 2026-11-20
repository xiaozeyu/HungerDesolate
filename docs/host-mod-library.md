# 主机级 MOD 库

- 更新时间：2026-11-17
- 状态：主机级 MOD 内容库、实例 MOD 应用和 UGC 首次启动加载已接入；真实主机验收继续跟进

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
    config/Cluster_1/<Master|Caves>/modoverrides.lua
    mods/releases/<modsReleaseId>/
      dedicated_server_mods_setup.lua
      <Master|Caves>/
        appworkshop_322330.acf
        content/322330/<workshopId>/
```

主库内容由 root 管理，内容目录只读；实例应用时复制到实例 Release，并设置运行 UID。

## 2.1 UGC 运行布局

实例的每个分片目录直接挂载为容器内 `/srv/ugc`，启动参数为：

```text
-ugc_directory /srv/ugc -skip_update_server_mods
```

- DST v2 加载器按 `content/322330/<workshopId>` 读取 MOD；
- `appworkshop_322330.acf` 是 Steam 的已安装 Workshop 索引，跳过启动更新时必须存在，否则引擎枚举不到 MOD；
- 新应用会直接生成索引；旧 Release 在启动预检时把 `steamapps/workshop/content/322330` 迁移到规范路径并补齐索引；
- 升级前没有“跳过更新”的 Compose 会在启动预检中识别并重建，避免漂移报错。

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

## 5. 删除限制与自动清理

删除主机 MOD 版本前只检查各实例 `desired/mods.json`：尚未应用的期望版本仍需要主机库内容作为应用源。已部署副本、活动 MOD Release 和备份不阻止删除——实例运行使用自己的 Release 副本，恢复时从主机库最新内容重建。

仍被期望引用的版本返回 `HOST_MOD_IN_USE`，不会删除。

主机 MOD 准备/更新任务成功后自动清理旧版本，不需要用户手动操作：

- 每个 Workshop 保留最新版本；
- 本次任务刚产出的版本始终保留；
- 被实例期望引用的版本保留；
- 其余旧版本在任务 `cleanup` 阶段删除；
- 更新失败不清理；清理失败不影响更新结果，下次更新会重试。

单个版本仍可通过现有删除操作处理。

删除专服时：

- `instances/<instanceId>` 内的活动/历史 MOD Release、索引、`modoverrides.lua` 和期望/已部署快照随实例目录删除；
- 该实例的用户备份、退役目录和 MOD 应用/DST 更新保护点归档一并清理；
- Compose 项目网络按项目名补充识别并删除；
- 主机库 `mods/prepared` 是共享内容，不随实例删除；最新版本按设计保留，未被引用的旧版本在下一次主机库准备/更新时回收。

## 6. 备份策略

备份只记录实例的 MOD 引用，不重复保存 MOD 内容。

- metadata 记录 `workshopId + artifactDigest`；
- 恢复读取备份中的期望列表与配置，选择主机库当前最新内容；
- 从主机库重建实例 MOD Release、`modoverrides.lua` 和活动 Release；
- 主机库缺失对应 MOD 时返回 `modsWarnings`，提示可能导致世界异常，但不阻止恢复；
- 主机库版本比备份新时只展示版本差异与风险，不阻断恢复。

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

## 8. 当前扩展能力

- 添加 MOD 支持 Workshop 链接/ID 和 Steam 合集。
- 合集内容通过 `GetCollectionDetails` 读取，再按每批 100 个获取元数据。
- 主机库支持检查更新：比较 Steam `contentHandle` 与固定产物 `sourceHandle`。
- 支持单行检查和批量检查；只对可更新项执行批量更新。
- 批量更新按每批最多 50 个创建持久任务，任务进入全局任务记录。
- 专服 MOD 页面支持从主机库多选加入。
- 复用相同内容摘要时更新 manifest 的名称、作者、预览图和来源 handle。
- 图片 URL 带内容摘要缓存参数，版本变化后刷新预览图。
- `modinfo.lua` 的数字选项描述会转换为文本；解析失败时保留已读取的元数据，并回退主机库名称/版本。
- 主机库列表会取回每个 MOD 最新版本的 `modinfo.lua`，由桌面端沙箱 Lua 求值（locale=zh）生成名称/作者/版本；函数、条件表达式和表索引不再显示为配置项名称。
- 实例 MOD Release 采用 `content/322330` + `appworkshop_322330.acf`，首次启动直接加载预置内容；旧布局自动迁移。
- 删除专服会清理实例 MOD 数据、保护点归档和 Compose 网络；主机库内容按共享库策略保留。
