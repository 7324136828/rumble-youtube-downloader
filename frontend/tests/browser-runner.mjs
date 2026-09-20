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
  let reportResult;
  let activePage;
  // A completion callback avoids Chromium's virtual media clock and inherited
  // dump-DOM pipe handles preventing an otherwise finished test from exiting.
  const reporter = {
    name: 'browser-test-completion',
    transformIndexHtml() {
      return [{ tag: 'script', injectTo: 'body', children: `
        (() => {
          let reported = false;
          const sendReport = window.fetch.bind(window);
          const report = () => {
            const status = document.body.dataset.testStatus;
            if (reported || !['passed', 'failed'].includes(status)) return;
            reported = true;
            sendReport('/__browser-test-result', { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ page: location.pathname.split('/').pop(), passed: status === 'passed',
                report: document.getElementById('results')?.textContent || status }) });
          };
          new MutationObserver(report).observe(document.body, { attributes: true, attributeFilter: ['data-test-status'] });
          report();
        })();` }];
    },
    configureServer(vite) {
      vite.middlewares.use('/__browser-test-result', (request, response) => {
        if (request.method !== 'POST') { response.statusCode = 405; response.end(); return; }
        let body = '';
        request.on('data', (chunk) => { body += chunk; if (body.length > 65536) request.destroy(); });
        request.on('end', () => {
          try {
            const result = JSON.parse(body);
            if (result.page === activePage) reportResult?.(result);
            response.end('ok');
          } catch { response.statusCode = 400; response.end(); }
        });
      });
    },
  };
  const server = await createServer({ configFile: false, root: frontend, plugins: [react(), reporter], optimizeDeps: { entries: pages.map((page) => `tests/${page}`) }, server: { host: '127.0.0.1', port, strictPort: true, hmr: false }, logLevel: 'error' });
  try {
    await server.listen();
    for (const page of pages) {
      const profile = await mkdtemp(join(tmpdir(), 'clipfeed-browser-test-'));
      let child;
      let timeout;
      try {
        const url = `http://127.0.0.1:${port}/tests/${page}`;
        activePage = page;
        const completed = new Promise((done) => { reportResult = done; });
        const completePage = reportResult;
        child = spawn(browser, ['--headless=new', '--disable-gpu', '--disable-extensions', '--disable-background-networking', '--no-first-run', '--no-default-browser-check', `--user-data-dir=${profile}`, url], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
        let errorOutput = '';
        child.stderr.on('data', (data) => { errorOutput = `${errorOutput}${data}`.slice(-4000); });
        child.once('error', (error) => completePage({ passed: false, report: error.message }));
        child.once('exit', (code) => completePage({ passed: false, report: `Browser exited early (${code}).\n${errorOutput}` }));
        timeout = setTimeout(() => completePage({ passed: false, report: `Browser tests timed out.\n${errorOutput}` }), 30000);
        const result = await completed;
        console.log(`${page}:\n${result.report}`);
        if (result.passed !== true) process.exitCode = 1;
      } finally {
        clearTimeout(timeout);
        reportResult = () => {};
        // Only terminate the isolated browser process tree launched above.
        if (child?.pid && process.platform === 'win32') {
          await new Promise((done) => {
            const stop = spawn('taskkill', ['/pid', String(child.pid), '/t', '/f'], { windowsHide: true, stdio: 'ignore' });
            const stopTimeout = setTimeout(() => { stop.kill(); child.kill(); done(); }, 5000);
            const stopped = () => { clearTimeout(stopTimeout); done(); };
            stop.once('close', stopped);
            stop.once('error', () => { child.kill(); stopped(); });
          });
        } else child?.kill();
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
