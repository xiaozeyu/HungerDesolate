/** Persisted task/build state is restored at startup and capped. */
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
let vite,taskStorage,buildStorage,useTasks,useBuildTasks;
const memory=new Map();
globalThis.localStorage={getItem:key=>memory.get(key)??null,setItem:(key,value)=>memory.set(key,String(value)),removeItem:key=>memory.delete(key),clear:()=>memory.clear(),key:index=>[...memory.keys()][index]??null,get length(){return memory.size;}};
const hostId='33333333-3333-4333-8333-333333333333';
function task(index,status='queued'){return {schemaVersion:1,taskId:`task-${index}`,operationId:`op-${index}`,target:{serverId:'server-a',hostId,instanceId:'22222222-2222-4222-8222-222222222222'},operation:'instanceCreate',status,stage:'reserve',lastCompletedStage:null,progress:{kind:'indeterminate'},sequence:1,createdAt:'2026-10-31T00:00:00Z',startedAt:null,finishedAt:null,cancelAllowed:false,observation:'live',result:null,error:null,journal:[]};}
function build(){return {serverId:'server-a',hostId,recordVersion:3,releaseId:'release-a',message:'构建中',status:{schemaVersion:1,requestId:'r',releaseId:'release-a',status:'building',logTail:[]},startedAt:1,updatedAt:2,observationError:''};}
before(async()=>{
  vite=await createServer({configFile:false,logLevel:'error',optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,watch:null}});
  taskStorage=await vite.ssrLoadModule('/src/lib/taskStorage.ts');
  buildStorage=await vite.ssrLoadModule('/src/lib/buildStorage.ts');
  taskStorage.saveTasks([task(1)]);
  buildStorage.saveBuildTasks([build()]);
  ({useTasks}=await vite.ssrLoadModule('/src/composables/useTasks.ts'));
  ({useBuildTasks}=await vite.ssrLoadModule('/src/composables/useBuildTasks.ts'));
});
after(async()=>{await vite?.close();globalThis.localStorage=undefined;});
test('task store restores saved active tasks and persists updates',()=>{
  const tasks=useTasks();
  assert.equal(tasks.tasks.value.some(item=>item.taskId==='task-1'),true);
  tasks.receive(task(2));
  const saved=JSON.parse(memory.get('hunger-desolate.tasks.v1'));
  assert.equal(saved.some(item=>item.taskId==='task-2'),true);
});
test('build store restores saved builds and persists terminal updates',()=>{
  const builds=useBuildTasks();
  assert.equal(builds.forHost(hostId)?.releaseId,'release-a');
  builds.receive('server-a',hostId,3,{schemaVersion:1,requestId:'r',releaseId:'release-a',status:'completed',logTail:['done']});
  const saved=JSON.parse(memory.get('hunger-desolate.builds.v1'));
  assert.equal(saved[0]?.status?.status,'completed');
});
test('task storage survives malformed data and caps history',()=>{
  memory.set('hunger-desolate.tasks.v1','not-json');
  assert.deepEqual(taskStorage.loadTasks(),[]);
  const many=Array.from({length:85},(_,index)=>task(index,'succeeded'));
  taskStorage.saveTasks(many);
  const loaded=taskStorage.loadTasks();
  assert.equal(loaded.length,80);
  assert.equal(loaded[0].taskId,'task-5');
});
