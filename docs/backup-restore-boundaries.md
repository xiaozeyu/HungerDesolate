# 备份与恢复文件边界

- 适用版本：1.0.5
- 依据：`remote-scripts/actions/backup-task.py`、`backup-archive.py`、`restore-task.py`
- 状态：边界已按当前实现整理，访问策略和凭据采用“恢复时保留当前值”的规则

## 1. 备份内容

备份根目录为实例目录 `/var/lib/famine-hub/instances/<instanceId>`。

| 路径 | 内容 |
| --- | --- |
| `data/Master/` | Master 世界存档 |
| `data/Caves/` | Caves 世界存档，仅启用洞穴时 |
| `config/Cluster_1/` | 世界配置，见下节 |
| `active-release.json` | 当前活动 Release |
| `manifest.json` | 实例身份、端口、拓扑和配置版本 |
| `compose.yaml` | Compose 定义 |
| `desired/mods.json` | 期望 MOD 状态 |
| `deployed-mods.json` | 已部署 MOD 状态 |
| `desired/world.json` | 期望世界配置 |
| `deployed-world.json` | 已部署世界配置 |

## 2. config 文件边界

### 备份并恢复

- `config/Cluster_1/cluster.ini` 中的非敏感、非访问策略字段
- `config/Cluster_1/Master/server.ini`
- `config/Cluster_1/Caves/server.ini`（启用洞穴时）
- `config/Cluster_1/Master/worldgenoverride.lua`
- `config/Cluster_1/Caves/worldgenoverride.lua`
- 其他不属于排除名单的普通配置

### 不进入归档

```text
cluster_token.txt
adminlist.txt
whitelist.txt
blocklist.txt
desired-secrets/
mods/prepared/
mods/releases/
host.json
registry.json
tasks/
operations/
staging/
locks/
日志
```

### 备份时清洗

`cluster.ini` 写入归档前会移除：

- `NETWORK.cluster_password`
- `SHARD.cluster_key`
- `GAMEPLAY.whitelist_slots`

`desired/world.json` 和 `deployed-world.json` 写入归档前会把访问策略字段重置为中性值：

- `adminList = []`
- `whitelist = []`
- `blocklist = []`
- `whitelistSlots = 0`

`deployed-world.json` 的 `digest` 会同步重算，保证归档自身可验证。

因此备份不再包含管理员名单、白名单、黑名单、服务器密码或 cluster key。

## 3. 恢复时保留当前值

恢复不是简单覆盖。以下内容使用恢复前实例的当前值：

- `adminlist.txt`
- `whitelist.txt`
- `blocklist.txt`
- `settings.advanced.adminList`
- `settings.advanced.whitelist`
- `settings.advanced.blocklist`
- `settings.advanced.whitelistSlots`
- `cluster.ini` 的 `cluster_password`
- `cluster.ini` 的 `cluster_key`
- `cluster_token.txt`
- Token 与服务器密码引用
- 活动 Release 的可恢复性校验使用当前 catalog

恢复流程会：

1. 保留当前玩家名单文件；
2. 把当前访问策略合并到恢复后的 desired/deployed world settings；
3. 重算 deployed world digest；
4. 把当前密码、cluster key、whitelist slots 写回恢复后的 `cluster.ini`；
5. 从当前 `desired-secrets` 重建 `cluster_token.txt`；
6. 从主机当前 MOD 库重建实例 MOD Release；
7. 校验活动 Release 仍存在并已验证；
8. 恢复世界数据和其余配置；
9. 根据恢复前运行状态创建/启动容器。

## 4. 恢复不负责恢复的内容

- 主机注册表和主机身份；
- 主机级 Release catalog、staging 和构建产物；
- 主机 MOD 固定产物；
- 实例 MOD Release 内容；
- 备份文件自身；
- 任务、操作、锁、staging 和日志；
- 其他实例。

## 5. 设计结论

### 属于世界状态，应该备份

- 世界存档；
- 世界生成配置；
- 游戏模式、人数、PVP、暂停等玩法配置；
- 分片端口配置；
- MOD 引用和 MOD 配置；
- Release、manifest、compose 身份快照。

### 属于当前访问策略，不备份、不恢复

- 管理员名单；
- 白名单；
- 黑名单；
- `whitelistSlots`。

这些字段在恢复时保留当前实例的值，避免“恢复备份后管理员被恢复成旧名单”或白名单槽位与名单不一致。

### 属于秘密，不备份、不恢复

- Klei Token；
- 服务器密码；
- `cluster_key`；
- `desired-secrets` 内的凭据。

恢复时从当前实例凭据重新生成。

### 需要产品确认

- `voteEnabled`
- `voteKickPercentage`
- `pauseWhenEmpty`

当前作为世界玩法配置处理；如果以后将它们定义为实例访问策略，应并入“恢复时保留当前值”的集合。

## 6. 旧备份兼容性

本次清洗只作用于新创建的备份。修复前生成的归档可能仍包含旧的 `cluster_password`、`cluster_key`、管理员名单或白名单设置。

- 恢复旧备份时，当前实现仍会用当前访问策略和当前凭据覆盖恢复结果，行为是安全的；
- 但旧归档文件本身仍可能含有敏感内容；
- 建议重新创建备份，并在需要时轮换服务器密码和 cluster key，再删除旧归档。

## 7. 验证

```text
python3 scripts/test-backup-archive.py
python3 scripts/test-backup-task.py
python3 scripts/test-restore-task.py
python3 scripts/test-world-config.py
```

覆盖：

- 备份归档 override 内容与摘要校验；
- `cluster.ini` 敏感字段清洗；
- 世界快照访问策略清洗及 digest 重算；
- 恢复后管理员/白名单/白名单槽位保持当前值；
- 恢复后密码和 cluster key 保持当前值。
