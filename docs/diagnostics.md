# 专服诊断

## 当前能力

诊断页面 `/hosts/:serverId/instances/:instanceId/diagnostics` 读取一次有界诊断快照：

- 运行摘要：运行状态、配置版本、世界版本、MOD 版本、DST build、活动 Release、MOD Release
- 分片容器状态
- Docker Server 版本、可用磁盘
- Master/Caves 最近日志，已脱敏、限制行数与总字节
- 最近失败或中断任务：任务 ID、阶段、错误码、恢复步骤和阶段记录

## 安全边界

- 日志只读取最近 200 行。
- 日志中 `token/password/secret/authorization` 值、`pds-` 开头的 Klei Token、长 base64 串会被替换为 `<redacted>`。
- 不读取 `cluster_token.txt`、`desired-secrets` 或存档内容。
- 任务证据只返回有界状态字段和阶段记录，不返回原始远端诊断文本。
- 日志使用 Vue 文本插值渲染，不把内容作为 HTML。

## 命令

```text
get_instance_diagnostics
```

返回：

```json
{
  "health": {},
  "logs": [{ "shard": "master", "lines": [], "truncated": false, "available": true }],
  "tasks": []
}
```

## 当前不做

- 历史健康曲线
- 诊断包导出
- 远端日志游标分页；当前为固定最近 200 行
