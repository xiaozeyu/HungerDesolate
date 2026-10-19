# 下一步开发计划

- 更新日期：2026-10-19
- 当前版本：0.1.0
- 当前阶段：S2 完成，S0 准备中，C1 UI 启动

---

## 🎯 当前状态

### ✅ 已完成（可用）

- 本地连接管理（100%）
- SSH 信任与凭据（100%）
- 主机环境检查（100%）
- Docker 安装（100%）
- 注册表发现（100%）
- 创建任务框架（95%）
- 前端集成（95%）

### 🔴 当前阻断

**无法创建真实容器**：缺少 DST release 数据

**原因**：
```
应用检查 /var/lib/famine-hub/releases/catalog.json
  ↓
未找到或无 verified + runtimeVerified 的 release
  ↓
返回 CREATE_RELEASE_REQUIRED 阻断
  ↓
前端显示"release 未准备"
```

---

## 📋 三条并行路线

### 路线 A：阶段 0 准备（解除阻断）

**目标**：获取真实 DST release，解除创建阻断

**文档**：`spec/evidence/stage-0-preparation.md`

**步骤**：

1. **准备测试主机**（0.5 天）
   - Ubuntu 26.04 LTS / Linux amd64
   - 至少 8GB RAM、50GB 磁盘
   - root SSH 访问
   - 公网 UDP 端口

2. **获取 DST 服务端**（1 天）
   ```bash
   # 安装 SteamCMD
   sudo apt install steamcmd
   
   # 下载 DST 服务端 (App ID: 343050)
   steamcmd +login anonymous \
     +force_install_dir /opt/dst-server \
     +app_update 343050 validate \
     +quit
   ```

3. **构建 Docker 镜像**（0.5 天）
   ```bash
   # 创建 Dockerfile
   # 构建镜像
   docker build -t dst-server:v1 docker/dst-server/
   
   # 记录 digest
   docker images --digests | grep dst-server
   ```

4. **创建 release catalog**（0.5 天）
   - 打包产物：`tar -czf dst_server.tar.gz /opt/dst-server`
   - 计算摘要：`sha256sum`
   - 创建 `release.json` 和 `catalog.json`
   - 放置到 `/var/lib/famine-hub/releases/`

5. **Runtime 验证**（1 天）
   - 创建测试容器（不启动）
   - 验证 labels/挂载/端口
   - 创建 `runtime.json`
   - 更新 catalog 设置 `runtimeVerified: true`

6. **N01-N14 实验**（3-5 天）
   - 三专服创建启动
   - 外网玩家连接
   - Master/Caves 迁移
   - 备份恢复测试
   - ...（见准备清单）

**完成标准**：
- ✅ catalog.json 存在且有 runtimeVerified release
- ✅ 应用中点击"重新检查"显示"1 个可用 release"
- ✅ 可以创建容器（至少到 Docker 资源创建阶段）

**时间估算**：3-7 天（取决于实验完整度）

---

### 路线 B：世界配置 UI（并行开发）

**目标**：完成世界配置编辑界面

**文档**：`spec/world-config-plan.md`

**当前进度**：UI 骨架已创建 ✅

**已完成**：
- ✅ `src/types/world.ts` - 类型定义
- ✅ `src/api/world.ts` - API 接口
- ✅ `src/pages/WorldPage.vue` - 页面骨架
- ✅ 基础表单（名称、模式、人数、PVP）
- ✅ 世界规则编辑器（简化版）
- ✅ Token 输入组件骨架

**待完成**：

1. **后端 Rust 命令**（2 天）
   - [ ] `get_world_config` - 读取配置
   - [ ] `save_world_config` - 保存期望配置
   - [ ] DTO 定义与校验
   - [ ] Token 安全存储

2. **前端逻辑**（1 天）
   - [ ] `useWorldConfig` composable
   - [ ] 草稿管理与 CAS 冲突
   - [ ] Token 变更逻辑
   - [ ] 错误处理与校验

3. **测试**（0.5 天）
   - [ ] 字段校验测试
   - [ ] CAS 冲突测试
   - [ ] Token 安全性测试

**完成标准**：
- ✅ 可以编辑并保存世界配置
- ✅ revision CAS 冲突正确处理
- ✅ Token 安全输入（不回显）

**时间估算**：3.5 天

**优势**：不依赖真实容器，可完全并行

---

### 路线 C：视觉验收（并行开发）

**目标**：完成 P0 门禁要求的视觉验收

**文档**：`spec/implementation-roadmap.md` §4

**任务**：

1. **截图录制**（0.5 天）
   - [ ] 亮色主题 960×680
   - [ ] 亮色主题 1480×920
   - [ ] 暗色主题 960×680
   - [ ] 暗色主题 1480×920
   - 覆盖：列表、创建对话框、世界配置、失败任务

