import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { spawnSync } from 'node:child_process';

test('CLI persists original messages and skips unchanged conversations', async () => {
  const temp = await mkdtemp(join(tmpdir(), 'archive-cli-test-'));
  try {
    const config = join(temp, 'source.json');
    const output = join(temp, 'archive with spaces');
    await writeFile(config, JSON.stringify({ command: process.execPath, args: [resolve('chatgpt_archive/tests/mcp_fixture.mjs')] }));
    const run = command => spawnSync(process.execPath, ['chatgpt_archive/cli.mjs', command, '--config', config, '--output', output], { encoding: 'utf8', timeout: 15000 });
    assert.equal(run('doctor').status, 0);
    const first = run('collect');
    assert.equal(first.status, 0, first.stderr);
    assert.equal(JSON.parse(first.stdout).changed, 1);
    const names = await readdir(join(output, 'conversations'));
    const content = await readFile(join(output, 'conversations', names.find(x => x.endsWith('.md'))), 'utf8');
    assert.ok(content.includes('$(touch /tmp/never-execute-archive-fixture)'));
    const second = run('collect');
    assert.equal(second.status, 0, second.stderr);
    assert.equal(JSON.parse(second.stdout).changed, 0);
  } finally { await rm(temp, { recursive: true, force: true }); }
});

test('missing source fails explicitly', () => {
  const result = spawnSync(process.execPath, ['chatgpt_archive/cli.mjs', 'collect'], { encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /--config/);
});
