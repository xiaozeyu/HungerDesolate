/** Source guards for Steam Workshop image hosts and collection authors. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';

test('workshop metadata allows current image CDN and resolves every creator', async () => {
  const config = await readFile(resolve(process.cwd(), 'src-tauri/tauri.conf.json'), 'utf8');
  const workshop = await readFile(resolve(process.cwd(), 'src-tauri/src/workshop.rs'), 'utf8');
  assert.match(config, /https:\/\/images\.steamusercontent\.com/);
  assert.match(workshop, /fetch_authors/);
  assert.doesNotMatch(workshop, /items\.len\(\) <= 12/);
});
