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
      <div class="section-heading">
        <div><h2>外观</h2><p class="muted-text">界面主题与显示偏好</p></div>
      </div>
      <div class="setting-row">
        <div>
          <strong>主题</strong>
          <small>亮色或暗色，切换后立即生效</small>
        </div>
        <AppSelect :model-value="theme" :options="options" label="主题" @update:model-value="applyTheme"/>
      </div>
    </section>

    <section class="section-block">
      <div class="section-heading">
        <div><h2>数据与存储</h2><p class="muted-text">本机应用数据位置</p></div>
      </div>
      <div class="setting-row">
        <div>
          <strong>数据库</strong>
          <small class="mono">hunger-desolate.sqlite3</small>
        </div>
        <span class="muted-text">本地 SQLite</span>
      </div>
    </section>
  </div>
</template>
