# Element Plus 迁移影响评估

> 历史评估（2026-11-09）。2026-11-13 已决定迁移到 Naive UI；当前实现见 `src/naive/`，本文仅保留选型对比记录。

- 版本：1.0.5（评估）
- 日期：2026-11-09
- 结论先行：**技术上可迁移，但当前项目规范不允许引入第二套视觉组件库；全站迁移成本高，Release 页局部引入不建议。**
- 适用对象：Release 管理列表改版，以及后续是否需要引入 Element Plus

## 1. 当前技术栈与 UI 现状

| 项目 | 当前情况 |
| --- | --- |
| 框架 | Vue 3.5 + TypeScript + Vite 6 |
| 桌面外壳 | Tauri 2 |
| 路由 | Vue Router 4 |
| 交互原语 | Reka UI 2.10（headless） |
| 图标 | `@lucide/vue` |
| 样式 | 自有 CSS Token + `style.css` + 页面 scoped CSS |
| Vue 文件 | 31 个（11 页面 + 19 组件 + App） |
| 共享组件 | `AppDialog`、`AppSelect`、`OptionSelect`、`StatusLabel`、`SummaryBar`、`TaskPanel`、`TaskDrawer`、`IconButton`、`FloatingTask` 等 |
| 当前使用量 | 21 处 `AppDialog`、7 处 `AppSelect`、10 处原生表格 |
| 当前构建 | JS 约 624 KB（gzip 约 176 KB），CSS 约 64 KB（gzip 约 11 KB） |
| 约束 | `AGENTS.md` 明确“不引入其他视觉组件库” |

## 2. 迁移方案对比

| 方案 | 范围 | 预计工作量 | 风险 | 建议 |
| --- | --- | ---: | --- | --- |
| A. 全站迁移 Element Plus | 31 个 Vue 文件、21 个 Dialog、7 个 Select、10 个表格、表单/按钮/提示 | 20–30 人日 | 高 | 不建议当前做 |
| B. 仅 Release 页局部引入 | 1 个页面 + Release 列表/进度/对话框 | 3–5 人日 | 中高（视觉和交互双栈） | 不建议 |
| C. 只引入 `ElProgress` | Release 构建进度条 | 2–3 人日（含依赖和主题处理） | 中（仍引入第二套视觉库） | 不建议 |
| D. 不迁移，自研列表/条纹进度 | Release 列表 UI | 0.5–1 人日 | 低 | 推荐 |

### 方案 A：全站迁移

**收益**

- 表格、分页、表单、对话框、提示、进度条等有统一组件。
- 长期如果团队已经熟悉 Element Plus，开发速度可能提升。
- 组件行为、无障碍基础和键盘交互由库维护。

**代价**

- 需要替换 21 处 `AppDialog`、7 处 `AppSelect`、10 处表格，以及大量按钮/输入/提示/状态样式。
- Element Plus 默认圆角、字号、间距、暗色策略与当前极简 Token 不一致，需要大量覆盖；迁移后不一定减少 CSS。
- 现有 13 个 UI source-guard 测试和 22 个单元测试依赖当前类名/结构，需同步重写。
- 现有 Reka UI 弹窗、焦点锁定、Escape、Select 键盘行为需要重新验收。
- 全量引入 Element Plus 的构建体积：约 **+1.0 MB minified JS + 355 KB CSS**（gzip 约 +295 KB + 48 KB）；按需引入会小一些，但必须做 POC 实测。
- 引入 `unplugin-vue-components` / `unplugin-auto-import` 等构建工具，增加构建链复杂度。
- 需要修改 `AGENTS.md` 冻结约束；否则违反项目规则。

**结论**：只有在明确决定“全站 UI 体系切换”时才做，不能作为 Release 列表改版的顺手改动。

### 方案 B：仅 Release 页局部引入

**看似收益**

- 只改一个页面，短期快。
- 可以用 `ElTable`、`ElProgress`、`ElDialog`。

**实际代价**

- 同一应用出现两套按钮、弹窗、表格、圆角、间距、暗色主题；页面之间视觉不一致。
- 仍需处理 Element Plus 主题覆盖，工作量不一定比自研小。
- Release 页会引入 Element Plus 运行时和样式，但其他页面不用，形成“只为一个页面背整库”的包体成本。
- 后续任何用到 Release 列表的测试都可能同时依赖 Reka UI 与 Element Plus 行为。
- 违反当前“不引入第二套视觉组件库”的约束。

**结论**：不建议。局部引入通常比全站迁移更容易长期失控。

### 方案 C：只引入 `ElProgress`

- 只为一个条纹进度条引入组件库，成本明显不划算。
- 当前自研条纹进度已经实现，约 20 行 CSS；ElProgress 的条纹/动画也可用 CSS 复刻。
- 仍要维护 Element Plus 版本、主题、样式隔离和构建依赖。

**结论**：不建议。

### 方案 D：不迁移，自研 UI

- Release 列表、摘要、条纹进度全部使用现有 Token 和 CSS。
- 无新增依赖、无主题冲突、无测试体系切换。
- 与当前页面一致，符合项目约束。
- 预计 0.5–1 人日完成列表改版。

**结论**：推荐。

## 3. 迁移影响维度

### 3.1 包体积与启动

- 当前 JS gzip 约 176 KB，CSS gzip 约 11 KB。
- Element Plus 全量：JS gzip 约 295 KB，CSS gzip 约 48 KB（不含现有代码）。
- Tauri 本地加载，网络不是问题；但 WebView 解析、首屏、内存会受影响。
- 按需引入需要 POC 测量；不能直接以全量数字判定，但可以确定会增加依赖和构建复杂度。

