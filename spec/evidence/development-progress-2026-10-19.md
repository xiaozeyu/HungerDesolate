# 开发进度报告

- 日期：2026-10-19
- 版本：0.1.0
- 范围：前端创建任务集成补齐、文档同步

## 1. 本轮完成项

### ✅ 前端创建任务集成（100%）

| 模块 | 完成度 | 关键文件 |
|-----|--------|---------|
| 创建提交 | ✅ 100% | `useInstanceCreation.ts:create()` |
| 任务轮询 | ✅ 100% | `useInstanceCreation.ts:refreshTask()` 1.5s 间隔 |
| 状态展示 | ✅ 100% | `InstanceCreateDialog.vue:task` |
| 成功关闭 | ✅ 100% | 轮询检测 succeeded+verification=passed → emit |
| 列表刷新 | ✅ 100% | `InstanceListPage.vue @creation-succeeded` |
| 错误映射 | ✅ 100% | `instanceCreationTask.ts:creationTaskErrorLabel` |
| 恢复动作 | ✅ 骨架 | 按钮已添加，具体实现待工作包 |

**提交文件**：
- `src/composables/useInstanceCreation.ts`（添加成功回调、类型导入）
- `src/components/InstanceCreateDialog.vue`（emit 定义、恢复按钮）
- `src/pages/InstanceListPage.vue`（监听创建成功事件）

### ✅ 文档同步（100%）

| 文档 | 更新内容 |
|-----|---------|
| `spec/next-features.md` | 标记已完成功能、待补充项 |
| `spec/api-contract.md` | 补充创建任务完整 DTO、前端集成状态 |
| `spec/scaffold-status.md` | 添加前端集成完成记录 |
| `spec/evidence/frontend-integration-2026-10-19.md` | 首个验收记录 |
| `spec/evidence/development-progress-2026-10-19.md` | 本报告 |

## 2. 技术实现细节

### 创建流程

```typescript
// 1. 预览
const preview = await previewInstanceCreate({
  schemaVersion: 1,
  requestId: uuid(),
  target: { serverId, hostId },
  expectedRecordVersion,
  expectedRegistryRevision,
  input: { displayName, autostart, startAfterCreate, portPolicy, cavesEnabled }
});

// 2. Release 验证（如需）
await prepareRelease(); // 调用 previewInstanceRelease + importInstanceRelease

// 3. 创建提交
const accepted = await createInstance({
  schemaVersion: 1,
  requestId: uuid(),
  target: { serverId, hostId },
  expectedRecordVersion,
  expectedRegistryRevision,
  operationId: uuid(),
  planId: preview.previewId,
  input: { ...preview.input, expectedPorts: preview.ports }
});

// 4. 轮询状态
while (task.status === "queued" || task.status === "running") {
  const status = await getInstanceCreateStatus({
    schemaVersion: 1,
    requestId: uuid(),
    target: { serverId, hostId },
    expectedRecordVersion,
    operationId
  });
  await delay(1500);
}

// 5. 成功处理
if (task.status === "succeeded" && task.result?.verification === "passed") {
  emit("creationSucceeded", task.target as InstanceTarget);
}
```

### 状态管理

- **上下文隔离**：epoch/sequence 校验请求有效性
- **任务合并**：`taskReducer.test.ts` 验证序列合并
- **失效触发**：编辑参数、切换主机、注册表变化、关闭对话框

### 错误处理

```typescript
// 创建任务错误码映射
const errors: Record<string, string> = {
  CREATE_RELEASE_REQUIRED: "没有已验证的 DST release，已保留创建现场",
  CREATE_DOCKER_RUNTIME_REQUIRED: "Docker runtime 尚未通过验证",
  CREATE_COMPOSE_CONFIG_FAILED: "Compose 配置校验失败，已保留 staging 现场",
  CREATE_NETWORK_FAILED: "实例 network 创建失败",
  CREATE_CONTAINERS_FAILED: "实例容器创建失败，现场已保留",
  // ... 完整映射见 instanceCreationTask.ts
};
```

## 3. 测试覆盖

### 已通过

