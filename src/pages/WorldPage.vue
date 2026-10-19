<script setup lang="ts">
import { useWorkspace } from "../composables/useWorkspace";
const domainWorkspace=useWorkspace();
import { usePageTarget } from "../composables/usePageTarget";
const { instanceName } = usePageTarget();
import AppSelect from "../components/AppSelect.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import { Save, Eye } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
</script>
<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="世界">
      <template #actions>
        <button disabled class="button">
          <Eye :size="16"/>
          预览变更
        </button>
        <button disabled class="button primary">
          <Save :size="16"/>
          保存并应用
        </button>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <template v-if="domainWorkspace.preview.value">
    <div class="form-grid form-grid-wide">
      <label>
        房间名称
        <input disabled :value="instanceName" />
      </label>
      <label>
        游戏模式
        <AppSelect model-value="survival" :options="[{label:'生存',value:'survival'},{label:'无尽',value:'endless'}]" label="游戏模式" disabled/>
      </label>
      <label>
        最大人数
        <input disabled value="6" type="number" />
      </label>
      <label>
        洞穴分片
        <div class="switch-field">
          <input disabled type="checkbox" checked />
          启用洞穴
        </div>
      </label>
    </div>
    <section class="section-block">
      <h2>
        凭据状态
      </h2>
      <div class="credential-row">
        <span>
          Klei Token
        </span>
        <strong class="muted-text">
          未采集
        </strong>
        <button disabled class="text-button">
          替换 Token
        </button>
      </div>
    </section>
    </template>
    <div v-else class="empty-state"><h2>暂不可用</h2></div>
  </div>
</template>
