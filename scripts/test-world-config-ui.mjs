/** Source-level guards for the single-step world save-and-apply flow. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';

test('world save-and-apply submits one check-and-apply call without a preview dialog', async () => {
  const page = await readFile(resolve(process.cwd(), 'src/pages/WorldPage.vue'), 'utf8');
  const api = await readFile(resolve(process.cwd(), 'src/api/world.ts'), 'utf8');
  assert.doesNotMatch(page, /previewWorldApply|preview_world_apply|applyPreview|previewBusy|previewApply/);
  assert.doesNotMatch(page, /AppDialog|应用世界配置预览|预览应用/);
  assert.doesNotMatch(api, /previewWorldApply|preview_world_apply|WorldApplyPreview|PreviewWorldApplyRequest/);
  assert.match(page, /async function saveAndApply\(\)[\s\S]*await save\(\)[\s\S]*await applyWorld\(\)/);
  const call = page.match(/applyWorldConfig\(\{[\s\S]*?\}\);/)?.[0] ?? "";
  assert.ok(call, "WorldPage must submit apply_world_config");
  assert.match(call, /operationId: crypto\.randomUUID\(\)/);
  assert.doesNotMatch(call, /desiredDigest|activeReleaseId/);
  assert.match(page, /applyBlockerLabel\(code\)/);
  assert.match(page, /result\.warnings/);
  assert.match(page, /保存并应用/);
  assert.doesNotMatch(page, /仅保存/);
  assert.match(page, /<NFormItemGi v-for="item in selectedGroupOptions"/);
  assert.match(page, /<AppSelect :model-value="optionValue/);
  assert.match(page, /resetActiveGroup/);
  assert.match(page, /listStats\('whitelist'\)/);
});
