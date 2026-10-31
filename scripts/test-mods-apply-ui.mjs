/** Source-level guards for the manual MOD protection-backup choice. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';

test('MOD apply takes an explicit backup choice instead of always creating one', async () => {
  const page = await readFile(resolve(process.cwd(), 'src/pages/ModsPage.vue'), 'utf8');
  const api = await readFile(resolve(process.cwd(), 'src/api/mods.ts'), 'utf8');
  const remote = await readFile(resolve(process.cwd(), 'remote-scripts/actions/mods-apply.py'), 'utf8');
  assert.match(page, /const createProtectionBackup = ref\(false\)/);
  assert.match(page, /应用前创建冷备份/);
  assert.match(page, /@click="openApplyPreview"/);
  assert.doesNotMatch(page, /openApplyPreview\(true\)/);
  assert.match(page, /createProtectionBackup: createProtectionBackup\.value/);
  assert.doesNotMatch(page, /不创建时，应用失败无法自动恢复原部署/);
  assert.doesNotMatch(page, /listModApplyProtectionBackups|protectionBackup|MOD_PROTECTION_BACKUP_REQUIRED/);
  assert.doesNotMatch(page, /class="mod-apply-options"/);
  const previewStart = page.indexOf('<AppDialog v-model:open="applyPreviewOpen"');
  const previewEnd = page.indexOf('</AppDialog>', previewStart);
  assert.ok(previewStart >= 0 && previewEnd > previewStart);
  assert.match(page.slice(previewStart, previewEnd), /v-model="createProtectionBackup"/);
  assert.doesNotMatch(api, /listModApplyProtectionBackups/);
  assert.match(api, /createProtectionBackup: boolean/);
  assert.match(remote, /if request\["createProtectionBackup"\]:/);
  assert.match(remote, /type\(request\.get\("createProtectionBackup"\)\) is not bool/);
  assert.doesNotMatch(remote, /has_cold_protection|verify_gate|restore_gate|has_world_data|MOD_RESTORE_ACCEPTANCE_REQUIRED/);
  const preview = await readFile(resolve(process.cwd(), 'src/lib/modApplyPreview.ts'), 'utf8');
  assert.doesNotMatch(preview, /MOD_RESTORE_ACCEPTANCE_REQUIRED/);
});