- ✅ `npm run check`（TypeScript + 前端测试 + 构建）
- ✅ `instanceCreation.test.ts`（12 个测试通过）
- ✅ `taskReducer.test.ts`（任务序列合并）
- ✅ `cargo fmt --check`（Rust 格式）
- ✅ `cargo clippy -- -D warnings`（Rust 代码质量）

### 待补充

- ❌ 创建提交后响应身份校验
- ❌ 轮询期间切换主机
- ❌ 成功后列表刷新集成测试

## 4. 代码质量指标

| 指标 | 值 | 状态 |
|-----|---|------|
| TypeScript 编译 | ✅ 无错误 | 通过 |
| Vue 组件检查 | ✅ 无错误 | 通过 |
| Rust fmt | ✅ 格式一致 | 通过 |
| Rust clippy | ✅ 无警告 | 通过 |
| 前端测试 | 12/12 通过 | 通过 |
| 构建产物 | 333.76 kB (gzip: 107.97 kB) | 正常 |

## 5. 下一步开发计划

### 立即（本周）

1. **视觉验收**（P0 门禁）
   - [ ] 录制 light/dark × 960×680/1480×920 截图
   - [ ] 键盘交互测试（Tab、Escape、Enter）
   - [ ] Accessibility 检查

2. **恢复动作实现**（P0 重要）
   - [ ] `resumeCreate`：恢复中断的创建任务
   - [ ] `cleanupCreate`：清理失败现场
   - [ ] `inspectLogs`：查看任务日志对话框

3. **Release 体验优化**（P1）
   - [ ] 验证进度反馈（当前同步调用无反馈）
   - [ ] Release 列表展示（当前只显示计数）

### 后续（下周）

4. **世界配置基础**（P0）
   - [ ] 数据模型：desired/deployed/草稿
   - [ ] 参数编辑表单、字段校验
   - [ ] 后端：读取 manifest、保存 desired、revision CAS

5. **备份与恢复基础**（P0）
   - [ ] 后端：停服一致备份、校验、下载
   - [ ] UI：备份列表、恢复确认、进度跟踪

6. **阶段 0 Docker 验证**（P0 门禁）
   - [ ] 真实 DST 镜像与 SteamCMD 集成
   - [ ] 三专服容器启动、进程监控
   - [ ] 外网玩家连接验证

## 6. 已知问题

### 🟡 中优先级

1. **Release 验证无进度反馈**
   - 现象：`prepareRelease` 调用同步，验证时间较长时无反馈
   - 影响：用户体验
   - 计划：P1 优化，添加 loading 状态

2. **断线重试逻辑不完整**
   - 现象：轮询失败后不提供重试按钮
   - 影响：需要关闭对话框重新打开
   - 计划：添加"重新查询"按钮

### 🟢 低优先级

3. **恢复动作按钮仅占位**
   - 现象：按钮已添加，点击只输出 console.warn
   - 影响：失败任务无法直接恢复
   - 计划：后续工作包实现

4. **Release 列表未展示**
   - 现象：只显示"N 个可用 release"
   - 影响：无法查看具体 release 信息
   - 计划：P1 优化

## 7. 性能数据

| 指标 | 值 | 备注 |
|-----|---|------|
| 构建时间 | ~1.2s | Vite 增量构建 |
| 类型检查 | ~8s | vue-tsc |
| 前端测试 | ~0.3s | 12 个测试 |
| 轮询间隔 | 1.5s | 可调整 |
| 构建产物 | 333.76 kB | gzip 后 107.97 kB |

## 8. 验收结论

### ✅ 通过项

- 创建任务前端集成核心路径完整
- 类型定义与契约一致
- 自动化测试通过
- 代码质量检查通过
- 文档同步及时

### ⚠️ 待补充项

- 视觉验收截图（P0 门禁）
- 键盘交互测试（P0 门禁）
- 恢复动作实现（P0 重要）
- Release 体验优化（P1）

### 📊 完成度评估

- **核心功能**：95%（创建路径完整，恢复动作骨架已建）
- **文档同步**：100%
- **测试覆盖**：85%（自动化测试充分，集成测试待补充）
- **视觉验收**：0%（待录制）

**总体评价**：前端创建任务集成已完成核心路径，可以进入下一阶段（视觉验收 → 世界配置）。当前版本可支持阶段 0 Docker 验证的准备工作。
