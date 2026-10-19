<script setup lang="ts">
import { computed } from "vue";
import { Play, Square, RotateCw, LockKeyhole } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import { useInstanceOverview } from "../composables/useInstanceOverview";
const { data, error, loading } = useInstanceOverview();
const name = computed(() => data.value?.identity.displayName ?? "专服");
const status = computed(() => data.value?.identity.runtimeStatus === 'running' ? '运行中' : data.value?.identity.runtimeStatus === 'stopped' ? '已停止' : '未知');
</script>

<template>
  <div class="page">
    <PageHeader :eyebrow="name" title="专服概览">
      <template #actions>
        <button class="button" disabled>
          <RotateCw :size="16"/>
          重启
        </button>
        <button class="button primary" disabled>
          <Play :size="16"/>
          启动专服
        </button>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <div v-if="loading" class="empty-state" aria-live="polite">
      <p>
        读取当前专服…
      </p>
    </div>
    <div v-else-if="error" class="error-strip" role="alert">
      {{error}}
    </div>
    <template v-else-if="data">
      <section class="identity-strip">
        <div>
          <span class="label">
            实例身份
          </span>
          <strong class="mono identity-value">
            {{data.identity.instanceId}}
          </strong>
        </div>
        <div>
          <span class="label">
            实际版本
          </span>
          <strong class="mono">
            {{data.identity.installedBuild ?? '未知'}}
          </strong>
        </div>
        <StatusLabel :tone="data.identity.runtimeStatus==='running'?'success':'muted'" :label="status"/>
      </section>
      <div class="overview-grid">
        <section class="section-block">
          <div class="section-heading">
            <div>
              <h2>
                分片状态
              </h2>
            </div>
          </div>
          <div class="shard-list">
            <div class="shard-row">
              <div>
                <strong>
                  主世界 · Master
                </strong>
                <small class="mono">
                  游戏 UDP · 未采集
                </small>
              </div>
              <StatusLabel tone="muted" label="未核实"/>
            </div>
            <div v-if="data.manifestSummary?.cavesEnabled !== false" class="shard-row">
              <div>
                <strong>
                  洞穴 · Caves
                </strong>
              </div>
              <StatusLabel tone="muted" label="未核实"/>
            </div>
          </div>
        </section>
        <section class="section-block">
          <div class="section-heading">
            <div>
              <h2>
                部署状态
              </h2>
            </div>
          </div>
          <div class="fact-list">
            <div>
              <span>
                DST 版本
              </span>
              <strong class="mono">
                {{data.identity.installedBuild ?? '未知'}}
              </strong>
            </div>
            <div>
              <span>
                MOD 发布
              </span>
              <StatusLabel tone="muted" label="未核实"/>
            </div>
            <div>
              <span>
                最近备份
              </span>
              <strong>
                未采集
              </strong>
            </div>
            <div>
              <span>
                配置版本
              </span>
              <strong>
                {{data.manifestSummary?.configRevision ?? '未采集'}}
              </strong>
            </div>
          </div>
        </section>
      </div>
      <section class="section-block blockers">
        <div class="section-heading">
          <div>
            <h2>
              阻断项
            </h2>
          </div>
        </div>
        <div class="empty-inline">
          <LockKeyhole :size="18"/>
          <span>
            {{data.blockers.join('；')}}
          </span>
          <button class="button subtle" disabled>
            <Square :size="15"/>
            停止专服
          </button>
        </div>
      </section>
    </template>
  </div>
</template>
