/** Exercise the production composable with mocked IPC; never contact a real host. */
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";
import { createRenderer, defineComponent, nextTick, ref } from "vue";

let vite, hosts, useHostEnvironment, dockerTasks;
const virtualId = "\0docker-install-test-hosts";
before(async () => {
  vite = await createServer({
    optimizeDeps: {noDiscovery: true, include: []}, configFile: false, logLevel: "error", server: { middlewareMode: true, hmr: false, watch: null },
    plugins: [{
      name: "test-host-ipc", enforce: "pre",
      resolveId(id, importer) { if (id === "virtual:test-hosts" || (/\/api\/hosts(?:\.ts)?$/.test(id) && importer?.endsWith("useHostEnvironment.ts"))) return virtualId; },
      configureServer(server) { server.ws.listen = () => {}; },
      load(id) {
        if (id !== virtualId) return;
        return `export const handlers = {};
          ${["getHostEnvironment", "getHostInitialization", "previewHostInitialization", "initializeHost", "previewDockerInstallation", "startDockerInstallation", "getDockerInstallationStatus"].map(name => `export function ${name}(request) { return handlers.${name}(request); }`).join("\n")}`;
      },
    }],
  });
  hosts = (await vite.ssrLoadModule("virtual:test-hosts")).handlers;
  ({ useHostEnvironment } = await vite.ssrLoadModule("/src/composables/useHostEnvironment.ts"));
  const { useDockerTasks } = await vite.ssrLoadModule("/src/composables/useDockerTasks.ts");
  dockerTasks = useDockerTasks();
});
after(async () => { await vite?.close(); });
const hostId = "33333333-3333-4333-8333-333333333333";
const operationId = "22222222-2222-4222-8222-222222222222";
const renderer = createRenderer({
  createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null,
  nextSibling: () => null, createElement: () => ({}), createText: () => ({}),
  setText() {}, setElementText() {}, patchProp() {},
});
function envelope(request) { return {schemaVersion:1, requestId:request.requestId, serverId:request.serverId, recordVersion:request.expectedRecordVersion}; }
function task() { return {schemaVersion:1, taskId:operationId, hostId, sequence:1, status:"queued", phase:"accepted", messageCode:null, startedAtUnixSeconds:1700000000, updatedAtUnixSeconds:1700000000, installedVersions:{}}; }
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve=yes; reject=no; }); return {promise, resolve, reject}; }
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)); await nextTick(); };
async function mount() {
  hosts.getHostEnvironment = async request => ({...envelope(request), hostId, environmentCheckId:"check", checkedAtUnixSeconds:1700000000, checks:[]});
  hosts.getHostInitialization = async request => ({...envelope(request), plan:null});
  hosts.getDockerInstallationStatus = async request => ({...envelope(request), hostId, task:null});
  let state;
  const server=ref({serverId:"server-a", hostId, recordVersion:3});
  const app=renderer.createApp(defineComponent({setup() { state=useHostEnvironment(server,ref(false)); return () => null; }}));
  app.mount({});
  await flush();
  state.dockerPreview.value = {planId:"11111111-1111-4111-8111-111111111111", operationId, preview:{status:"ready", snapshotDigest:"a".repeat(64), expiresAtUnixSeconds:1900000000}};
  return {state, server, unmount:() => app.unmount()};
}

test("confirmation immediately shows submission and admits one queued task", async () => {
  const view=await mount(), reply=deferred();
  let calls=0, statusCalls=0, request;
  hosts.startDockerInstallation = input => {calls++; request=input; return reply.promise;};
  hosts.getDockerInstallationStatus = async input => {statusCalls++; return {...envelope(input), hostId, task:null};};
  try {
    const pending=view.state.startDockerInstall();
    assert.equal(view.state.dockerStarting.value,true);
    assert.equal(view.state.dockerTaskLoading.value,true);
    await view.state.startDockerInstall();
    await view.state.refreshDockerTask();
    assert.equal(calls,1, JSON.stringify(view.state.dockerTaskError.value));
    assert.equal(statusCalls,0);
    reply.resolve({...envelope(request), hostId, task:task()});
    await pending;
    assert.equal(view.state.dockerStarting.value,false);
    assert.equal(view.state.dockerTask.value.taskId,operationId);
    assert.equal(view.state.dockerTaskError.value,null);
  } finally { view.unmount(); }
});

test("start error survives reconciliation that finds no task", async () => {
  const view=await mount();
  hosts.startDockerInstallation=async () => {throw {code:"PLAN_STALE",message:"安装预检已过期",recoverySteps:[]};};
  try {
    await view.state.startDockerInstall();
    assert.equal(view.state.dockerStarting.value,false);
    assert.equal(view.state.dockerTaskLoading.value,false);
    assert.equal(view.state.dockerTask.value,null);
    assert.equal(view.state.dockerTaskError.value.code,"PLAN_STALE");
  } finally {view.unmount();}
});

test("a rejected new operation never displays the previous failed task", async () => {
  const view=await mount();
  const oldTask={...task(), taskId:"44444444-4444-4444-8444-444444444444", status:"failed", phase:"failed", messageCode:"INSTALL_FAILED"};
  hosts.startDockerInstallation=async () => {throw {code:"PLAN_STALE",message:"安装预检已过期或主机状态变化，请重新检查"};};
  hosts.getDockerInstallationStatus=async request => ({...envelope(request),hostId,task:request.operationId===operationId ? null : oldTask});
  try {
    await view.state.startDockerInstall();
    assert.equal(view.state.dockerTask.value,null);
    assert.equal(view.state.dockerTaskError.value.code,"PLAN_STALE");
  } finally {view.unmount();}
});

test("lost admission response can still discover the accepted task", async () => {
  const view=await mount();
  hosts.startDockerInstallation=async () => {throw new Error("响应丢失");};
  hosts.getDockerInstallationStatus=async request => ({...envelope(request),hostId,task:task()});
  try {
    await view.state.startDockerInstall();
    assert.equal(view.state.dockerTask.value.taskId,operationId);
    assert.equal(view.state.dockerStarting.value,false);
    assert.equal(view.state.dockerTaskError.value,null);
  } finally {view.unmount();}
});

test("a response for a different task cannot replace the tracked operation", async () => {
  const view=await mount();
  dockerTasks.receive("server-a",hostId,task());
  hosts.getDockerInstallationStatus=async request => {
    assert.equal(request.operationId,operationId);
    return {...envelope(request),hostId,task:{...task(),taskId:"44444444-4444-4444-8444-444444444444"}};
  };
  try {
    await view.state.refreshDockerTask();
    assert.equal(view.state.dockerTask.value.taskId,operationId);
    assert.ok(view.state.dockerTaskError.value);
  } finally {view.unmount();}
});

test("late admission response cannot update a different host or reset its busy state", async () => {
  const view=await mount(), reply=deferred();
  let request;
  hosts.startDockerInstallation=input => {request=input;return reply.promise;};
  try {
    const pending=view.state.startDockerInstall();
    view.server.value={serverId:"server-b",hostId:"55555555-5555-4555-8555-555555555555",recordVersion:3};
    await flush();
    reply.resolve({...envelope(request),hostId,task:task()});
    await pending;
    assert.equal(view.state.dockerTask.value,null);
    assert.equal(view.state.dockerStarting.value,false);
    assert.equal(view.state.dockerTaskError.value,null);
  } finally {view.unmount();}
});
