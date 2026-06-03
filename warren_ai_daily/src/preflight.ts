/**
 * preflight.ts — Verify that Warren AI is reachable and the chat input is visible.
 *
 * Usage:
 *   npm run preflight
 *
 * What it does:
 *   1. Loads config from .env
 *   2. Opens a browser (persistent or CDP depending on WARREN_BROWSER_MODE)
 *   3. Navigates to WARREN_AI_URL
 *   4. Checks if the chat input is visible
 *   5. Writes output/preflight_latest.json
 *   6. Saves a screenshot if chat input is NOT found
 *   7. Exits with code 0 either way, printing clear next steps
 */
import { loadConfig } from './config';
import { WarrenClient, SELECTORS } from './warren_client';
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

interface PreflightResult {
  timestamp: string;
  url: string;
  browserMode: string;
  chatInputFound: boolean;
  chatInputSelector: string | null;
  botDetected: boolean;
  loginFormVisible: boolean;
  screenshotPath: string | null;
  notes: string[];
}

async function runPreflight(): Promise<void> {
  const config = loadConfig();
  const result: PreflightResult = {
    timestamp:         new Date().toISOString(),
    url:               config.warrenUrl,
    browserMode:       config.browserMode,
    chatInputFound:    false,
    chatInputSelector: null,
    botDetected:       false,
    loginFormVisible:  false,
    screenshotPath:    null,
    notes:             [],
  };

  console.log('');
  console.log('╔══════════════════════════════════════════════════════════════════╗');
  console.log('║  WARREN AI PREFLIGHT CHECK                                       ║');
  console.log('╚══════════════════════════════════════════════════════════════════╝');
  console.log('');
  console.log(`URL:          ${config.warrenUrl}`);
  console.log(`Browser mode: ${config.browserMode}`);
  console.log(`Headless:     ${config.headless}`);
  console.log('');

  const client = new WarrenClient(config);

  try {
    await client.launch();
    await client.openPage();

    console.log(`[preflight] Navigating to ${config.warrenUrl}…`);

    // We need direct page access for detailed checks — use the existing client navigate
    // by calling ensureLoggedIn in non-interactive preflight mode via a raw check.
    // Actually use a standalone approach with direct Playwright for full control.
  } catch (err) {
    result.notes.push(`Browser launch failed: ${err}`);
    writePreflight(config.outputDir, result);
    console.error(`[preflight] Browser launch failed: ${err}`);
    process.exit(0); // Exit 0 — preflight itself ran, failure is informational
  }

  // Use the client for the actual page interaction
  try {
    // Re-create client with full control
    const browser = config.browserMode === 'cdp'
      ? await chromium.connectOverCDP(config.chromeCdpUrl, { timeout: 15000 })
      : null;

    let page;
    if (browser) {
      const ctx = browser.contexts()[0] ?? await browser.newContext();
      page = ctx.pages()[0] ?? await ctx.newPage();
    } else {
      fs.mkdirSync(config.browserProfileDir, { recursive: true });
      const ctx = await chromium.launchPersistentContext(config.browserProfileDir, {
        headless: config.headless,
        viewport: { width: 1440, height: 900 },
        args: ['--no-sandbox'],
      });
      page = ctx.pages()[0] ?? await ctx.newPage();
    }

    console.log('[preflight] Navigating…');
    await page.goto(config.warrenUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 });
    await page.waitForTimeout(3000);

    // Bot detection
    for (const sel of SELECTORS.botBlockIndicators) {
      try {
        const visible = await page.locator(sel).first().isVisible({ timeout: 400 });
        if (visible) {
          result.botDetected = true;
          result.notes.push(`Bot/captcha detected: ${sel}`);
          break;
        }
      } catch { /* continue */ }
    }

    // Login form
    for (const sel of SELECTORS.loginRequiredIndicators) {
      try {
        const visible = await page.locator(sel).first().isVisible({ timeout: 400 });
        if (visible) {
          result.loginFormVisible = true;
          result.notes.push(`Login form detected: ${sel}`);
          break;
        }
      } catch { /* continue */ }
    }

    // Chat input
    for (const sel of SELECTORS.chatInput) {
      try {
        const visible = await page.locator(sel).last().isVisible({ timeout: 1000 });
        if (visible) {
          result.chatInputFound    = true;
          result.chatInputSelector = sel;
          result.notes.push(`Chat input found: ${sel}`);
          break;
        }
      } catch { /* continue */ }
    }

    // Screenshot always — useful for verifying the state
    const screenshotDir  = config.outputDir;
    fs.mkdirSync(screenshotDir, { recursive: true });
    const screenshotPath = path.join(screenshotDir, 'preflight_latest.png');
    try {
      await page.screenshot({ path: screenshotPath, fullPage: false });
      result.screenshotPath = screenshotPath;
      console.log(`[preflight] Screenshot: ${screenshotPath}`);
    } catch { /* ignore */ }

    // Close browser (always, preflight is just a check)
    if (browser) {
      await browser.close();
    } else {
      await page.context().close();
    }

  } catch (err) {
    result.notes.push(`Page interaction failed: ${err}`);
  }

  writePreflight(config.outputDir, result);
  printSummary(result, config.outputDir);
  process.exit(0);
}

function writePreflight(outputRoot: string, result: PreflightResult): void {
  fs.mkdirSync(outputRoot, { recursive: true });
  const filepath = path.join(outputRoot, 'preflight_latest.json');
  fs.writeFileSync(filepath, JSON.stringify(result, null, 2), 'utf-8');
  console.log(`[preflight] Results written to: ${filepath}`);
}

function printSummary(result: PreflightResult, outputRoot: string): void {
  console.log('');
  console.log('── Preflight Result ─────────────────────────────────────────────────');

  if (result.chatInputFound) {
    console.log('✓ Chat input FOUND');
    console.log(`  Selector: ${result.chatInputSelector}`);
    console.log('');
    console.log('You are ready to run the daily workflow:');
    console.log('  npm run run-daily');
    console.log('  npm run run-daily:visible   (headless=false, browser stays open)');
    console.log('  npm run run-daily:resume    (skip already-completed steps)');
  } else {
    console.log('✗ Chat input NOT found');
    console.log('');
    if (result.botDetected) {
      console.log('⚠ Bot/captcha challenge detected.');
      console.log('  Open the browser manually, solve the challenge, then re-run preflight.');
    } else if (result.loginFormVisible) {
      console.log('⚠ Login form is visible — you need to log in first.');
      console.log('  Run in visible mode so you can log in:');
      console.log('    HEADLESS=false npm run preflight');
      console.log('  Or use CDP mode to attach to your existing Chrome session:');
      console.log('    npm run run-daily:cdp');
    } else {
      console.log('⚠ Unknown state. Notes:');
      result.notes.forEach((n) => console.log(`  • ${n}`));
      console.log('');
      console.log('Try running with HEADLESS=false to inspect the browser:');
      console.log('  HEADLESS=false npm run preflight');
    }
    if (result.screenshotPath) {
      console.log(`  Screenshot saved: ${result.screenshotPath}`);
    }
  }

  if (result.notes.length > 0) {
    console.log('');
    console.log('Notes:');
    result.notes.forEach((n) => console.log(`  • ${n}`));
  }

  console.log('────────────────────────────────────────────────────────────────────');
  console.log('');
}

runPreflight().catch((err) => {
  console.error('[preflight] Fatal:', err);
  process.exit(1);
});
