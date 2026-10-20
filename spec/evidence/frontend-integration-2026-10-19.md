# 前端创建任务集成验收记录

- 日期：2026-10-19
- 验收人：自动化测试 + 人工核对
- 范围：创建对话框前端集成、任务轮询、状态展示

## 1. 功能完成度

### ✅ 已实现

| 功能 | 状态 | 证据 |
|-----|------|------|
| 创建表单 | ✅ 完成 | `InstanceCreateDialog.vue` 收集全部参数 |
| 预览调用 | ✅ 完成 | `previewInstanceCreate` 调用与响应校验 |
| Release 状态 | ✅ 完成 | 显示可用/未准备状态、验证按钮 |
| Release 验证 | ✅ 完成 | `prepareRelease` 调用 import |
| 创建提交 | ✅ 完成 | `createInstance` 调用与 operationId 绑定 |
| 任务轮询 | ✅ 完成 | 1.5秒间隔轮询直到终态 |
| 进度展示 | ✅ 完成 | 根据 stage 显示当前阶段 |
| 错误映射 | ✅ 完成 | `creationTaskErrorLabel` 映射完整 |
| 成功关闭 | ✅ 完成 | succeeded 且 verification=passed 时关闭对话框 |
| 列表刷新 | ✅ 完成 | 创建成功后刷新实例列表 |

### ⚠️ 待补充

| 功能 | 当前状态 | 计划 |
|-----|---------|------|
| 恢复动作按钮 | 未实现 | 失败时展示 recoveryActions 按钮 |
| 任务日志查看 | 未实现 | "查看日志"按钮与日志对话框 |
| 清理现场 | 未实现 | failed 时提供清理入口 |
| 断线重试 | 部分实现 | 轮询失败保留 stale，但无重试按钮 |

## 2. 调用流程验证

### 创建路径

```
用户填写表单
  ↓
点击"预览" → previewInstanceCreate
  ↓
显示端口计划与阻断
  ↓
在 DST Release 管理页完成镜像与运行时验证；创建窗口只选择已验证 Release
  ↓
点击"创建容器" → createInstance
  ↓
轮询 getInstanceCreateStatus (1.5s 间隔)
  ↓
succeeded + verification=passed → 关闭对话框 → 刷新列表
```

### 错误路径

```
预览阻断（端口、Docker、注册表）
  → 显示阻断项，禁用"创建容器"

Release 未准备
  → 显示未就绪状态，不提供创建窗口内验证操作

创建失败（CREATE_RELEASE_REQUIRED / CREATE_DOCKER_RUNTIME_REQUIRED）
  → 显示错误消息，保留创建现场
```

## 3. 隔离测试覆盖

### 已通过

- ✅ 预览输入校验（`instanceCreation.test.ts`）
- ✅ 响应身份匹配（`matchesCreationPreview`）
- ✅ 上下文隔离（epoch/sequence）
- ✅ 编辑参数失效（切换主机、注册表变化）
- ✅ 任务序列合并（`taskReducer.test.ts`）

### 未覆盖

- ❌ 创建提交后 Release 状态变化
- ❌ 轮询期间切换主机
- ❌ 任务完成后列表刷新逻辑

## 4. 前端类型完整性

### 已定义

```typescript
// src/api/instances.ts
interface InstanceCreateInput { displayName, autostart, startAfterCreate, portPolicy, cavesEnabled }
interface InstanceCreatePreview { previewId, proposedInstanceId, ports, blockers, allowed, ... }
interface InstanceCreateTaskResponse { task: TaskSnapshot | null }

// src/types/contracts.ts
interface TaskSnapshot { taskId, operationId, target, operation, status, stage, result, error, ... }
interface TaskResult { verification, remoteChanged, invalidate, recoveryActions }
interface TaskRecoveryAction { action, label, requiresPreview }
```

### 待补充

- ⚠️ `InstanceCreateRequest` 完整类型（当前通过 any）
- ⚠️ Release 相关类型的前端 DTO 文档

## 5. 视觉交互验收

### 待录制

- ❌ 亮色主题 960×680
- ❌ 亮色主题 1480×920
- ❌ 暗色主题 960×680
- ❌ 暗色主题 1480×920

### 键盘交互

- ⚠️ Tab 顺序未测试
- ⚠️ Escape 关闭对话框未测试
- ⚠️ 表单提交 Enter 未测试

## 6. 已知问题

1. **Release 验证时间较长无进度**：`prepareRelease` 调用同步，无进度反馈
2. **任务失败后无恢复入口**：需要实现 recoveryActions 按钮
3. **断线重试逻辑不完整**：轮询失败后不提供重试按钮
4. **日志查看未实现**：failed 任务无法查看详细日志

## 7. 下一步

1. 补充任务恢复动作按钮（P0）
2. 实现任务日志查看对话框（P0）
3. 录制四组视觉验收截图（P0）
4. 补充键盘交互测试（P0）
5. 创建更新错误码映射（P1）

## 8. 验收结论

**核心路径通过**：预览 → Release 验证 → 创建 → 轮询 → 成功关闭 → 刷新列表

**待补充项**：恢复动作、日志查看、视觉验收

**阻断项**：无（核心创建路径可用）
