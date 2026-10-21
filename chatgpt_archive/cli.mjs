#!/usr/bin/env node
// Local runner. The source is an explicitly configured, authorized stdio MCP server.
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { createInterface } from 'node:readline';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

export class McpClient {
  constructor(command, args = []) {
    this.pending = new Map();
    this.sequence = 0;
    this.process = spawn(command, args, { stdio: ['pipe', 'pipe', 'pipe'] });
    // Do not print server stderr: it may contain conversation content or credentials.
    this.process.stderr.resume();
    this.process.on('error', () => this.fail(new Error('MCP 서버를 시작할 수 없습니다. 실행 경로를 확인하세요.')));
    this.process.on('exit', () => this.fail(new Error('MCP 서버 연결이 종료됐습니다.')));
    this.process.stdin.on('error', () => this.fail(new Error('MCP 서버에 요청을 전달하지 못했습니다.')));
    this.lines = createInterface({ input: this.process.stdout });
    this.lines.on('line', line => {
      let message;
      try { message = JSON.parse(line); } catch { return; }
      if (message.method && message.id != null) {
        // No sampling, elicitation, or server-initiated execution.
        const reply = message.method === 'ping' ? { result: {} }
          : { error: { code: -32601, message: 'Client capability not supported' } };
        this.send({ jsonrpc: '2.0', id: message.id, ...reply });
        return;
      }
      const pending = this.pending.get(message.id);
      if (!pending) return;
      clearTimeout(pending.timer);
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error('MCP 요청이 실패했습니다 (code: ' + message.error.code + ').'));
      else pending.resolve(message.result);
    });
  }
  send(message) { this.process.stdin.write(JSON.stringify(message) + '\n'); }
  fail(error) {
    this.failure = error;
    for (const entry of this.pending.values()) { clearTimeout(entry.timer); entry.reject(error); }
    this.pending.clear();
  }
  request(method, params = {}) {
    if (this.failure) return Promise.reject(this.failure);
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('MCP 요청 시간 초과: ' + method));
      }, 60000);
      this.pending.set(id, { resolve, reject, timer });
      this.send({ jsonrpc: '2.0', id, method, params });
    });
  }
  async connect() {
    await this.request('initialize', { protocolVersion: '2024-11-05', capabilities: {},
      clientInfo: { name: 'chatgpt-archive', version: '1.0.0' } });
    this.send({ jsonrpc: '2.0', method: 'notifications/initialized' });
    const names = new Set();
    const cursors = new Set();
    let cursor;
    do {
      const page = await this.request('tools/list', cursor ? { cursor } : {});
      for (const tool of page.tools || []) names.add(tool.name);
      cursor = page.nextCursor;
      if (cursor && cursors.has(cursor)) throw new Error('MCP 도구 목록 커서가 반복됩니다.');
      if (cursor) cursors.add(cursor);
    } while (cursor);
    return names;
  }
  close() {
    this.fail(new Error('MCP 연결 닫힘'));
    this.lines.close();
    this.process.stdin.end();
    this.process.kill();
    const timer = setTimeout(() => this.process.kill('SIGKILL'), 1000);
    timer.unref();
  }
}

function execute(cmd) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn('/bin/sh', ['-c', cmd], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
    let output = '';
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.resume();
    const timer = setTimeout(() => child.kill('SIGKILL'), 120000);
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', code => {
      clearTimeout(timer);
      resolvePromise({ exit_code: code ?? 1, output: code === 0 ? output : '로컬 보관 명령 실패. Python 및 출력 폴더 권한을 확인하세요.' });
    });
  });
}

export async function main(argv) {
  if (argv.includes('--help') || !argv.length) {
    console.log('node chatgpt_archive/cli.mjs <doctor|collect> --config FILE [--output DIR] [--full] [--thread-id ID]');
    console.log('FILE: {"command":"/absolute/path/to/authorized-mcp-server","args":[],"listTool":"list_threads","readTool":"read_thread"}');
    return 0;
  }
  const command = argv.shift();
  if (!['doctor', 'collect'].includes(command)) throw new Error('명령은 doctor 또는 collect여야 합니다.');
  let configPath;
  const options = { root, threadIds: [] };
  while (argv.length) {
    const arg = argv.shift();
    if (arg === '--full') { options.full = true; continue; }
    if (!['--config', '--output', '--thread-id'].includes(arg) || !argv.length) throw new Error('알 수 없거나 값이 없는 옵션: ' + arg);
    const value = argv.shift();
    if (arg === '--config') configPath = value;
    if (arg === '--output') options.output = resolve(value);
    if (arg === '--thread-id') options.threadIds.push(value);
  }
  if (!configPath) throw new Error('--config가 필요합니다. 앱 전용 대화 조회 도구는 일반 CLI에 자동 연결되지 않습니다.');
  const config = JSON.parse(await readFile(configPath, 'utf8'));
  if (typeof config.command !== 'string' || !config.command.startsWith('/') ||
      (config.args !== undefined && (!Array.isArray(config.args) || config.args.some(x => typeof x !== 'string'))))
    throw new Error('config에는 MCP 서버의 절대 실행 경로 command와 문자열 배열 args가 필요합니다.');
  const client = new McpClient(config.command, config.args);
  try {
    const names = await client.connect();
    const listTool = config.listTool || 'list_threads';
    const readTool = config.readTool || 'read_thread';
    if (!names.has(listTool) || !names.has(readTool))
      throw new Error('대화 조회 도구가 없습니다. ChatGPT 목록/본문을 제공하는 MCP 서버가 필요합니다.');
    if (command === 'doctor') {
      console.log(JSON.stringify({ connected: true, tools: [listTool, readTool], note: '도구 존재 확인만 완료. 실제 조회 권한과 응답 형식은 collect로 검증해야 합니다.' }));
      return 0;
    }
    const tools = {
      mcp__codex_app__list_threads: args => client.request('tools/call', { name: listTool, arguments: args }),
      mcp__codex_app__read_thread: args => client.request('tools/call', { name: readTool, arguments: args }),
      exec_command: args => execute(args.cmd),
    };
    const source = await readFile(resolve(root, 'chatgpt_archive/collector.js'), 'utf8');
    const report = await new Function('tools', 'options', source)(tools, options);
    console.log(JSON.stringify(report));
    return report.new_errors?.length ? 2 : 0;
  } finally { client.close(); }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then(code => { process.exitCode = code; }).catch(error => {
    console.error(JSON.stringify({ error: error.message }));
    process.exitCode = 1;
  });
}
