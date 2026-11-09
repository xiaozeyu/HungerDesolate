# 世界配置开发计划

- 创建日期：2026-10-19
- 优先级：P0 核心
- 依赖：容器创建框架已完成
- 可并行：阶段 0 准备
- 当前实现：基础/联机字段、Master/Caves 选项、期望配置 CAS 保存、应用命令已落地；应用检查已合并进 `apply_world_config`，前端不再有独立预览命令或预览窗口。实例启动命令会等待 DST 进程及容器内 UDP 监听器就绪，状态标为本机运行验证。启动失败诊断与真实主机实测待验收；公网可达性仍未验证。

## 1. 功能范围

根据 `spec/api-contract.md` §4.2 和 §7.2：

### 数据模型

```typescript
interface WorldConfig {
  revision: number;
  desired: WorldSettings;
  deployed: WorldSettings | null;
  desiredDigest: string;
  deployedDigest: string | null;
  deployedRevision: number | null;
  tokenStatus: "configured" | "missing" | "unknown";
  serverPasswordStatus: "configured" | "missing" | "unknown";
  validationWarnings: string[];
}

interface WorldSettings {
  roomName: string;
  description: string;
  gameMode: "relaxed" | "survival" | "endless" | "wilderness" | "lightsout";
  maxPlayers: number;         // 1-128
  pvp: boolean;
  language: "zh" | "en";
  pauseWhenEmpty: boolean;
  cavesEnabled: boolean;      // 只读，容器创建时确定
  advanced: {
    voteEnabled: boolean;
    voteKickPercentage: number;
    adminList: string[];
    whitelist: string[];
    whitelistSlots: number;
    blocklist: string[];
  };
  worldgen: {
    master: ShardWorldConfig;
    caves: ShardWorldConfig;
  };
}

interface ShardWorldConfig {
  preset: "SURVIVAL_TOGETHER" | "DST_CAVE";
  worldgenOverrides: Record<string, string>;
  worldSettingsOverrides: Record<string, string>;
}
```

DST 选项 key、标签、分组、分片适用范围、默认值和可选值由迁移的 `dst-world-options.json` 定义；远端只接受该 schema 中对应分片允许的值。世界配置保存为期望配置，当前不填充 `deployed`，直到配置发布和运行验证能力实现。

### 关键约束

1. **Token 与密钥分离**
   - Token 更换请求：`{ action: "replace", value: string }` 或 `{ action: "keep" }`
   - 敏感输入只允许从内存送入专用命令
   - 禁止进入调试、请求日志、fixture、草稿、任务摘要

2. **期望与部署分离**
   - `desired`：用户编辑的期望配置
   - `deployed`：实际运行的配置
   - `desiredDigest !== deployedDigest` → 显示"待应用"

3. **revision CAS**
   - 保存时携带 `expectedRevision`
   - 冲突返回 `REVISION_CONFLICT`，保留草稿
   - 成功返回新 `revision` 作为前端下限

4. **洞穴保留**
   - `cavesEnabled` 是容器创建时确定的拓扑字段
   - 关闭洞穴保留存档，不删除数据

## 2. 开发任务拆解

### 阶段 1：数据层（2 天）

#### 后端（Rust）

- [ ] **定义 DTO**
  ```rust
  // src-tauri/src/world_config_dto.rs
  pub struct WorldConfig { ... }
  pub struct WorldSettings { ... }
  pub struct TokenChange {
      action: TokenAction,
      value: Option<String>,
  }
  pub enum TokenAction { Replace, Keep }
  ```

- [ ] **读取命令**
  ```rust
  // src-tauri/src/commands.rs
  #[tauri::command]
  async fn get_world_config(
      request: ReadRequest<InstanceTarget>
  ) -> Result<QueryResponse<WorldConfig>, CommandError> {
      // 1. 验证 target 身份
      // 2. 读取 manifest.json
      // 3. 返回 desired/deployed/tokenStatus
  }
  ```

