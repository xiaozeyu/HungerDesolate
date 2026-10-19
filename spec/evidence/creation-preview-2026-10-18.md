# 新建专服预览验证

## 实现与边界

- 列表读取成功后开放“新建专服”，分成专服设置和世界设置：名称、启动策略、自动端口策略，以及模式、人数、洞穴、PVP、说明和 Token 输入；创建路线固定为公网。Token 只在表单内存中存在，不进入预览请求。
- Rust command `preview_instance_create` 通过已验证 SSH 执行固定只读 Python 脚本，核对完整目标、连接版本、注册表版本、身份和文件安全，再计算未预留的建议端口。
- 合并 registry/manifest 中既有实例保留端口、IPv4/IPv6 本地 socket 与 Docker HostConfig.PortBindings。关闭洞穴为三项 UDP 端口，开启为六项；内外端口同号。
- 结果始终明确 `allowed=false`、`portReservation=unreserved`，保留部署兼容性门禁。未新增实际创建/启动 command，不把建议实例 ID、目录或预览成功当作实例已创建。

## 自动检查

- Python 10 个隔离场景覆盖无写入、停止实例保留、IPv6、Docker 发布、公网范围耗尽、缺证据、错误身份/版本、symlink、读期间变更、只读文件系统和版本解析；所有 Docker 调用均模拟，元数据位于一次性目录。
- Rust 测试覆盖严格请求 schema、拒绝凭据/路径、名称/人数/模式、协议包络、完整同号端口与部署门禁。
- TypeScript 测试覆盖目标/输入/版本/目录约束、重复或越界端口、错误成功声明、安全阻断文案。
- 生产 composable 测试覆盖输入编辑、重复查询、旧 error/finally、关窗重开、A→B→A、注册表变化和无效字段不调用后端。
- 前端 check、Rust fmt/check/test/clippy（all-targets，-D warnings）通过；原有需独立 Linux/SSH fixture 的跳过项保持原状。

## 界面核对

- 使用隔离 IPC fixture 核对 light/dark × 960×680/1480×920，未连接真实服务器。
- 核对列表入口、专服设置→世界设置两步流程、名称/人数/洞穴、模式方向键+Enter、Token 临时输入、提交中状态、六项/三项端口表、长目录换行与弹窗滚动。
- 返回编辑保留本次参数；Escape 关闭后焦点回到“新建专服”。按钮未提供实际创建执行入口。

## 未覆盖

Docker 安装已获用户成功反馈，但新创建预检尚未在用户 Linux 主机验收。B2 的端口锁、注册写入和 operation 接纳内核已在隔离 fixture 中实现；真实游戏镜像、SteamCMD、LAN 发现、外部玩家连接、双向洞穴迁移、三专服隔离、旧 owner/TOCTOU 实机验证和后台创建恢复继续按阶段 0/B3 推进。
