// Synthetic data only; never connects to an account.
import { createInterface } from 'node:readline';
for await (const line of createInterface({ input: process.stdin })) {
  const request = JSON.parse(line);
  if (request.id == null) continue;
  let result;
  if (request.method === 'initialize') result = { protocolVersion: '2024-11-05', capabilities: { tools: {} }, serverInfo: { name: 'fixture', version: '1' } };
  else if (request.method === 'tools/list') result = { tools: [{ name: 'list_threads' }, { name: 'read_thread' }] };
  else if (request.params.name === 'list_threads') result = { structuredContent: { threads: [{ id: 'fixture', kind: 'chatgpt', title: 'CLI fixture', updatedAt: 1 }] } };
  else result = { structuredContent: {
    thread: { id: 'fixture', kind: 'chatgpt', title: 'CLI fixture', updatedAt: 1 },
    page: { hasMore: false, order: 'newest_first' },
    turns: [{ id: 'turn1', status: 'completed', items: [
      { id: 'question', type: 'userMessage', content: [{ type: 'text', text: 'Literal $(touch /tmp/never-execute-archive-fixture) and `echo hello`' }] },
      { id: 'answer', type: 'agentMessage', text: 'Synthetic answer' }
    ] }]
  } };
  process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result }) + '\n');
}
