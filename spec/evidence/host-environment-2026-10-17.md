# 主机环境检查验收记录

- 日期：2026-10-17
- 范围：已验证 SSH 会话上的只读环境探测、Docker/Compose 检查、资源/权限结果和主机环境页面

## 实现

- 新增 `get_host_environment` command，复用已确认的 SSH 主机公钥、凭据和 hostId；执行后关闭会话，不写远端文件。
- 远端脚本固定在仓库 `remote-scripts/probes/host-environment.sh`，无前端参数插值，固定 PATH，清除 Docker context 环境变量，只读取系统、文件系统、命令版本和 Docker Engine 信息。
- 脚本通过 `FHENV\t1`、Base64 字段和 `FHENV\tEND` 返回结构化结果；Rust 限制总输出/字段大小、拒绝重复/未知字段/非法状态/截断结果。
- Docker 结果区分未安装、权限不足、引擎不可访问和可访问；Compose 主版本小于 2 是阻断。系统架构要求 Linux amd64；部署兼容性保留 warning，直到阶段 0 实验通过。
- 主机环境页面显示系统与资源、Docker、采集时间、阻断/警告/通过结果；详情使用可展开项，缺失资源显示“未知”。响应绑定 serverId、recordVersion、requestId、hostId，切换主机或旧响应不会覆盖当前页面。

## 测试

- Rust 环境解析/结果模型测试 6 项：正常报告、Docker 缺失/权限/引擎失败、Compose 缺失、无效内存/磁盘、错误平台、重复/未知/截断协议、缺少工具。
- `python3 scripts/test-host-environment.py` 通过 8 个隔离 shell fixture：available、missing、permissionDenied、unavailable、badServer、composeMissing、timeoutMissing、invalidRoot，并执行被忽略的 Rust fixture 解析测试。
- `python3 scripts/test-local-ssh.py` 通过：临时 loopback SSH 服务执行生产 SSH handshake/auth/SFTP 路径，环境探测脚本通过已验证会话读取结果。
- 前端测试、类型检查、生产构建、Rust fmt/check/test/clippy（`-D warnings`）通过。
- 界面预览检查了 light/dark × 960×680/1480×920；环境结果按阻断优先排序，可键盘展开恢复步骤。

## 限制

本轮没有在真实生产 Linux 主机上运行 Docker 探测，也没有安装 Docker、修改远端权限或创建管理目录。阶段 0 仍需固定首发发行版、Docker/Compose 版本、磁盘/内存预算，并完成三专服联网/更新/恢复实验。