- [ ] **保存命令**
  ```rust
  #[tauri::command]
  async fn save_world_config(
      target: InstanceTarget,
      settings: WorldSettings,
      token_change: Option<TokenChange>,
      expected_revision: i64,
      operation_id: String
  ) -> Result<MutationResult<WorldConfig>, CommandError> {
      // 1. CAS 校验 revision
      // 2. 规范化配置并计算 digest
      // 3. 保存到 manifest desired 字段
      // 4. Token 单独写入受保护位置
      // 5. 返回新 revision 和失效范围
  }
  ```

- [ ] **输入校验**
  - roomName: 1-64 字符，不含控制字符
  - description: 0-256 字符
  - maxPlayers: 1-128
  - gameMode/pvp/worldSize 枚举值
  - cavesEnabled 与 cavesRules 一致性

#### 前端（TypeScript）

- [ ] **API 接口**
  ```typescript
  // src/api/world.ts
  export function getWorldConfig(
      target: InstanceTarget,
      requestId: string,
      expectedRecordVersion: number
  ): Promise<QueryResponse<WorldConfig>>
  
  export function saveWorldConfig(
      target: InstanceTarget,
      settings: WorldSettings,
      tokenChange: TokenChange | null,
      expectedRevision: number,
      operationId: string,
      requestId: string,
      expectedRecordVersion: number
  ): Promise<MutationResult<WorldConfig>>
  ```

- [ ] **类型定义**
  ```typescript
  // src/types/world.ts
  export interface WorldConfig { ... }
  export interface WorldSettings { ... }
  export type TokenChange = 
    | { action: "replace"; value: string }
    | { action: "keep" };
  ```

### 阶段 2：UI 组件（3 天）

#### WorldPage.vue

- [ ] **页面布局**
  ```vue
  <template>
    <div class="page">
      <PageHeader title="世界配置">
        <template #actions>
          <button @click="refresh">刷新</button>
          <button v-if="hasChanges" @click="save">保存</button>
          <button v-if="canApply" @click="apply">应用配置</button>
        </template>
      </PageHeader>
      
      <!-- 状态指示 -->
      <div class="world-status">
        <StatusLabel :tone="statusTone" :label="statusLabel"/>
        <span v-if="desiredVsDeployed === 'pending'">
          配置已保存，待应用
        </span>
      </div>
      
      <!-- 编辑表单 -->
      <WorldConfigForm
        v-model="draft"
        :deployed="deployed"
        :caves-enabled="cavesEnabled"
        :validation-errors="validationErrors"
      />
      
      <!-- Token 输入（独立组件） -->
      <TokenInput
        v-model="tokenChange"
        :status="tokenStatus"
        :disabled="saving"
      />
    </div>
  </template>
  ```

- [ ] **草稿管理**
  - 初始加载：`draft = { ...desired }`
  - 编辑触发：`hasChanges = digestOf(draft) !== desiredDigest`
  - 保存成功：更新 `desiredDigest`，不清空 `draft`
  - 刷新响应：低于 `revisionFloor` 不覆盖草稿
  - 切换实例：清空草稿

- [ ] **CAS 冲突处理**
  ```typescript
  catch (error) {
    if (error.code === "REVISION_CONFLICT") {
      // 保留 draft
      // 重新读取 desired
      // 显示比较 Dialog
      showConflictDialog(draft, freshDesired);
    }
  }
  ```

#### WorldConfigForm.vue

- [ ] **基础字段**
  ```vue
  <div class="form-grid">
    <label>
      专服名称
      <input v-model="draft.roomName" maxlength="64" required/>
    </label>
    
    <label>
      说明
      <textarea v-model="draft.description" maxlength="256"/>
    </label>
    
    <label>
      游戏模式
      <select v-model="draft.gameMode">
        <option value="survival">生存</option>
        <option value="endless">无尽</option>
        <option value="wilderness">荒野</option>
      </select>
    </label>
    
    <label>
      最大玩家数
      <input v-model.number="draft.maxPlayers" type="number" min="1" max="128"/>
    </label>
    
    <label class="switch">
      <input v-model="draft.pvp" type="checkbox"/>
      PVP 模式
    </label>
  </div>
  ```

