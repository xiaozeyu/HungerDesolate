import {describe,it} from 'node:test';
import assert from 'node:assert/strict';
import {validateCreationInput,creationBlockerLabel} from './instanceCreation.ts';
import type {InstanceCreateInput} from '../api/instances.ts';
const input:InstanceCreateInput={displayName:'生存一服',autostart:true,startAfterCreate:false,portPolicy:'auto',cavesEnabled:true};
describe('creation input boundary',()=>{
  it('validates names and only supported inputs',()=>{
    assert.equal(validateCreationInput(input),null);
    for (const change of [{displayName:' '},{displayName:'a'.repeat(65)},{displayName:'a\nb'},{cavesEnabled:'yes'},{startAfterCreate:true,autostart:false}]) assert.notEqual(validateCreationInput({...input,...change} as InstanceCreateInput),null);
  });
  it('maps blockers without echoing arbitrary remote strings',()=>{
    assert.equal(creationBlockerLabel('PORT_PLAN_EXHAUSTED'),'可用端口范围不足');
    assert.equal(creationBlockerLabel('DOCKER_UNAVAILABLE'),'Docker 引擎不可访问');
    assert.equal(creationBlockerLabel('unknown secret'),'创建条件未满足');
    assert.equal(creationBlockerLabel('__proto__'),'创建条件未满足');
  });
});
