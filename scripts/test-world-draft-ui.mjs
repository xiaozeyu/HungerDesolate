/** World drafts survive navigation but are isolated by target and base revision. */
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
let vite,readWorldDraft,writeWorldDraft,clearWorldDraft;
before(async()=>{
  vite=await createServer({configFile:false,logLevel:'error',optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,watch:null}});
  ({readWorldDraft,writeWorldDraft,clearWorldDraft}=await vite.ssrLoadModule('/src/lib/worldDraft.ts'));
});
after(async()=>{await vite?.close();});
const target={serverId:'server-a',hostId:'11111111-1111-4111-8111-111111111111',instanceId:'22222222-2222-4222-8222-222222222222'};
const other={...target,instanceId:'33333333-3333-4333-8333-333333333333'};
function settings(roomName){return {roomName,description:'',maxPlayers:8,gameMode:'survival',pvp:false,cavesEnabled:true,autosave:true,advanced:{voteKickPercentage:50,whitelistSlots:0,whitelist:[],adminList:[],blocklist:[]},worldgen:{master:{},caves:{}}};}
test('draft is keyed by target and revision and copied on read/write',()=>{
  const draft=settings('草稿世界');
  writeWorldDraft(target,7,draft);
  draft.roomName='被外部修改';
  assert.equal(readWorldDraft(target,7)?.roomName,'草稿世界');
  assert.equal(readWorldDraft(target,8),null);
  assert.equal(readWorldDraft(other,7),null);
});
test('clear removes every revision for one target and leaves other targets intact',()=>{
  writeWorldDraft(target,7,settings('七'));
  writeWorldDraft(target,8,settings('八'));
  writeWorldDraft(other,8,settings('另一个'));
  clearWorldDraft(target);
  assert.equal(readWorldDraft(target,7),null);
  assert.equal(readWorldDraft(target,8),null);
  assert.equal(readWorldDraft(other,8)?.roomName,'另一个');
  clearWorldDraft(other);
});
