<script setup lang="ts">
import { useWorkspace } from "../composables/useWorkspace";
const domainWorkspace=useWorkspace();
import { usePageTarget } from "../composables/usePageTarget";
const { instanceName } = usePageTarget();
import PreviewNotice from "../components/PreviewNotice.vue";
import { Download, ShieldCheck } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
</script>
<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="更新">
      <template #actions>
        <button disabled class="button primary">
          <Download :size="16"/>
          创建备份并更新
        </button>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <template v-if="domainWorkspace.preview.value">
    <section class="update-hero">
      <div class="update-icon">
        <ShieldCheck :size="20"/>
      </div>
      <div>
        <strong>
          有可用更新
        </strong>
      </div>
    </section>
    <div class="fact-list wide">
      <div>
        <span>
          已安装 DST
        </span>
        <strong class="mono">
          示例 build
        </strong>
      </div>
      <div>
        <span>
          可用 MOD 更新
        </span>
        <strong>
          2 个
        </strong>
      </div>
      <div>
        <span>
          最近检查
        </span>
        <strong>
          示例时间
        </strong>
      </div>
    </div>
    </template>
    <div v-else class="empty-state"><h2>暂不可用</h2></div>
  </div>
</template>
