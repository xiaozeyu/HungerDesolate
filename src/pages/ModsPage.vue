<script setup lang="ts">
import { useWorkspace } from "../composables/useWorkspace";
const domainWorkspace=useWorkspace();
import { usePageTarget } from "../composables/usePageTarget";
const { instanceName } = usePageTarget();
import PreviewNotice from "../components/PreviewNotice.vue";
import { Plus, RefreshCw, Upload } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
</script>
<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="MOD">
      <template #actions>
        <button disabled class="button">
          <RefreshCw :size="16"/>
          检查更新
        </button>
        <button disabled class="button primary">
          <Plus :size="16"/>
          添加 MOD
        </button>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <template v-if="domainWorkspace.preview.value">
    <section class="summary-line">
      <div>
        <span class="metric-number">
          12
        </span>
        <span>
          个已启用
        </span>
      </div>
      <span class="summary-divider"/>
      <div>
        <span class="metric-number warning-number">
          2
        </span>
        <span>
          个待更新
        </span>
      </div>
      <span class="summary-divider"/>
      <div class="muted-text">
        最后检查：示例时间
      </div>
    </section>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>
              MOD
            </th>
            <th>
              期望状态
            </th>
            <th>
              实际部署
            </th>
            <th>
              版本
            </th>
            <th>
              操作
            </th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              <strong>
                Global Positions
              </strong>
              <small class="table-sub">
                workshop · 134596
              </small>
            </td>
            <td>
              <StatusLabel tone="success" label="启用"/>
            </td>
            <td>
              <StatusLabel tone="success" label="已部署"/>
            </td>
            <td class="mono">
              v2.4.1
            </td>
            <td>
              <button disabled class="text-button">
                <Upload :size="15"/>
                配置
              </button>
            </td>
          </tr>
          <tr>
            <td>
              <strong>
                Geometric Placement
              </strong>
              <small class="table-sub">
                workshop · 351813
              </small>
            </td>
            <td>
              <StatusLabel tone="warning" label="待应用"/>
            </td>
            <td>
              <StatusLabel tone="warning" label="旧版本"/>
            </td>
            <td class="mono">
              v1.9.0 → v1.9.2
            </td>
            <td>
              <button disabled class="text-button">
                查看差异
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    </template>
    <div v-else class="empty-state"><h2>暂不可用</h2></div>
  </div>
</template>
