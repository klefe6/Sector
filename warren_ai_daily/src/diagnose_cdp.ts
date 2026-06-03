/**
 * diagnose_cdp.ts — Print CDP HTTP info, OS listener cmdline, and try Playwright attach.
 *
 *   npm run diagnose-cdp
 */
import dotenv from 'dotenv';
dotenv.config();

import { chromium } from 'playwright';
import {
  CHROME_CDP_LAUNCH_COMMAND,
  getCdpListenerCmdlineHintLines,
  parseCdpTcpTarget,
} from './cdp_instructions';

const CDP_URL = process.env.CHROME_CDP_URL || 'http://127.0.0.1:9222';

async function fetchJsonVersion(base: string): Promise<Record<string, string>> {
  const u = base.replace(/\/?$/, '');
  const res = await fetch(`${u}/json/version`, { signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as Record<string, string>;
}

async function main(): Promise<void> {
  const tcp = parseCdpTcpTarget(CDP_URL);
  console.log(`CHROME_CDP_URL=${CDP_URL}\n`);

  if (!tcp) {
    console.error('Could not parse CHROME_CDP_URL.');
    process.exit(1);
  }

  const versionUrl = `${CDP_URL.replace(/\/?$/, '')}/json/version`;
  console.log(`GET ${versionUrl}`);
  try {
    const j = await fetchJsonVersion(CDP_URL);
    console.log('  Browser:', j.Browser ?? '(unknown)');
    console.log('  webSocketDebuggerUrl:', j.webSocketDebuggerUrl ?? '(missing)');
  } catch (e) {
    console.error('  HTTP failed:', e);
    process.exit(1);
  }

  console.log('\nPlaywright chromium.connectOverCDP (15s timeout)…');
  try {
    const browser = await chromium.connectOverCDP(CDP_URL, { timeout: 15_000 });
    const n = browser.contexts().reduce((a, c) => a + c.pages().length, 0);
    console.log(`  OK — attached. Open pages (all contexts): ${n}`);
    await browser.close();
    console.log('\nCDP is usable.');
    console.log('\nNext: keep this Chrome window open, confirm Warren AI chat is ready, then run:');
    console.log('  npm run morning-full');
    process.exit(0);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    console.error('  FAILED:', msg.split('\n').slice(0, 8).join('\n'));
    const hint = getCdpListenerCmdlineHintLines(tcp.port);
    if (hint.length) console.error('\nOS listener (why attach often fails):', hint.join('\n'));
    else
      console.error(
        '\nOS listener: (could not read process — install `lsof` or check permissions.)',
      );
    console.error('\nFix: quit Chrome on this port, then launch with:');
    console.error(`  ${CHROME_CDP_LAUNCH_COMMAND}`);
    console.error('\nThen: npm run diagnose-cdp  (expect OK before morning-full)');
    process.exit(2);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