- [ ] **世界规则（可折叠）**
  ```vue
  <details class="world-rules">
    <summary>主世界规则</summary>
    <WorldRulesEditor v-model="draft.masterRules"/>
  </details>
  
  <details v-if="cavesEnabled" class="world-rules">
    <summary>洞穴规则</summary>
    <WorldRulesEditor v-model="draft.cavesRules"/>
  </details>
  ```

- [ ] **字段校验**
  - 实时校验：`@input` 触发
  - 错误提示：字段下方红色文本
  - 阻断保存：`hasErrors` 时禁用保存按钮

#### TokenInput.vue

- [ ] **安全输入**
  ```vue
  <div class="token-input">
    <div class="token-status">
      <StatusLabel 
        :tone="status === 'configured' ? 'success' : 'warning'" 
        :label="statusLabel"
      />
      <span v-if="status === 'missing'">
        启动需要认证时必须配置 Token
      </span>
    </div>
    
    <div v-if="changing" class="token-field">
      <label>
        Klei Token
        <input 
          v-model="tokenValue" 
          type="password" 
          autocomplete="off"
          placeholder="pds-g^KU_..."
        />
      </label>
      <button @click="cancel">取消</button>
    </div>
    
    <button v-else @click="changing = true">
      {{ status === 'configured' ? '更换 Token' : '设置 Token' }}
    </button>
  </div>
  ```

- [ ] **行为约束**
  - `tokenValue` 只存在组件内存
  - 切换页面/关闭对话框清空
  - 保存成功后清空，显示"已配置"
  - 不回显已保存的 Token

#### WorldRulesEditor.vue

- [ ] **规则编辑器**（简化版）
  ```vue
  <div class="world-rules-editor">
    <label>
      世界大小
      <select v-model="rules.worldSize">
        <option value="small">小</option>
        <option value="medium">中</option>
        <option value="large">大</option>
        <option value="huge">巨大</option>
      </select>
    </label>
    
    <label>
      起始季节
      <select v-model="rules.season_start">
        <option value="autumn">秋</option>
        <option value="winter">冬</option>
        <option value="spring">春</option>
        <option value="summer">夏</option>
      </select>
    </label>
    
    <!-- 更多规则待补充 -->
  </div>
  ```

- [ ] **扩展性**
  - 使用配置驱动：`RULE_SCHEMA` 定义字段
  - 支持数字、枚举、布尔类型
  - 显示中文标签与提示

### 阶段 3：应用流程（2 天）

第一版应用实现约束：配置应用只替换受保护的 `config/` 并按预览时已运行的分片集合重建和启动，不改写 `data/Master`、`data/Caves` 世界存档，因此已存在世界文件的实例仍可重复应用配置。世界生成选项在已有存档上只对新世界生效；未来重置或迁移世界数据仍必须先接入保护备份链路。应用回执明确 `runtimeVerified=false`。此首版操作不宣称公网联通或玩家可加入。

#### 检查与应用（2026-10-31 合并）

- [x] **检查合并进应用命令**（后端）
  ```rust
  #[tauri::command]
  async fn apply_world_config(
      target: InstanceTarget,
      expected_record_version: u64,
      expected_revision: u64,
      operation_id: String
  ) -> Result<WorldApplyResponse, CommandError> {
      // 1. 同一 SSH 会话内先执行只读应用检查（revision、期望摘要、Token、部署、Release、渲染）
      // 2. 阻断项以 WORLD_APPLY_BLOCKED + details.blockers 返回，不进入写入
      // 3. 以 revision、检查得到的期望 digest、active release 和 operationId 绑定应用
      // 4. 按原运行状态停止、重建并启动；返回 runtimeVerified=false 与 warnings
  }
  ```

