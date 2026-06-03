/**
 * morning.ts — Pre-flight launcher for the daily Warren AI run.
 *
 * Usage:
 *   npm run morning
 *
 * What it does:
 *   1. Checks whether Chrome is running with --remote-debugging-port=9222
 *   2. If not reachable: prints exact Chrome launch command and exits cleanly
 *   3. If reachable: checks whether Warren AI tab is open and chat is visible
 *   4. Prints a clear go/no-go verdict and exact next command to run
 *
 * This command never starts the actual run — it only checks readiness
 * and prints instructions. It is safe to run at any time.
 */
import dotenv from 'dotenv';
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
import {
  formatCdpBlockedPreambleLines,
  parseCdpTcpTarget,
  probeTcpPort,
  type CdpTcpProbe,
} from './cdp_instructions';

dotenv.config();

const CDP_URL    = process.env.CHROME_CDP_URL    || 'http://127.0.0.1:9222';
const WARREN_URL = process.env.WARREN_AI_URL     || 'https://www.investing.com/warrenai';
const OUTPUT_DIR = path.resolve(process.env.OUTPUT_DIR || './output');

// Warren AI's confirmed chat input selector
const CHAT_INPUT_SEL = '[data-test="chat-input-field"]';

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function todayOutputDir(): string {
  return path.join(OUTPUT_DIR, today());
}

function checkTodayOutputExists(): { exists: boolean; files: string[] } {
  const dir = todayOutputDir();
  if (!fs.existsSync(dir)) return { exists: false, files: [] };
  const files = fs.readdirSync(dir);
  return { exists: true, files };
}

async function checkCdp(): Promise<{
  reachable: boolean;
  warrenTabFound: boolean;
  chatVisible: boolean;
  tabUrl: string;
  portProbe: CdpTcpProbe;
}> {
  let browser;
  try {
    browser = await chromium.connectOverCDP(CDP_URL, { timeout: 15000 });
  } catch {
    const tcp = parseCdpTcpTarget(CDP_URL);
    let portProbe: CdpTcpProbe = 'unknown';
    if (tcp) portProbe = await probeTcpPort(tcp.host, tcp.port);
    return { reachable: false, warrenTabFound: false, chatVisible: false, tabUrl: '', portProbe };
  }

  try {
    const ctx   = browser.contexts()[0];
    const pages = ctx?.pages() ?? [];
    const warrenPage = pages.find((p) => p.url().includes('warrenai'));

    if (!warrenPage) {
      return {
        reachable: true,
        warrenTabFound: false,
        chatVisible: false,
        tabUrl: pages[0]?.url() ?? '',
        portProbe: 'open',
      };
    }

    // Check chat input visibility
    let chatVisible = false;
    try {
      chatVisible = await warrenPage.locator(CHAT_INPUT_SEL).last()
        .isVisible({ timeout: 3000 }).catch(() => false);
    } catch { /* ignore */ }

    return {
      reachable:      true,
      warrenTabFound: true,
      chatVisible,
      tabUrl:         warrenPage.url(),
      portProbe:      'open',
    };
  } finally {
    await browser.close();
  }
}

async function main(): Promise<void> {
  const line = '═'.repeat(66);
  console.log('');
  console.log(line);
  console.log('  WARREN AI — MORNING READINESS CHECK');
  console.log(`  ${new Date().toLocaleString('en-US', { weekday: 'long', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}`);
  console.log(line);
  console.log('');

  // ── Step 1: Check if today already ran ──────────────────────────────────
  const { exists: alreadyRan, files: existingFiles } = checkTodayOutputExists();
  if (alreadyRan) {
    const hasAll5 = [1,2,3,4,5].every((n) => existingFiles.includes(`step_0${n}.md`));
    const hasSummary = existingFiles.includes('run_summary.json');
    const hasReport  = existingFiles.includes('final_action_plan.md');

    let summarySuccess = false;
    try {
      const s = JSON.parse(fs.readFileSync(path.join(todayOutputDir(), 'run_summary.json'), 'utf-8'));
      summarySuccess = s.success === true;
    } catch { /* ignore */ }

    if (hasAll5 && hasSummary && hasReport && summarySuccess) {
      console.log(`✓ TODAY'S RUN ALREADY COMPLETED SUCCESSFULLY`);
      console.log(`  Output: ${todayOutputDir()}`);
      console.log(`  Files: ${existingFiles.join(', ')}`);
      console.log('');
      console.log('  Nothing to do. Check your final_action_plan.md for today\'s trades.');
      console.log('');
      process.exit(0);
      return;
    }

    console.log(`⚠ PARTIAL RUN DETECTED for ${today()}`);
    console.log(`  Files present: ${existingFiles.join(', ')}`);
    console.log(`  Use: npm run run-daily:resume   (to pick up from where it stopped)`);
    console.log('');
  }

  // ── Step 2: Check CDP ─────────────────────────────────────────────────
  console.log(`Checking Chrome CDP at ${CDP_URL}…`);
  const cdp = await checkCdp();

  if (!cdp.reachable) {
    formatCdpBlockedPreambleLines(CDP_URL, cdp.portProbe, {
      title: 'CHROME CDP REQUIRED — readiness check failed',
      primaryRerun: 'npm run run-daily:cdp   (ideas only)',
    }).forEach((l) => console.log(l));
    console.log('  Combined ideas + portfolio: npm run morning-full');
    console.log('');
    process.exit(0);
    return;
  }

  console.log(`✓ Chrome CDP reachable`);

  if (!cdp.warrenTabFound) {
    console.log('');
    console.log('✗ Warren AI tab not found in Chrome.');
    console.log(`  Current tab: ${cdp.tabUrl || '(none)'}`);
    console.log('');
    console.log('  In the open Chrome window, navigate to:');
    console.log(`    ${WARREN_URL}`);
    console.log('');
    console.log('  Log in if prompted. When chat is visible, run:');
    console.log('    npm run run-daily:cdp');
    console.log('');
    process.exit(0);
    return;
  }

  console.log(`✓ Warren AI tab found: ${cdp.tabUrl}`);

  if (!cdp.chatVisible) {
    console.log('');
    console.log('✗ Chat input is NOT visible — you may need to log in.');
    console.log('');
    console.log('  In the Chrome window, log in to InvestingPro.');
    console.log('  When you can see the "Ask WarrenAI" input box, run:');
    console.log('    npm run run-daily:cdp');
    console.log('');
    process.exit(0);
    return;
  }

  console.log('✓ Chat input visible — ready to run');
  console.log('');
  console.log(line);
  console.log('  ✓ ALL CHECKS PASSED — READY');
  console.log('');

  if (alreadyRan) {
    console.log('  Partial run detected. Resume with:');
    console.log('    npm run run-daily:resume');
  } else {
    console.log('  Start today\'s run with:');
    console.log('    npm run run-daily:cdp');
  }

  console.log('');
  console.log(`  Outputs will be saved to: ${todayOutputDir()}`);
  console.log(line);
  console.log('');
  process.exit(0);
}

main().catch((err) => {
  console.error('[morning] Error:', err);
  process.exit(0); // Always exit 0 — this is an advisory command
});
