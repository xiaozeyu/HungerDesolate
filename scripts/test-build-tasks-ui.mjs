/** Global build task store keeps Release builds observable after navigation. */
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
let vite,useBuildTasks;
before(async()=>{
  vite=await createServer({configFile:false,logLevel:'error',optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,watch:null}});
  ({useBuildTasks}=await vite.ssrLoadModule('/src/composables/useBuildTasks.ts'));
});
after(async()=>{await vite?.close();});
const hostId='33333333-3333-4333-8333-333333333333';
const releaseId='44444444-4444-4444-8444-444444444444';
test('build store keeps start message, progress and observation errors per host',()=>{
  const tasks=useBuildTasks();
  tasks.clear(hostId);
  tasks.start('server-a',hostId,3,releaseId,'开始构建');
  assert.equal(tasks.forHost(hostId)?.status.status,'building');
  assert.equal(tasks.forHost(hostId)?.message,'开始构建');
  tasks.receive('server-a',hostId,3,{schemaVersion:1,requestId:'r',releaseId,status:'building',progress:{currentStep:'构建镜像层',percent:42},logTail:['step']});
  assert.equal(tasks.forHost(hostId)?.status.progress?.percent,42);
  assert.equal(tasks.forHost(hostId)?.message,'开始构建');
  tasks.error(hostId,'查询超时');
  assert.equal(tasks.forHost(hostId)?.observationError,'查询超时');
  tasks.receive('server-a',hostId,3,{schemaVersion:1,requestId:'r',releaseId,status:'completed',logTail:['done']});
  assert.equal(tasks.forHost(hostId)?.status.status,'completed');
  assert.equal(tasks.forHost(hostId)?.observationError,'');
  tasks.clear(hostId);
  assert.equal(tasks.forHost(hostId),null);
});
test('a response for another release cannot replace the tracked build',()=>{
  const tasks=useBuildTasks();
  tasks.clear(hostId);
  tasks.start('server-a',hostId,3,releaseId,'开始构建');
  tasks.receive('server-a',hostId,3,{schemaVersion:1,requestId:'r',releaseId:'55555555-5555-4555-8555-555555555555',status:'completed',logTail:[]});
  assert.equal(tasks.forHost(hostId)?.status.status,'building');
  assert.equal(tasks.forHost(hostId)?.releaseId,releaseId);
});
