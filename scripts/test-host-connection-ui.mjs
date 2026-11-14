/** Source guards for the direct host-connect and fingerprint-confirmation flow. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';

test('host list connects directly and confirms fingerprints without a settings gate', async () => {
  const hosts = await readFile(resolve(process.cwd(), 'src/pages/HostsPage.vue'), 'utf8');
  const form = await readFile(resolve(process.cwd(), 'src/components/ServerForm.vue'), 'utf8');
  const connector = await readFile(resolve(process.cwd(), 'src/composables/useHostConnection.ts'), 'utf8');
  const trust = await readFile(resolve(process.cwd(), 'src/components/FingerprintTrustDialog.vue'), 'utf8');
  const sshPanel = await readFile(resolve(process.cwd(), 'src/components/SshConnectionPanel.vue'), 'utf8');
  const settings = await readFile(resolve(process.cwd(), 'src/components/ConnectionSettingsDialog.vue'), 'utf8');
  const instanceList = await readFile(resolve(process.cwd(), 'src/pages/InstanceListPage.vue'), 'utf8');
  const hostMods = await readFile(resolve(process.cwd(), 'src/pages/HostModsPage.vue'), 'utf8');
  const environment = await readFile(resolve(process.cwd(), 'src-tauri/src/environment.rs'), 'utf8');
  const registry = await readFile(resolve(process.cwd(), 'src-tauri/src/registry.rs'), 'utf8');

  assert.doesNotMatch(hosts, /savedMessage|savedServerId|已添加|配置连接/);
  assert.match(hosts, /测试连接/);
  assert.match(hosts, /connectHost/);
  assert.match(hosts, /FingerprintTrustDialog/);
  assert.match(hosts, /connection\.busyServerId\.value === host\.serverId/);

  assert.match(form, /testConnection/);
  assert.match(form, /emit\("tested"/);
  assert.match(form, /@submit\.prevent="save\(false\)"/);

  assert.match(connector, /SSH_TRUST_REQUIRED/);
  assert.match(connector, /SSH_FINGERPRINT_CHANGED/);
  assert.match(connector, /scanFingerprint/);
  assert.match(connector, /confirmFingerprint/);
  assert.match(connector, /connectServer/);
  assert.match(hosts, /managementState/);

  assert.match(trust, /指纹已变化/);
  assert.match(trust, /确认指纹/);
  assert.match(sshPanel, /getSshState/);
  assert.doesNotMatch(sshPanel, /FingerprintTrustDialog|scanFingerprint|saveCredentials/);
  assert.match(settings, /FingerprintTrustDialog/);
  assert.match(settings, /hostConnection\.connect/);
  assert.match(form, /saveCredentials/);
  assert.doesNotMatch(form, /放弃输入并载入最新记录/);

  assert.match(environment, /管理目录需要通过初始化/);
  assert.match(registry, /检查注册表与实例清单或刷新/);
  assert.doesNotMatch(instanceList, /连接设置|主机环境|openConnection/);
  assert.match(hostMods, /统一管理MOD，通过Workshop ID或合集链接添加/);
});