- [x] **UI 流程**
  ```vue
  <button @click="saveAndApply" :disabled="saving || applyBusy">
    {{ saving ? '保存中…' : applyBusy ? '检查并部署中…' : '保存并应用' }}
  </button>
  ```
  保存成功后直接调用 `apply_world_config`；阻断项和警告内联显示，不再打开应用预览 Dialog，也不保留单独“预览应用”入口。

## 3. 测试计划

### 单元测试

- [ ] `worldConfigValidation.test.ts`
  - 字段长度/范围校验
  - 枚举值校验
  - cavesRules 一致性

- [ ] `worldConfigDigest.test.ts`
  - 规范化规则
  - digest 计算稳定性

### 集成测试

- [ ] 保存成功后 revision 增加
- [ ] CAS 冲突保留草稿
- [ ] Token 不进入响应
- [ ] 切换实例清空草稿
- [ ] 刷新不覆盖 dirty 草稿

### 手动测试

- [ ] 填写完整配置并保存
- [ ] 故意制造 revision 冲突
- [ ] Token 输入/切换/取消
- [ ] 亮暗主题表单样式
- [ ] 键盘 Tab 顺序

## 4. 文档输出

- [ ] 更新 `spec/api-contract.md` §7.2
- [ ] 补充世界参数 schema
- [ ] 记录字段校验规则
- [ ] 创建验收记录

## 5. 依赖与阻断

### 不依赖项（可立即开发）

- ✅ UI 布局与组件
- ✅ 前端类型定义
- ✅ 草稿管理逻辑
- ✅ 字段校验

### 依赖项（需要真实环境）

- ⏳ Token 写入位置确认（依赖容器目录结构）
- ⏳ 世界规则完整 schema（依赖 DST 文档）
- ⏳ 应用任务执行（依赖容器启停）

### 开发顺序

```
立即：UI 组件 + 前端逻辑（3 天）
  ↓
并行：后端保存逻辑（2 天）
  ↓
等待：应用任务（需要容器启停，阶段 0 后）
```

## 6. 成功标准

### 最小可用（MVP）

- ✅ 可以编辑基础字段（名称、模式、人数、PVP）
- ✅ 保存成功，revision 增加
- ✅ CAS 冲突正确处理
- ✅ Token 安全输入

### 完整版（P0）

- ✅ 世界规则编辑
- ✅ 应用配置任务
- ✅ 停服备份流程
- ✅ 验证配置生效

## 7. 时间估算

| 阶段 | 工作量 | 时间 |
|-----|--------|------|
| 数据层 | 后端 DTO + 命令 + 校验 | 2 天 |
| UI 组件 | 表单 + Token + 规则编辑 | 3 天 |
| 应用流程 | 预览 + 任务 + 验证 | 2 天 |
| 测试 | 单元 + 集成 + 手动 | 1 天 |
| 文档 | API + 验收 | 0.5 天 |
| **总计** | | **8.5 天** |

**并行开发**：前 3 天的 UI 可以与阶段 0 准备并行。

## 8. 风险与备选

| 风险 | 影响 | 备选 |
|-----|------|------|
| DST 参数 schema 不完整 | 规则编辑不全 | 先实现基础字段，规则后补 |
| Token 写入位置不确定 | 保存失败 | 先只保存 desired，Token 占位 |
| 应用任务依赖启停 | 无法完整测试 | 先实现保存，应用待启停完成 |

## 9. 下一步

### 立即开始（不等阶段 0）

1. 创建 `src/api/world.ts`
2. 创建 `src/types/world.ts`
3. 创建 `src/pages/WorldPage.vue` 骨架
4. 创建 `src/components/WorldConfigForm.vue`

### 验证点

- UI 布局完成 → 录制截图
- 保存逻辑完成 → 单元测试
- 应用流程完成 → 集成测试

**建议**：立即开始 UI 开发，与阶段 0 准备并行，最大化开发效率。
