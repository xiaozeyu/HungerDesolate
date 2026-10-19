<script setup lang="ts">
import { useWorkspace } from "../composables/useWorkspace";
const domainWorkspace=useWorkspace();
import { usePageTarget } from "../composables/usePageTarget";
const { instanceName } = usePageTarget();
import PreviewNotice from "../components/PreviewNotice.vue";
import { Terminal, AlertCircle, RefreshCw } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
</script>
<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="诊断">
      <template #actions>
        <button disabled class="button">
          <RefreshCw :size="16"/>
          刷新证据
        </button>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <template v-if="domainWorkspace.preview.value">
    <div class="diagnostic-grid">
      <section class="section-block">
        <div class="section-heading">
          <div>
            <h2>
              分片日志
            </h2>
            <p class="mono">
              Master · 最近 200 行
            </p>
          </div>
          <Terminal :size="18"/>
        </div>
        <pre class="log-window">
          [09:41:32] Master server is 示例\n[09:41:34] Registered in the server lobby\n[09:41:39] Caves shard connected\n[09:42:04] Waiting for player connections
        </pre>
      </section>
      <section class="section-block">
        <div class="section-heading">
          <div>
            <h2>
              失败任务
            </h2>
          </div>
          <AlertCircle :size="18" class="icon-warning"/>
        </div>
        <div class="incident-row">
          <div>
            <strong>
              应用 MOD
            </strong>
            <small>
              任务在部署验证阶段失败
            </small>
          </div>
          <StatusLabel tone="danger" label="需要处理"/>
          <button disabled class="text-button">
            查看任务
          </button>
        </div>
      </section>
    </div>
    </template>
    <div v-else class="empty-state"><h2>暂不可用</h2></div>
  </div>
</template>
