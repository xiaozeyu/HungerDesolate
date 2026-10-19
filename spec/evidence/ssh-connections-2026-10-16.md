# SSH 接入验收记录

- 日期：2026-10-16
- 范围：SSH 信任、系统凭据库接口、只读登录与远端 hostId 发现
- 未执行：真实生产主机连接、远端写入、Docker 安装部署

## 验证内容

前端新增连接上下文/记录版本/请求代次/请求 ID 回归。Rust 覆盖指纹 SHA-256、未信任/变更阻断、挑战过期与归属、单次确认、同公钥保留绑定、替换公钥使凭据失效、hostId 丢失或变化拒绝、候选凭据跟踪、文件锁、凭据重试和删除失败恢复。

最终检查：19 个前端测试、24 个常规 Rust 测试通过；本机 SSH 集成用例在专用脚本中另行运行通过。前端类型/生产构建、Rust fmt/check/test/clippy（-D warnings）和 macOS Tauri debug 编译通过。

凭据工作流测试通过供应商 keyring API 配置测试专用内存 builder，走生产 save/read/cleanup 路径；测试验证数据库不含秘密样本。生产只选择 macOS/Windows 原生系统库，没有内存或明文文件 fallback。该测试不等同系统钥匙串授权交互已验证。

## 本机 SSH 集成

执行 `python3 scripts/test-local-ssh.py`，通过。

脚本以独立 sshd_config、临时 RSA 主机密钥和客户端密钥，在 127.0.0.1 随机高端口启动临时 OpenSSH。测试调用生产 handshake/authenticate/SFTP 路径：无信任和错误信任均在认证前拒绝，正确公钥与临时私钥认证通过，SFTP 身份检查成功返回。服务结束、临时目录清理，无用户 SSH 配置修改。

普通 Rust 测试显式忽略此需要服务的用例；上面的脚本单独运行它。未使用真实密码、生产私钥或生产主机。

## 界面与交互

在浏览器明确开启界面预览，扫描/验证/保存凭据禁用，预览私钥与口令输入禁用。检查 light/dark × 960×680/1480×920：SSH 区域正常滚动，Dialog 正文与固定 Footer 无遮挡。截图在本轮工具中查看，未导出文件。

验证 Escape、关闭按钮、焦点回退及 Shift+Tab 锁定。发现 Tooltip 会消耗第一次 Escape，已在 DialogContent 补充键盘处理并复测一次关闭成功。沿用已有 reduced-motion CSS，未切换操作系统设置进行验证。

## 限制与后续

- 原生系统凭据库授权弹窗、macOS 权限拒绝及 Windows 目标机器行为仍需实机验收。
- 密码与有口令私钥已实现认证分支；本机集成覆盖无口令临时私钥，仍需目标主机认证矩阵。
- DNS 使用系统解析器，TCP 连接预算 10 秒（最多八个地址，单地址最多三秒），SSH 操作 10 秒超时；不承诺系统 DNS 解析具有同样硬截止。
- 当前是登录验证而非持续 SSH 会话；不支持跳板机、agent 或 keyboard-interactive。
- 尚未读取专服 registry、检查 Docker 环境或创建管理根，不能据此放行专服部署。
