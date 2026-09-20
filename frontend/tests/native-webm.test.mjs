import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { spawn } from 'node:child_process';
import { access, mkdtemp, rm } from 'node:fs/promises';
import { createServer as createTcpServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

// Real media decoding uses real time. The virtual-time dump-DOM runner used by
// deterministic behavior tests can suspend while the native media clock runs.
const candidates = [process.env.PLAYER_TEST_BROWSER, 'C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', '/usr/bin/chromium', '/usr/bin/google-chrome'].filter(Boolean);
let browser;
for (const path of candidates) { try { await access(path); browser = path; break; } catch {} }
if (!browser) throw new Error('Set PLAYER_TEST_BROWSER to a locally installed Chromium browser executable.');
const probe = createTcpServer();
await new Promise((done, reject) => { probe.once('error', reject); probe.listen(0, '127.0.0.1', done); });
const port = probe.address().port;
await new Promise((done) => probe.close(done));
let complete;
const completed = new Promise((done) => { complete = done; });
const server = await createServer({
  configFile: false,
  root: fileURLToPath(new URL('../', import.meta.url)),
  plugins: [react(), {
    name: 'native-webm-test-reporter',
    configureServer(vite) {
      vite.middlewares.use('/__native-webm-result', (request, response) => {
        if (request.method !== 'POST') { response.statusCode = 405; response.end(); return; }
        let body = '';
        request.on('data', (chunk) => { body += chunk; if (body.length > 16384) request.destroy(); });
        request.on('end', () => {
          try { complete(JSON.parse(body)); response.end('ok'); }
          catch { response.statusCode = 400; response.end(); }
        });
      });
    },
  }],
  optimizeDeps: { entries: ['tests/native-webm.html'] },
  server: { host: '127.0.0.1', port, strictPort: true, hmr: false },
  logLevel: 'error',
});
const profile = await mkdtemp(join(tmpdir(), 'clipfeed-native-webm-test-'));
let child;
let timeout;
let stderr = '';
try {
  await server.listen();
  child = spawn(browser, ['--headless=new', '--disable-gpu', '--disable-extensions', '--disable-background-networking', '--no-first-run', '--no-default-browser-check', `--user-data-dir=${profile}`, `http://127.0.0.1:${port}/tests/native-webm.html`], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
  child.stderr.on('data', (data) => { stderr = `${stderr}${data}`.slice(-4000); });
  child.once('error', (error) => complete({ passed: false, report: error.message }));
  child.once('exit', (code) => complete({ passed: false, report: `Browser exited early (${code}).\n${stderr}` }));
  timeout = setTimeout(() => complete({ passed: false, report: `Native media checks timed out.\n${stderr}` }), 30000);
  const result = await completed;
  console.log(`native-webm.html:\n${result.report}`);
  if (!result.passed) process.exitCode = 1;
} finally {
  clearTimeout(timeout);
  // Chromium launches child processes on Windows; stopping just its root can
  // leave pipe handles/profile locks open. Terminate only this spawned tree.
  if (child?.pid && process.platform === 'win32') {
    await new Promise((done) => {
      const stop = spawn('taskkill', ['/pid', String(child.pid), '/t', '/f'], { windowsHide: true, stdio: 'ignore' });
      const stopTimeout = setTimeout(() => { stop.kill(); child.kill(); done(); }, 5000);
      const stopped = () => { clearTimeout(stopTimeout); done(); };
      stop.once('close', stopped);
      stop.once('error', () => { child.kill(); stopped(); });
    });
  } else child?.kill();
  server.httpServer?.closeAllConnections();
  await server.close();
  const resolved = resolve(profile);
  if (resolved.startsWith(resolve(tmpdir()) + sep)) await rm(profile, { recursive: true, force: true, maxRetries: 3 }).catch(() => {});
}
