# 错误文案审计与术语转换

- 版本：1.3.8
- 日期：2026-11-09；更新：2026-11-17
- 状态：部分实施，持续按页面接入
- 范围：桌面端主机、专服、世界、MOD、备份、Release、任务面板的用户可见错误
- 不在范围：诊断页原始日志、构建日志正文、任务 journal；这些保留技术信息，供排查

## 1. 结论

当前多数页面通过 `describeError` 或 `TaskSnapshot.error` 直接显示后端 `message`，部分文案仍包含实现术语：

- `release catalog`、`manifest`、`checksum`、`revision`、`artifactDigest`、`staging`
- `hostId`、`instanceId`、`operationId`、`schemaVersion`
- `SFTP`、`Docker engine`、`Compose`、`desired/deployed`

这些词对管理员不直观。建议保留**错误码和原始日志**在诊断/任务详情中，普通页面只显示：

1. 发生了什么（用户能理解的结果）
2. 是否修改了远端
3. 下一步做什么

## 2. 页面审计

| 页面 | 现有技术表达示例 | 用户问题 | 建议展示 |
| --- | --- | --- | --- |
| Release 管理 | “远端 release catalog 无效，准备或验证 DST release 后重试” | catalog/release 不是用户语言 | “无法读取主机上的版本清单；刷新后仍失败，请重新准备或验证 DST 版本” |
| Release 管理 | 构建失败时直接显示日志最后一行，如 `FAMINE_BUILD_FAILED:1`、`Dockerfile:...` | 无法判断失败阶段和下一步 | “镜像构建在「下载并校验 DST 服务端」阶段失败；展开构建日志查看原因” |
| Release 管理 | `imageDigest`、`artifactDigest` | 不知道是镜像指纹/产物 | “镜像指纹”“镜像产物” |
| 主机环境 | `SFTP failed`、`HOST_IDENTITY_READ_FAILED`、`Docker engine unavailable` | 不知道是文件传输、身份读取还是容器服务 | “无法读取主机文件”“无法读取主机身份”“容器服务不可用” |
| 主机环境 | `Compose version missing`、`UID`、`/var/lib/... permission` | 不知道缺什么、谁能处理 | “容器编排未安装”“运行用户权限不足”“管理目录不可写” |
| 专服列表 | “远端注册表格式无效”“实例 manifest 缺失/无效” | 注册表/manifest 是内部结构 | “专服登记信息异常”“专服清单缺失或损坏” |
| 专服列表 | 直接显示 `hostId`/`instanceId`/`registryRevision` | 无法对应到具体专服 | 显示专服名称；编号仅在详情中展示 |
| 主机列表/连接设置 | `OPERATION_ID_REUSED`、`SSH_AUTH_KIND_MISMATCH`、`HOST_IDENTITY_READ_FAILED` | 错误码和协议术语 | “该操作已用于其他修改”“认证方式与保存的凭据不一致”“无法读取主机身份” |
| 备份 | `checksum`、`restorability`、`missingArtifacts`、`captureMode` | 不知道备份是否可靠、能否恢复 | “完整性校验”“可恢复性”“缺少恢复所需版本”“冷备/准热备” |
| 备份 | 恢复失败直接显示 `BACKUP_RESTORE_*` 或归档内部错误 | 不知道世界有没有被改动 | “恢复未完成；当前世界未替换/已回滚”+ 下一步 |
| 主机 MOD 库 | `artifactDigest`、`sourceHandle`、`workshop metadata`、`staging` | 不知道 MOD 文件是否已准备好 | “MOD 文件版本”“创意工坊信息”“暂存目录” |
| 专服 MOD | `desiredDigest`、`schemaBindings`、`modinfoSha256`、`modApply` | 不知道配置是否已应用 | “保存的 MOD 配置”“配置项校验”“应用 MOD” |
| 玩家 | `PLAYER_BRIDGE_LAYOUT_UNSUPPORTED`、`PLAYER_STATE_INVALID`、`PLAYER_BRIDGE_OVERRIDES_UNMANAGED` | 不知道为何不能启用或没有数据 | “当前实例布局不支持玩家桥接”“玩家状态暂不可用”“现有 MOD 覆盖文件不是应用生成的格式” |
| 世界配置 | `revision conflict`、`desired/deployed`、`digest` | 不知道哪份配置生效 | “配置已在其他窗口更新”“已保存/已应用”“配置指纹” |
| 概览 | `blocker` 代码、`processOnly`、`verificationLevel` | 不知道阻断含义和验证程度 | “待处理项”“仅确认进程，尚未验证玩家可连接” |
| 专服创建 | `CREATE_BLOCKED details.blockers`、`COMPOSE_DIAGNOSTIC` | 需要自己翻译大写下划线代码 | 显示白名单中文阻断项；诊断代码放入详情 |
| 任务面板/任务抽屉 | 错误码以 mono 字体直接显示，如 `BACKUP_TASK_FAILED` | 普通用户被错误码干扰 | 默认显示友好描述；错误码放“技术详情”折叠区 |
| 诊断页 | 日志、错误码、异常类型 | 诊断场景需要技术信息 | 保留原文，但明确标注“诊断信息”；不承担普通错误解释 |

