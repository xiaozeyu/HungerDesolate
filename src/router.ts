import { createRouter, createWebHashHistory } from "vue-router";
import { useWorkspace } from "./composables/useWorkspace";
import { activateContext } from "./context/activeContext";
import { isUuid, sameTarget } from "./lib/navigationGuard";
import { activeContext } from "./context/activeContext";
import { canInspectInstance } from "./lib/instanceList";
import HostsPage from "./pages/HostsPage.vue";
import InstanceListPage from "./pages/InstanceListPage.vue";
import HostEnvironmentPage from "./pages/HostEnvironmentPage.vue";
import HostModsPage from "./pages/HostModsPage.vue";
import ReleaseManagementPage from "./pages/ReleaseManagementPage.vue";
import InstanceOverviewPage from "./pages/InstanceOverviewPage.vue";
import WorldPage from "./pages/WorldPage.vue";
import ModsPage from "./pages/ModsPage.vue";
import BackupsPage from "./pages/BackupsPage.vue";
import DiagnosticsPage from "./pages/DiagnosticsPage.vue";
import SettingsPage from "./pages/SettingsPage.vue";
export const router = createRouter({
    history: createWebHashHistory(),
    routes: [
        { path: "/", redirect: "/hosts" },
        { path: "/hosts", component: HostsPage, meta: { title: "主机" } },
        { path: "/hosts/:serverId/instances", component: InstanceListPage, meta: { title: "专服列表" } },
        { path: "/hosts/:serverId/mods", component: HostModsPage, meta: { title: "主机 MOD 库" } },
        { path: "/hosts/:serverId/environment", component: HostEnvironmentPage, meta: { title: "主机环境" } },
        { path: "/hosts/:serverId/releases", component: ReleaseManagementPage, meta: { title: "Release 管理" } },
        ...[
            { name: "overview", title: "概览", component: InstanceOverviewPage },
            { name: "world", title: "世界", component: WorldPage },
            { name: "mods", title: "MOD", component: ModsPage },
            { name: "backups", title: "备份", component: BackupsPage },
            { name: "diagnostics", title: "诊断", component: DiagnosticsPage },
        ].map(page => ({ path: `/hosts/:serverId/instances/:instanceId/${page.name}`, component: page.component, meta: { title: page.title } })),
        { path: "/settings", component: SettingsPage, meta: { title: "应用设置" } },
        { path: "/:pathMatch(.*)*", redirect: "/hosts" },
    ],
});
router.beforeEach(async to => {
    const workspace = useWorkspace();
    const serverId = typeof to.params.serverId === "string" ? to.params.serverId : null;
    const instanceId = typeof to.params.instanceId === "string" ? to.params.instanceId : null;
    if (!serverId)
        return true;
    if (!workspace.preview.value) await workspace.ensureLoaded();
    const server = workspace.servers.value.find(item => item.serverId === serverId);
    if (!isUuid(serverId) || !server)
        return "/hosts";
    if (!instanceId)
        return true;
    if (!isUuid(instanceId) || !server.hostId)
        return `/hosts/${serverId}/instances`;
    const target = { serverId, hostId: server.hostId, instanceId };
    if (!workspace.canEnter(target) || !workspace.instances(server.hostId).some(item => item.instanceId === instanceId && canInspectInstance(item))) {
        return `/hosts/${serverId}/instances`;
    }
    return true;
});
router.afterEach(to => {
    if (to.path === "/settings")
        return;
    const workspace = useWorkspace();
    const serverId = String(to.params.serverId ?? "");
    const instanceId = String(to.params.instanceId ?? "");
    const hostId = workspace.servers.value.find(server => server.serverId === serverId)?.hostId;
    const target = serverId && instanceId && hostId ? { serverId, hostId, instanceId } : null;
    if (!sameTarget(activeContext.value.target, target))
        activateContext(target);
    if (!instanceId)
        workspace.clearSelection();
});