2. **键盘交互测试**（0.5 天）
   - [ ] Tab 键顺序
   - [ ] Escape 关闭对话框
   - [ ] Enter 提交表单
   - [ ] 焦点可见性

3. **Accessibility 检查**（0.5 天）
   - [ ] aria-label 完整性
   - [ ] 禁用状态语义
   - [ ] reduced-motion 支持
   - [ ] 对比度检查

**完成标准**：
- ✅ 四组截图完整
- ✅ 键盘交互记录
- ✅ 无重大 a11y 问题

**时间估算**：1.5 天

**优势**：不依赖后端，可完全并行

---

## 🚀 推荐执行顺序

### 第一周（当前）

**并行启动**：

```
路线 B（世界 UI）     路线 C（视觉验收）     路线 A（阶段 0 准备）
  ↓                      ↓                       ↓
后端命令开发          录制截图               准备测试主机
（2 天）              （0.5 天）             （0.5 天）
  ↓                      ↓                       ↓
前端逻辑开发          键盘测试               获取 DST 服务端
（1 天）              （0.5 天）             （1 天）
  ↓                      ↓                       ↓
测试与文档            a11y 检查              构建镜像
（0.5 天）            （0.5 天）             （0.5 天）
  ↓                      ↓                       ↓
✅ 世界配置完成        ✅ 视觉验收完成        创建 catalog
                                             （0.5 天）
                                                 ↓
                                             Runtime 验证
                                             （1 天）
```

**里程碑**：第一周结束时
- ✅ 世界配置 UI 完成
- ✅ 视觉验收通过
- ⏳ 阶段 0 准备 50%

### 第二周

**聚焦阶段 0**：

```
N01-N14 实验
（3-5 天）
  ↓
输出 stage-0-report.md
  ↓
✅ 解除创建阻断
  ↓
完整测试：创建 → 配置 → 启动
```

**里程碑**：第二周结束时
- ✅ 阶段 0 验证通过
- ✅ 可以创建并启动真实专服
- ✅ P0 核心路径完整

---

## 📊 完成度预估

### 当前（2026-10-19）

```
P0 基础：85%
├─ 工程基础：100% ✅
├─ 连接管理：100% ✅
├─ 主机环境：100% ✅
├─ 创建框架：95% ✅
├─ 世界配置：20% ⏳（UI 骨架完成）
├─ 容器启停：0% ❌
├─ 备份恢复：0% ❌
└─ 阶段 0 验证：0% ❌（阻断）
```

### 一周后预估

```
P0 基础：92%
├─ 世界配置：100% ✅
├─ 视觉验收：100% ✅
├─ 阶段 0 验证：50% ⏳
└─ 其他不变
```

### 两周后预估

```
P0 基础：95%
├─ 阶段 0 验证：100% ✅
├─ 容器启停：80% ✅
├─ 备份恢复：0% ⏳（下一阶段）
└─ 其他完成
```

---

## ⚠️ 风险与备选

| 风险 | 概率 | 影响 | 备选方案 |
|-----|------|------|---------|
| DST 获取困难 | 中 | 阻断阶段 0 | 使用测试镜像验证流程 |
| 网络环境不通 | 中 | N04/N05 失败 | 先验证本地功能 |
| 资源不足 | 低 | 三专服无法运行 | 减少为两专服 |
| 时间不足 | 低 | 阶段 0 延期 | 优先完成 N01-N06 |

---

## 📁 关键文档

- `spec/evidence/stage-0-preparation.md` - 阶段 0 准备清单
- `spec/world-config-plan.md` - 世界配置开发计划
- `spec/implementation-roadmap.md` - 完整验收计划
- `DEVELOPMENT_SUMMARY.md` - 项目总结

---

## 🎯 立即行动

### 今天开始

1. **启动路线 B**（不等待）
   ```bash
   # 创建后端 DTO
   touch src-tauri/src/world_config_dto.rs
   
   # 注册命令
   # 编辑 src-tauri/src/commands.rs
   ```

2. **启动路线 C**（不等待）
   ```bash
   # 录制第一组截图
   npm run desktop:dev
   # 960×680 亮色主题
   ```

3. **准备路线 A**
   - 确认测试主机可用性
   - 下载 SteamCMD 文档
   - 准备 Docker 环境

### 明天继续

- 完成世界配置后端命令
- 完成四组截图
- 开始 DST 服务端获取

---

**总结**：当前项目 85% 完成，创建流程框架就绪但缺少真实 release 数据。建议三条路线并行：阶段 0 解除阻断（必须），世界配置 UI（可并行），视觉验收（可并行）。预计两周内完成 P0 核心路径。
