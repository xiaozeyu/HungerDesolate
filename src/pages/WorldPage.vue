<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useWorkspace } from "../composables/useWorkspace";
import { usePageTarget } from "../composables/usePageTarget";
import AppSelect from "../components/AppSelect.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
import { Save, Eye, RefreshCw } from "@lucide/vue";
import type { WorldSettings } from "../types/world";

const domainWorkspace = useWorkspace();
const { instanceName } = usePageTarget();

// 临时状态（待实现真实读取）
const loading = ref(false);
const saving = ref(false);
const tokenStatus = ref<"configured" | "missing" | "unknown">("unknown");
const desiredVsDeployed = ref<"inSync" | "pending" | "drifted" | "unknown">("unknown");

const draft = ref<WorldSettings>({
  roomName: instanceName.value || "",
  description: "",
  gameMode: "survival",
  maxPlayers: 16,
  pvp: false,
  cavesEnabled: true,
  masterRules: {
    worldSize: "medium",
    seasonStart: "autumn",
    dayLength: "default",
  },
  cavesRules: {
    worldSize: "medium",
    seasonStart: "autumn",
    dayLength: "default",
  },
});

const hasChanges = ref(false);
const canApply = computed(() => desiredVsDeployed.value === "pending");
const tokenChanging = ref(false);
const tokenValue = ref("");

const gameModeOptions = [
  { label: "生存", value: "survival" },
  { label: "无尽", value: "endless" },
  { label: "荒野", value: "wilderness" },
];

const worldSizeOptions = [
  { label: "小", value: "small" },
  { label: "中", value: "medium" },
  { label: "大", value: "large" },
  { label: "巨大", value: "huge" },
];

const seasonOptions = [
  { label: "秋", value: "autumn" },
  { label: "冬", value: "winter" },
  { label: "春", value: "spring" },
  { label: "夏", value: "summer" },
];

const dayLengthOptions = [
  { label: "默认", value: "default" },
  { label: "长", value: "long" },
  { label: "短", value: "short" },
];

function refresh() {
  console.log("TODO: 实现刷新");
}

function save() {
  console.log("TODO: 实现保存", { draft: draft.value, tokenChanging: tokenChanging.value });
  saving.value = true;
  setTimeout(() => {
    saving.value = false;
    hasChanges.value = false;
  }, 1000);
}

function apply() {
  console.log("TODO: 实现应用配置");
}

function cancelTokenChange() {
  tokenChanging.value = false;
  tokenValue.value = "";
}

watch(draft, () => {
  hasChanges.value = true;
}, { deep: true });

</script>
<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="世界配置">
      <template #actions>
        <button class="button" :disabled="loading || saving" @click="refresh">
          <RefreshCw :size="16"/>
          {{ loading ? '读取中…' : '刷新' }}
        </button>
        <button v-if="canApply" class="button" :disabled="saving" @click="apply">
          <Eye :size="16"/>
          应用配置
        </button>
        <button class="button primary" :disabled="!hasChanges || saving || loading" @click="save">
          <Save :size="16"/>
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </template>
    </PageHeader>
    <PreviewNotice/>

    <template v-if="domainWorkspace.preview.value">
      <!-- 状态指示 -->
      <div v-if="desiredVsDeployed === 'pending'" class="section-block">
        <StatusLabel tone="warning" label="待应用"/>
        <span>配置已保存，点击"应用配置"以更新运行环境</span>
      </div>

      <!-- 基础设置 -->
      <section class="section-block">
        <h2>基础设置</h2>
        <div class="form-grid">
          <label>
            专服名称
            <input v-model="draft.roomName" maxlength="64" required autocomplete="off"/>
          </label>

          <label>
            说明
            <textarea v-model="draft.description" maxlength="256" rows="3" autocomplete="off"/>
          </label>

          <label>
            游戏模式
            <AppSelect v-model="draft.gameMode" :options="gameModeOptions" label="游戏模式"/>
          </label>

          <label>
            最大玩家数
            <input v-model.number="draft.maxPlayers" type="number" min="1" max="128"/>
          </label>

          <label class="switch-field">
            <input v-model="draft.pvp" type="checkbox"/>
            PVP 模式
          </label>

          <div class="form-field">
            <span>洞穴分片</span>
            <div class="creation-static-value">
              {{ draft.cavesEnabled ? '已启用（容器创建时确定）' : '未启用' }}
            </div>
          </div>
        </div>
      </section>

      <!-- 主世界规则 -->
      <details class="section-block" open>
        <summary><h2>主世界规则</h2></summary>
        <div class="form-grid">
          <label>
            世界大小
            <AppSelect v-model="draft.masterRules.worldSize" :options="worldSizeOptions" label="世界大小"/>
          </label>

          <label>
            起始季节
            <AppSelect v-model="draft.masterRules.seasonStart" :options="seasonOptions" label="起始季节"/>
          </label>

          <label>
            昼夜长度
            <AppSelect v-model="draft.masterRules.dayLength" :options="dayLengthOptions" label="昼夜长度"/>
          </label>
        </div>
      </details>

      <!-- 洞穴规则 -->
      <details v-if="draft.cavesEnabled && draft.cavesRules" class="section-block">
        <summary><h2>洞穴规则</h2></summary>
        <div class="form-grid">
          <label>
            世界大小
            <AppSelect v-model="draft.cavesRules.worldSize" :options="worldSizeOptions" label="世界大小"/>
          </label>

          <label>
            起始季节
            <AppSelect v-model="draft.cavesRules.seasonStart" :options="seasonOptions" label="起始季节"/>
          </label>

          <label>
            昼夜长度
            <AppSelect v-model="draft.cavesRules.dayLength" :options="dayLengthOptions" label="昼夜长度"/>
          </label>
        </div>
      </details>

      <!-- Token 输入 -->
      <section class="section-block">
        <h2>凭据</h2>
        <div class="credential-row">
          <span>Klei Token</span>
          <StatusLabel
            v-if="tokenStatus !== 'unknown'"
            :tone="tokenStatus === 'configured' ? 'success' : 'warning'"
            :label="tokenStatus === 'configured' ? '已配置' : '未配置'"
          />
          <strong v-else class="muted-text">未采集</strong>

          <div v-if="tokenChanging" class="token-input-field">
            <input
              v-model="tokenValue"
              type="password"
              placeholder="pds-g^KU_..."
              autocomplete="off"
              maxlength="256"
            />
            <button class="text-button" @click="cancelTokenChange">取消</button>
          </div>
          <button v-else class="text-button" :disabled="saving" @click="tokenChanging = true">
            {{ tokenStatus === 'configured' ? '更换 Token' : '设置 Token' }}
          </button>
        </div>
        <p v-if="tokenStatus === 'missing'" class="muted-text">
          启动需要认证时必须配置 Token
        </p>
      </section>
    </template>

    <div v-else class="empty-state">
      <h2>暂不可用</h2>
      <p>世界配置需要真实连接</p>
    </div>
  </div>
</template>
