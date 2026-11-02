# 荒野中枢项目文档

- 版本：1.0.5（Bate）
- 产品规划：[新项目规划](../new-product-plan.md)
- 当前状态：[当前状态](../docs/current-status.md)

本目录保存仍然有效的产品边界、接口契约、前后端实现说明和功能设计。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [接口契约](api-contract.md) | target、DTO、命令、任务、revision、幂等、错误和刷新范围 |
| [前端实施](frontend-implementation.md) | 路由、页面、组件、状态归属、请求隔离、草稿和交互 |
| [后端实施](backend-implementation.md) | Rust、SQLite、SSH、manifest、Compose、锁、任务、备份和 MOD |
| [MOD 管理设计](mod-management-plan.md) | Workshop 获取、固定产物、配置解析、更新和移除 |
| [世界配置设计](world-config-plan.md) | 世界选项、渲染、应用和验证 |
| [后续功能](next-features.md) | 尚未完成的真实功能与完成条件 |

## 冻结约束

1. Windows/macOS 桌面端；Vue 3 + TypeScript + Vite + Tauri 2 + Rust。
2. 远端仅使用 Docker Compose 路线。
3. 实例业务必须携带完整 target，不能根据当前选中对象寻址。
4. 查询、写入、任务和草稿绑定主机版本与请求身份。
5. 密码、Token、SSH 密钥不得进入 SQLite、草稿、日志、事件或诊断。
6. 远端任务由主机 controller 守护；页面切换不能终止任务。
7. 不引入第二套视觉组件库，不恢复阶段说明性常驻文案。

## 版本

版本号只在 [package.json](../package.json) 维护。Tauri 从该文件读取应用版本，界面从 bootstrap snapshot 获取运行时版本。`npm run version:sync` 负责同步 `src-tauri/Cargo.toml`。

接口字段、枚举或命令变化必须同步修改前端类型、Rust DTO、调用点、fixture 和契约文档。
