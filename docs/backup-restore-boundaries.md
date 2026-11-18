# 备份与恢复文件边界

- 适用版本：1.3.8
- 依据：`remote-scripts/actions/backup-task.py`、`backup-archive.py`、`restore-task.py`、`mods-apply.py`
- 状态：已按下方边界实现

## 1. 备份内容

DST 以 `-persistent_storage_root /srv/data -conf_dir config -cluster Cluster_1` 启动，真实世界存档位于实例的 `config/Cluster_1/<Master|Caves>/save/`。备份只采集这个 `save` 子树：

```text
实际来源（主机路径）：
  config/Cluster_1/Master/save/
  config/Cluster_1/Caves/save/（启用洞穴时）

归档内逻辑路径：
  data/Master/save/
  data/Caves/save/
```

归档路径是逻辑路径，采集时由 `config/Cluster_1/<分片>/save` 映射到 `data/<分片>/save`；恢复时再写回 `config/Cluster_1/<分片>/save`。

不进入归档：

```text
config/Cluster_1/cluster.ini、cluster_token.txt
config/Cluster_1/adminlist.txt、whitelist.txt、blocklist.txt
config/Cluster_1/<分片>/server.ini、worldgenoverride.lua、modoverrides.lua
active-release.json
manifest.json
compose.yaml
desired/world.json、deployed-world.json
desired/mods.json、deployed-mods.json
mods/、desired-secrets/
data/（Agreements 等非存档文件）
运行日志和临时文件
```

排除的文件名固定为：

```text
cluster_token.txt
adminlist.txt
blocklist.txt
whitelist.txt
server_log.txt
server_chat_log.txt
client_log.txt
hungerhub_bridge_state
hungerhub_bridge_command
hungerhub_bridge_result
```

metadata 与归档分开保存，记录身份、采集模式、时间、checksum、大小、`dstBuild` 和 MOD 引用。旧 metadata 中的 `dstReleaseId`、`modsReleaseId`、`configRevision` 只作历史信息，不参与恢复门禁。

导入（仅限同一 host + 同一 instance）沿用同一归档边界：归档 manifest 的 `hostId`/`instanceId` 必须匹配，`inspect_archive` 通过且归档包含世界存档后才重建 metadata 并原子发布。导入 metadata 的 `dstBuild` 为 null、`mods` 为空，恢复时按“信息未知”处理，不伪造版本或 MOD 引用。备注写入 `metadata.reason`，只改 metadata、不重写归档；下载后再导入会回退到归档 manifest 中的创建时备注。

## 2. 恢复行为

恢复只替换世界存档，始终使用当前活动 Release：

1. 校验备份归属、metadata、checksum 和归档结构，并确认归档包含 `data/<分片>/save` 世界存档；
2. 停分片（按需创建世界存档保护点）；
3. 解压归档并把 `data/<分片>/save` 提交回 `config/Cluster_1/<分片>/save`；
4. 按当前运行状态重建/启动容器并验证进程、端口和版本；
5. 不改 `active-release.json`、`manifest.json`、`compose.yaml`、`config/Cluster_1` 中除 `save` 以外的配置、世界配置、MOD、访问策略或凭据。

规则：

- 不校验、不切换备份记录的 Release；备份不能用来切换 DST 版本。
- 若备份 `dstBuild` 高于当前 build，恢复阻断 `BACKUP_BUILD_NEWER_THAN_INSTANCE`；低于或未知时允许恢复。
- MOD 与当前实例不同时返回 `modsWarnings`，允许继续；不自动改写当前 MOD。
- 恢复前保护点只含世界存档，同样可在当前 Release 上恢复。
- 归档缺少 `data/<分片>/save`（例如旧版本只采集到 `data/<分片>/Agreements`）时，列表把 `restorability` 标为 `missingArtifacts`，恢复预检阻断 `BACKUP_WORLD_DATA_MISSING`。

## 3. 旧归档兼容

历史归档可能包含 `config/Cluster_1`、`active-release.json`、`manifest.json`、`compose.yaml` 或 `desired/deployed` 文件。

- 恢复只提取 `data/<分片>/save`；旧版本的 `data/<分片>` 布局仅在包含 `session/`、`index` 或 `clock` 时按存档识别并迁移到 `config/Cluster_1/<分片>/save`；
- 只有 `data/<分片>/Agreements/...`、没有上述存档标记的旧归档不视为世界存档，恢复被阻止；
- 不读取旧归档中的 Release 身份，不触发版本切换；
- 旧 metadata 中的 `dstReleaseId`、`modsReleaseId`、`configRevision` 只作历史信息。

## 4. 不备份、不恢复

服务器信息：

- Release、manifest、Compose、端口和拓扑；
- `config/Cluster_1` 中除 `<分片>/save` 以外的配置、世界配置和 MOD 配置；
- 实例 registry、任务、锁和日志。

秘密与访问策略：

- Klei Token、服务器密码、`cluster_key`；
- 管理员名单、白名单、黑名单、`whitelistSlots`。

恢复不会覆盖当前实例的这些值和文件。

## 5. 内部保护点

- 恢复和 DST 更新创建的保护点是用户可见备份，按 §1 的世界存档边界保存。
- MOD 应用的完整回滚快照存放在 `mod-apply-tasks/<taskId>/protection/`，不再写入用户备份目录；它属于任务事务，不进入备份列表。删除专服时会清理该实例的保护点归档。

## 6. 运行权限

恢复提交后、创建容器前，世界存档递归归还 DST UID/GID 1000：

```text
config/Cluster_1/Master/save/    UID 1000, 目录 0700、普通文件 0600
config/Cluster_1/Caves/save/     UID 1000, 目录 0700、普通文件 0600
```

`config/Cluster_1` 的其余文件、MOD Release 和凭据不由恢复流程改写，因此保持当前实例的权限和内容。

## 7. 验证

```text
python3 scripts/test-backup-archive.py
python3 scripts/test-backup-task.py
python3 scripts/test-backup-worker.py
python3 scripts/test-instance-backups.py
python3 scripts/test-restore-task.py
python3 scripts/test-restore-mods.py
python3 scripts/test-mods-apply.py
```

覆盖：

- 归档只含 `config/Cluster_1/<分片>/save` 世界存档且排除日志；
- 旧归档只提取可识别的世界存档；
- 缺少世界存档的旧归档不可恢复；
- 恢复不切换 Release、不改 manifest/Compose/配置；
- 备份 build 高于当前 build 时阻断；
- MOD 差异只警告；
- MOD 应用保护点存于任务目录。