## 3. 术语转换表

| 当前术语 | 建议用户术语 | 备注 |
| --- | --- | --- |
| release / DST Release | DST 版本 / 版本 | 页面标题可保留 Release，正文用“版本” |
| release catalog | 版本清单 | 不再出现 catalog |
| manifest | 专服清单 | 实例 manifest 对应专服清单 |
| registry | 专服登记 | 主机级实例登记 |
| artifact / artifactDigest | 镜像产物 / 镜像指纹 | MOD 场景用“MOD 文件版本” |
| imageDigest | 镜像指纹 | 不显示完整 hash，除非详情 |
| revision / configRevision | 配置版本 | revision conflict 用“配置已更新” |
| checksum | 完整性校验 | 不直接说 checksum |
| desired / deployed | 已保存 / 已应用 | 世界、MOD 通用 |
| staging | 暂存目录 | 只在需要路径时显示 |
| preflight | 前置检查 | 任务阶段使用 |
| restore | 恢复 | 明确“恢复世界”或“恢复备份” |
| hostId | 主机身份 | 详情中可显示原值 |
| instanceId | 专服编号 | 列表不显示，详情可显示 |
| operationId / taskId | 操作编号 | 只在任务详情/诊断显示 |
| schemaVersion | 数据版本 | 通常不对用户展示 |
| Compose | 容器编排 | 主机环境技术区可保留 Compose |
| SFTP | 文件传输 | 面向用户的错误说明 |
| Docker engine | 容器服务 | 技术区可保留 Docker |
| blocker | 待处理项 | 阻断项需说明后果 |
| captureMode cold/quasiHot | 冷备 / 准热备 | 准热备注明不保证跨文件一致 |

## 4. 实施建议

1. 在 `src/lib/commandError.ts` 增加按 `error.code` 的友好文案映射，保留原 `message` 到 `details.technicalMessage`，诊断页可读取。
2. 普通页面只渲染友好 `message` 和 `recoverySteps`；`error.code` 从主界面移除，放入“技术详情”折叠区。
3. `TaskPanel`/`TaskDrawer` 默认显示友好摘要，技术错误码仅在展开后显示。
4. 构建失败摘要不直接使用日志最后一行；显示失败阶段和“展开构建日志查看原因”，原始日志继续保留。
5. 诊断页、备份 metadata 详情、Release 详情保留原始字段，不作为普通页面主文案。

建议优先级：

- P0：Release 管理、专服列表、主机环境、备份、专服 MOD
- P1：主机列表/连接设置、世界配置、概览、任务面板、专服创建

## 5. 本轮已同步的文案

- Release catalog 失败：`无法读取主机上的版本清单`
- 恢复建议：`刷新版本列表后重试；仍失败时重新准备或验证 DST 版本`

其余页面按本文术语表逐项接入，不在本轮一次性替换全部后端错误文本，避免丢失诊断信息。
