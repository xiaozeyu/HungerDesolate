<script setup lang="ts">
import { useTheme } from "../composables/useTheme";
import PageHeader from "../components/PageHeader.vue";
import AppSelect from "../components/AppSelect.vue";
const { theme, applyTheme } = useTheme();
const options = [{ label: "亮色", value: "light" }, { label: "暗色", value: "dark" }];
</script>

<template>
  <div class="page">
    <PageHeader eyebrow="应用" title="应用设置"/>
    <section class="section-block">
      <div class="setting-row">
        <div>
          <strong>
            主题
          </strong>
        </div>
        <AppSelect :model-value="theme" :options="options" label="主题" @update:model-value="applyTheme"/>
      </div>
      <div class="setting-row">
        <div>
          <strong>
            应用数据
          </strong>
          <small class="mono">
            app.hungerdesolate.hub · hunger-desolate.sqlite3
          </small>
        </div>
        <span class="muted-text">
          本机
        </span>
      </div>
      <div class="setting-row">
        <div>
          <strong>
            远端支持
          </strong>
          <small>
            Docker Compose · Linux amd64
          </small>
        </div>
        <span class="muted-text">
          暂不可用
        </span>
      </div>
    </section>
  </div>
</template>
