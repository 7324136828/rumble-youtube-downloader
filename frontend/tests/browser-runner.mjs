import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { spawn } from 'node:child_process';
import { access, mkdtemp, rm } from 'node:fs/promises';
import { createServer as createTcpServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

// Uses installed Chromium with an isolated server and temporary browser profile.
export async function runBrowserTests(pages) {
  const candidates = [process.env.PLAYER_TEST_BROWSER, 'C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', '/usr/bin/chromium', '/usr/bin/google-chrome'].filter(Boolean);
  let browser;
  for (const path of candidates) { try { await access(path); browser = path; break; } catch {} }
  if (!browser) throw new Error('Set PLAYER_TEST_BROWSER to a locally installed Chromium browser executable.');
  if (pages.some((page) => !/^[a-z-]+\.html$/.test(page))) throw new Error('Invalid test page name.');

  // Vite treats port 0 as its default port. Reserve an OS-assigned port first.
  const probe = createTcpServer();
  await new Promise((done, reject) => { probe.once('error', reject); probe.listen(0, '127.0.0.1', done); });
  const port = probe.address().port;
  await new Promise((done) => probe.close(done));
  const frontend = fileURLToPath(new URL('../', import.meta.url));
  const server = await createServer({ configFile: false, root: frontend, plugins: [react()], optimizeDeps: { entries: pages.map((page) => `tests/${page}`) }, server: { host: '127.0.0.1', port, strictPort: true, hmr: false }, logLevel: 'error' });
  try {
    await server.listen();
    for (const page of pages) {
      const profile = await mkdtemp(join(tmpdir(), 'clipfeed-browser-test-'));
      let child;
      let timeout;
      try {
        const url = `http://127.0.0.1:${port}/tests/${page}`;
        child = spawn(browser, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', `--user-data-dir=${profile}`, '--dump-dom', '--virtual-time-budget=10000', url], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
        let output = '';
        let errorOutput = '';
        child.stdout.on('data', (data) => { output += data; });
        child.stderr.on('data', (data) => { errorOutput += data; });
        timeout = setTimeout(() => child.kill(), 30000);
        const exitCode = await new Promise((done, reject) => { child.once('error', reject); child.once('close', done); });
        const report = output.match(/<pre id="results"[^>]*>([\s\S]*?)<\/pre>/)?.[1];
        console.log(`${page}:\n${report || output || errorOutput}`);
        if (exitCode !== 0 || !output.includes('data-test-status="passed"')) process.exitCode = 1;
      } finally {
        clearTimeout(timeout);
        child?.kill();
        // Remove only the temporary directory created above, never an existing profile.
        const resolved = resolve(profile);
        if (resolved.startsWith(resolve(tmpdir()) + sep)) await rm(profile, { recursive: true, force: true, maxRetries: 3 }).catch(() => {});
      }
    }
  } finally {
    server.httpServer?.closeAllConnections();
    await server.close();
  }
}
