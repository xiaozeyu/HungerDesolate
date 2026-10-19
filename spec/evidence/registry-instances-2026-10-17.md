# 注册表与专服列表验收记录

- 日期：2026-10-17
- 范围：registry.json/manifest.json 只读发现、实例身份校验、列表页面和详情入口

## 实现

- 固定读取 `/var/lib/famine-hub/registry.json`；registry entry 的路径只能是受控 `instances/<instanceId>` 或 `retired/<instanceId>`，不接受前端路径。
- 活动实例读取 `instances/<instanceId>/manifest.json`，校验 schema、hostId、instanceId、displayName、configRevision、Compose project、人数上限和洞穴/开机字段。
- SFTP 管理文件读取限制在管理根，拒绝 `..`、绝对路径、符号链接、目录替代文件、超大文件和读取期间 registry 变化。
- 归档实例不显示；创建失败实例允许出现在列表但缺 manifest 时不可进入详情。运行状态、玩家数、实际 build 统一为未知，直到运行探测实现。

## 测试

- Rust 注册表解析测试 5 组，覆盖正常摘要、错误 host、schema、重复 ID、危险路径、manifest 缺失/不匹配、归档/空列表和容量限制。
- 前端列表边界测试覆盖 host/server/recordVersion/requestId、重复实例、同名不同 ID、未知运行状态、创建失败/归档、人数范围和 manifest 可进入条件。
- 临时 SSH/SFTP 集成测试通过：真实读取临时 host.json、registry.json、manifest.json；随后注入损坏 registry、删除 manifest、写入错误 hostId、替换 manifest 为符号链接和路径穿越读取，均返回明确错误。
- 前端 33 项测试、Rust 37 项常规测试通过；Rust clippy `-D warnings`、前端类型检查/构建通过。
- 浏览器预览核对 light/dark × 960×680/1480×920；刷新更新时间，进入测试服后侧栏/面包屑/实例 ID 匹配；无 manifest 的创建失败项“进入”禁用。真实领域数据继续以 SSH/SFTP 集成测试为准。

## 限制

当前 registry/manifest 是只读发现，未创建或修改远端实例。容器/进程/端口/玩家数没有实时采集；列表显示未知。下一步必须在真实 Linux amd64 主机上完成阶段 0 Docker 三专服、联网、更新和恢复验收。
