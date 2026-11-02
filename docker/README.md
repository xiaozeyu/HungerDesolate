# Docker 实施边界

当前提供受控 release 元数据校验、staging Compose、`docker compose config` 资源计划、受控 network/container 创建、runtime 验证与持久任务。运行时镜像仍需按目标主机实测 Linux amd64、Master/Caves 拓扑、每实例独立 bridge 网络、完整内外同号 UDP 端口、UGC 权限和发布切换。

通过后再新增 runtime/worker Dockerfile 与受控模板，并固定镜像 digest。容器不得 privileged 或挂 Docker socket。不要把社区镜像的启动自动更新行为直接复制为本项目默认行为。

验证记录以实际主机结果为准；目录存在不代表 Docker 已验收。
