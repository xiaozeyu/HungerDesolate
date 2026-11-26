# 项目开发规范

- Maven 仓库路径：`/opt/homebrew/Cellar/maven/3.9.15/repository`。当前项目采用 Vue/Rust，不使用 Maven。
- 产品名为“荒野中枢”，内部项目名 hunger-desolate，应用标识 `app.hungerdesolate.hub`。
- 远端仅实现 Docker Compose 路线。
- 前端使用 Vue 3、TypeScript、Naive UI、自有 CSS Token 与 `@lucide/vue`；主题映射集中在 `src/naive/theme.ts`，结构覆盖集中在 `src/naive/overrides.css`。
- 界面保持极简：页面标题、表单、选项和页脚不添加常驻流程说明、实现说明或重复帮助文案。保留明确标签、短格式示例、真实状态与条件错误；必要的指纹/删除后果仅在确认时说明。
- 实例业务必须携带完整 target；页面、草稿、任务不能以当前选中对象作为后端寻址依据。
- 所有查询提交（含 error/finally）校验上下文代次和最新请求序号。
- 业务 command 只注册已经实现的功能，未实现远端能力不得返回假成功。
- Rust 阻塞 I/O 进入 spawn_blocking；持有内存锁时禁止 I/O；生产不使用 unwrap/expect/panic/todo。
- 密码、Token、密钥不得进入 SQLite、草稿、日志、事件或诊断；未来凭据功能使用独立 keyring 命名空间。
- 单一实现不创建 trait/工厂/registry；不添加空壳模块或 dead_code 豁免。
- 前端检查：`npm run check`；Rust 检查：fmt/check/test/clippy，clippy 必须 `-D warnings`。
- UI 必须核对亮暗主题和 960×680/1480×920，测试键盘、焦点和 Escape。
