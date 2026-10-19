# Docker 实施边界

当前已提供受控 release 元数据校验、staging Compose、`docker compose config` 资源计划、gated network/container 创建和 runtime 验证内核，但尚未提供经过阶段 0 验证的生产镜像或可启动部署流程。阶段 0 需验证 Linux amd64、Master/Caves 拓扑、每实例独立 bridge 网络、完整内外同号 UDP 端口、UGC 权限、发布切换及后台 worker。

通过后再新增 runtime/worker Dockerfile 与受控模板，并固定镜像 digest。容器不得 privileged 或挂 Docker socket。不要把社区镜像的启动自动更新行为直接复制为本项目默认行为。

实验与证据清单见 `spec/implementation-roadmap.md` 的阶段 0；目录存在不代表 Docker 已验收。