### 3.2 主题与视觉

- 当前使用 `--canvas`、`--surface`、`--text`、`--accent`、`--success` 等语义 Token。
- Element Plus 使用 `--el-color-primary`、`--el-bg-color`、`--el-text-color-*` 等变量体系。
- 需要建立 `--el-*` → 项目 Token 的映射，并处理暗色主题。
- Element Plus 默认圆角、字号、控件高度与当前极简设计不同，需覆盖大量组件样式。
- 迁移后页面布局 CSS/Iconfont/表格样式仍要保留，不能直接删除现有 CSS。

### 3.3 组件与交互

| 当前组件 | Element Plus 对应 | 影响 |
| --- | --- | --- |
| `AppDialog`（Reka UI） | `ElDialog` | 焦点锁定、Escape、外部关闭、footer 结构需重新验收 |
| `AppSelect` / `OptionSelect` | `ElSelect` / `ElOption` | 键盘、空态、禁用、宽度行为不同 |
| 原生表格 | `ElTable` | 列宽、空态、行操作、响应式需重写 |
| 自定义按钮/输入 | `ElButton` / `ElInput` | 尺寸、禁用、loading、焦点样式需统一 |
| 自研条纹进度 | `ElProgress` | 视觉可替代，但引入整库不划算 |
| `StatusLabel` / `SummaryBar` | 无直接对应 | 仍保留自研 |

### 3.4 测试影响

- 13 个 `scripts/test-*-ui.mjs` source guard 直接匹配当前类名、组件名、模板结构；迁移会大面积失败。
- 22 个 `src/lib/*.test.ts` 中部分逻辑不依赖 UI，部分组件行为测试需要重写。
- 需要新增 Element Plus 主题/暗色/键盘回归。
- 现有 `npm run check` 会同时跑类型检查、测试和构建，迁移期间需要分批切换，不能一次性改完。

### 3.5 Tauri / CSP / 构建

- Element Plus 使用 Vue 组件和 CSS，Tauri CSP 不会因此直接失败；当前 `style-src` 已允许 `unsafe-inline`。
- 弹层会 teleport 到 body，需要核对外壳层级、滚动锁和 z-index。
- 若使用自动导入插件，需要确认 Vite/Tauri 构建在 macOS/Windows 上一致。
- 不涉及 Rust 后端，无 target/Revision/任务逻辑变化。

### 3.6 维护与锁定

- Element Plus 版本升级可能改变主题变量、弹窗行为和表格 API。
- 全站迁移后，现有 Reka UI 与自定义组件是否保留需要明确；不建议两套长期并存。
- 若未来还要支持移动端/Web，Element Plus 的响应式表格和弹层行为需要额外评估。

## 4. 建议的实施路径

### 推荐：不迁移，先做 Release 列表自研改版

1. 按 [Release 管理列表 UI 规划](release-management-ui-plan.md) 修改列表。
2. 继续使用 `StatusLabel`、`SummaryBar`、现有表格样式。
3. 条纹进度继续用 CSS 实现。
4. 不新增依赖，不改 `AGENTS.md`。
5. 预计 0.5–1 人日。

### 如果产品决定未来采用 Element Plus

必须先做一个受控 POC，再决定是否全站迁移：

1. 新增分支/临时工作区，不直接改主分支依赖。
2. 只实现 Release 页 + 一个 Dialog + 一个 Table + 一个 Select，测量按需构建体积。
3. 建立 `--el-*` 与项目 Token 的映射，验证亮/暗主题。
4. 验证 960×680、1480×920、键盘、焦点、Escape、滚动锁。
5. 记录包体积、首屏时间、构建时间和测试改动量。
6. POC 通过后，再决定是否修改 `AGENTS.md` 冻结约束并全站迁移。

POC 的退出条件：

- 构建体积增量在可接受预算内（建议 gzip JS ≤ 250 KB）。
- 视觉与现有页面一致，不需要大量 `!important`。
- 关键交互（Dialog/Select/Table）通过键盘和焦点验收。
- 现有测试有明确的迁移/替换方案，而不是删除断言。

## 5. 工作量估算

| 工作 | 人日 |
| --- | ---: |
| Release 列表自研改版 | 0.5–1 |
| Element Plus 只替换 Release 页（POC + 主题 + 测试） | 3–5 |
| 全站组件替换 | 12–20 |
| 主题/暗色/视觉回归 | 3–5 |
| 测试迁移与修复 | 3–5 |
| 全站合计 | 18–30 |

以上为一名熟悉项目的开发者估算，不含真实主机验收和安装包发布。

## 6. 结论

- 当前页面确实是 Vue 3。
- Element Plus 技术上可以用于 Vue 3，但当前项目规范禁止引入第二套视觉组件库。
- 为一个 Release 列表或进度条引入 Element Plus 不划算，且会造成双主题/双交互体系。
- 推荐继续使用 Reka UI + 自有 CSS Token，自行实现 Release 列表和条纹进度。
- 如果未来要全站切换，必须先做 POC、改项目规范、预留 18–30 人日，并接受包体积和测试迁移成本。

## 7. 参考

- Element Plus 官方文档：<https://element-plus.org/>
- 当前依赖与约束：[package.json](../package.json)、[AGENTS.md](../AGENTS.md)
- 当前构建产物：`dist/assets/`
