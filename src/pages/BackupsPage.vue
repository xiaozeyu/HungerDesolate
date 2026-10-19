<script setup lang="ts">
import { useWorkspace } from "../composables/useWorkspace";
const domainWorkspace=useWorkspace();
import { usePageTarget } from "../composables/usePageTarget";
const { instanceName } = usePageTarget();
import PreviewNotice from "../components/PreviewNotice.vue";
import { Download, Plus, RotateCcw } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
</script>
<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="备份">
      <template #actions>
        <button disabled class="button">
          <Download :size="16"/>
          下载
        </button>
        <button disabled class="button primary">
          <Plus :size="16"/>
          创建备份
        </button>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <template v-if="domainWorkspace.preview.value">
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>
              时间
            </th>
            <th>
              原因
            </th>
            <th>
              版本
            </th>
            <th>
              大小
            </th>
            <th>
              校验
            </th>
            <th>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              示例时间
            </td>
            <td>
              更新前保护点
            </td>
            <td class="mono">
              示例 build
            </td>
            <td>
              1.8 GB
            </td>
            <td>
              <StatusLabel tone="success" label="可恢复"/>
            </td>
            <td>
              <button disabled class="text-button">
                <RotateCcw :size="15"/>
                恢复
              </button>
            </td>
          </tr>
          <tr>
            <td>
              昨天 23:10
            </td>
            <td>
              手动创建
            </td>
            <td class="mono">
              示例 build
            </td>
            <td>
              1.7 GB
            </td>
            <td>
              <StatusLabel tone="success" label="已校验"/>
            </td>
            <td>
              <button disabled class="text-button">
                详情
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
