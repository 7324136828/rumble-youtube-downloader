import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { spawn } from 'node:child_process';
import { access, mkdtemp, rm, stat } from 'node:fs/promises';
import { createServer as createTcpServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = fileURLToPath(new URL('../', import.meta.url));
const screenshots = fileURLToPath(new URL('../../docs/screenshots/', import.meta.url));
const captures = [
  { route: 'keywords', file: 'clipfeed-video-topics.png', width: 1440, height: 1060 },
  { route: 'recommendations', file: 'clipfeed-recommendation-settings.png', width: 1440, height: 1100 },
  { route: 'settings', file: 'clipfeed-download-settings.png', width: 1440, height: 1250 },
];
const browsers = [
  process.env.PLAYER_TEST_BROWSER,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
].filter(Boolean);
let browser;
for (const candidate of browsers) {
  try { await access(candidate); browser = candidate; break; } catch {}
}
if (!browser) throw new Error('Install Chrome or Edge, or set PLAYER_TEST_BROWSER.');

const probe = createTcpServer();
await new Promise((done, reject) => { probe.once('error', reject); probe.listen(0, '127.0.0.1', done); });
const port = probe.address().port;
await new Promise((done) => probe.close(done));
const server = await createServer({
  configFile: false, root: frontend, plugins: [react()],
  server: { host: '127.0.0.1', port, strictPort: true, hmr: false }, logLevel: 'error',
});
try {
  await server.listen();
  for (const capture of captures) {
    const profile = await mkdtemp(join(tmpdir(), 'clipfeed-readme-'));
    const output = join(screenshots, capture.file);
    try {
      const args = [
        '--headless=new', '--disable-gpu', '--disable-extensions', '--disable-background-networking',
        '--no-first-run', '--no-default-browser-check', '--hide-scrollbars',
        '--force-device-scale-factor=1', '--virtual-time-budget=3500',
        `--window-size=${capture.width},${capture.height}`, `--user-data-dir=${profile}`,
        `--screenshot=${output}`, `http://127.0.0.1:${port}/tests/readme-demo.html#${capture.route}`,
      ];
      const child = spawn(browser, args, { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
      let stderr = '';
      child.stderr.on('data', (chunk) => { stderr = `${stderr}${chunk}`.slice(-3000); });
      const code = await new Promise((done, reject) => {
        const timeout = setTimeout(() => { child.kill(); reject(new Error(`${capture.route} screenshot timed out`)); }, 30000);
        child.once('error', (error) => { clearTimeout(timeout); reject(error); });
        child.once('exit', (result) => { clearTimeout(timeout); done(result); });
      });
      if (code !== 0) throw new Error(`${capture.route} browser exited ${code}: ${stderr}`);
      const image = await stat(output);
      if (image.size < 10000) throw new Error(`${capture.route} screenshot is unexpectedly small`);
      console.log(`${capture.file}: ${image.size} bytes`);
    } finally {
      const target = resolve(profile);
      if (target.startsWith(resolve(tmpdir()) + sep) && target.includes('clipfeed-readme-')) {
        await rm(profile, { recursive: true, force: true, maxRetries: 3 }).catch(() => {});
      }
    }
  }
} finally {
  server.httpServer?.closeAllConnections();
  await server.close();
}
