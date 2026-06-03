/**
 * run_portfolio_monitor.ts — Warren AI portfolio monitoring (Prompts 6 & 7).
 *
 * Separate from the 5-step new-opportunity workflow. Uses CDP mode only.
 *
 * Env:
 *   WARREN_BROWSER_MODE=cdp (required)
 *   PORTFOLIO_DRY_RUN=true  — print prompts, no browser
 *   PORTFOLIO_FORCE=true    — ignore duplicate protection for today
 *   PORTFOLIO_SKIP_EXPORT=true — skip python export before run
 */
import dotenv from 'dotenv';
dotenv.config();

import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { chromium } from 'playwright';

import { loadConfig } from './config';
import { WarrenClient } from './warren_client';
import {
  formatPortfolioBlock,
  isPortfolioEmpty,
  loadPortfolioFile,
  portfolioJsonPath,
} from './portfolio_loader';
import { PortfolioReportWriter } from './portfolio_report';

const CDP_URL = process.env.CHROME_CDP_URL || 'http://127.0.0.1:9222';
const CHAT_INPUT_SEL = '[data-test="chat-input-field"]';

const PROMPT6_FILE = 'portfolio_news_thesis_monitor.md';
const PROMPT7_FILE = 'portfolio_action_plan.md';

function todayString(): string {
  return new Date().toISOString().slice(0, 10);
}

function sleep(ms: number): Promise<void> {
  return new Promise((res) => setTimeout(res, ms));
}

function stripHtmlComments(raw: string): string {
  return raw.replace(/<!--[\s\S]*?-->/g, '').trim();
}

function loadPromptTemplate(promptsDir: string, filename: string): string {
  const fp = path.join(promptsDir, filename);
  if (!fs.existsSync(fp)) throw new Error(`Prompt file not found: ${fp}`);
  return stripHtmlComments(fs.readFileSync(fp, 'utf-8'));
}

/** Body text after the first `---` following front matter, or full file. */
function extractResponseBody(savedMd: string): string {
  const lines = savedMd.split('\n');
  const dashIdx = lines.findIndex((l) => l.trim() === '---');
  if (dashIdx >= 0) {
    const rest = lines.slice(dashIdx + 1).join('\n').trim();
    if (rest.length > 20) return rest;
  }
  return savedMd.trim();
}

function loadSavedPortfolioStep(dayDir: string, step: 6 | 7): string | null {
  const filepath = path.join(dayDir, `portfolio_step_0${step}.md`);
  if (!fs.existsSync(filepath)) return null;
  return extractResponseBody(fs.readFileSync(filepath, 'utf-8'));
}

async function checkCdpReady(): Promise<{
  ok: boolean;
  reachable: boolean;
  warrenTab: boolean;
  chatVisible: boolean;
  tabUrl: string;
}> {
  let browser;
  try {
    browser = await chromium.connectOverCDP(CDP_URL, { timeout: 15000 });
  } catch {
    return { ok: false, reachable: false, warrenTab: false, chatVisible: false, tabUrl: '' };
  }
  try {
    const ctx = browser.contexts()[0];
    const pages = ctx?.pages() ?? [];
    const warrenPage = pages.find((p) => p.url().toLowerCase().includes('warrenai'));
    if (!warrenPage) {
      return { ok: false, reachable: true, warrenTab: false, chatVisible: false, tabUrl: pages[0]?.url() ?? '' };
    }
    let chatVisible = false;
    try {
      chatVisible = await warrenPage.locator(CHAT_INPUT_SEL).last().isVisible({ timeout: 5000 }).catch(() => false);
    } catch {
      /* ignore */
    }
    return { ok: chatVisible, reachable: true, warrenTab: true, chatVisible, tabUrl: warrenPage.url() };
  } finally {
    await browser.close();
  }
}

function runExportFromDb(projectRoot: string): void {
  const script = path.join(projectRoot, 'scripts', 'export_portfolio_from_db.py');
  if (!fs.existsSync(script)) {
    console.warn('[portfolio] export script missing, using existing JSON only');
    return;
  }
  try {
    execSync(`python3 "${script}"`, { stdio: 'inherit', cwd: projectRoot });
  } catch (e) {
    console.warn('[portfolio] export_portfolio_from_db.py failed — using existing JSON:', e);
  }
}

function duplicateComplete(dayDir: string): boolean {
  const s6 = path.join(dayDir, 'portfolio_step_06.md');
  const s7 = path.join(dayDir, 'portfolio_step_07.md');
  const sum = path.join(dayDir, 'portfolio_summary.json');
  if (!fs.existsSync(s6) || !fs.existsSync(s7) || !fs.existsSync(sum)) return false;
  try {
    const j = JSON.parse(fs.readFileSync(sum, 'utf-8')) as { success?: boolean };
    return j.success === true;
  } catch {
    return false;
  }
}

async function main(): Promise<void> {
  const dryRun = process.env.PORTFOLIO_DRY_RUN === 'true';
  const force = process.env.PORTFOLIO_FORCE === 'true';
  const skipExport = process.env.PORTFOLIO_SKIP_EXPORT === 'true';

  const config = loadConfig();
  const date = todayString();
  const projectRoot = path.resolve(__dirname, '..');
  const dayDir = path.join(config.outputDir, date);

  console.log('');
  console.log('╔══════════════════════════════════════════════════════════════════╗');
  console.log('║       WARREN AI — PORTFOLIO MONITOR (steps 6 & 7)               ║');
  console.log(`║  Date: ${date}   Dry-run: ${dryRun ? 'YES' : 'NO '}   Force: ${force ? 'YES' : 'NO '}              ║`);
  console.log('╚══════════════════════════════════════════════════════════════════╝');
  console.log('');

  if (config.browserMode !== 'cdp') {
    console.error('[portfolio] WARREN_BROWSER_MODE must be "cdp" for portfolio monitor.');
    console.error('           Example: npm run portfolio-monitor');
    process.exit(1);
  }

  if (!skipExport) {
    runExportFromDb(projectRoot);
  }

  const pfFile = loadPortfolioFile();
  if (isPortfolioEmpty(pfFile)) {
    console.log(`[portfolio] No holdings in ${portfolioJsonPath()}`);
    console.log('[portfolio] Run `npm run export-portfolio` after adding positions in the Cockpit,');
    console.log('            or edit config/current_portfolio.json. Exiting cleanly.');
    process.exit(0);
  }

  const portfolioText = formatPortfolioBlock(pfFile);
  console.log(`[portfolio] Loaded ${pfFile.positions.length} holding(s). Block length: ${portfolioText.length} chars\n`);

  if (!force && duplicateComplete(dayDir)) {
    console.log('[portfolio] Today’s portfolio monitor already completed (portfolio_summary.json success).');
    console.log('            Set PORTFOLIO_FORCE=true to rerun.');
    process.exit(0);
  }

  let prompt6 = '';
  let prompt7 = '';
  try {
    const p6raw = loadPromptTemplate(config.promptsDir, PROMPT6_FILE);
    const p7raw = loadPromptTemplate(config.promptsDir, PROMPT7_FILE);
    prompt6 = p6raw.replace(/\{\{CURRENT_PORTFOLIO\}\}/g, portfolioText);
    const saved6Preview = loadSavedPortfolioStep(dayDir, 6);
    const report6 = saved6Preview || '(step 6 not run yet)';
    prompt7 = p7raw.replace(/\{\{PORTFOLIO_MONITOR_REPORT\}\}/g, report6);
  } catch (e) {
    console.error('[portfolio] Failed to load prompts:', e);
    process.exit(1);
  }

  if (dryRun) {
    console.log('── Prompt 6 (preview, first 1200 chars) ──────────────────────────\n');
    console.log(prompt6.slice(0, 1200) + (prompt6.length > 1200 ? '\n…' : ''));
    console.log('\n── Prompt 7 (preview, first 800 chars) ────────────────────────────\n');
    console.log(prompt7.slice(0, 800) + (prompt7.length > 800 ? '\n…' : ''));
    console.log('\n[portfolio] PORTFOLIO_DRY_RUN=true — no prompts sent.');
    process.exit(0);
  }

  const writer = new PortfolioReportWriter(config.outputDir, date, false, pfFile.positions);

  const ready = await checkCdpReady();
  if (!ready.reachable) {
    const msg = `Chrome CDP not reachable at ${CDP_URL}`;
    console.error(`[portfolio] ${msg}`);
    console.error('  Launch: google-chrome --remote-debugging-port=9222 \\');
    console.error("    --remote-allow-origins='*' \\");
    console.error('    --user-data-dir="$HOME/.warren-ai-chrome-profile"');
    writer.failStep(6, 'CDP check', msg);
    writer.writeSummary(false);
    process.exit(1);
  }
  if (!ready.warrenTab || !ready.chatVisible) {
    const msg = !ready.warrenTab
      ? 'Warren AI tab not found — open https://www.investing.com/warrenai'
      : 'Chat input not visible — log in to Warren AI';
    console.error(`[portfolio] ${msg}`);
    writer.failStep(6, 'Readiness', msg);
    writer.writeSummary(false);
    process.exit(1);
  }

  const client = new WarrenClient(config);

  try {
    await client.launch();
    await client.openPage();

    const loggedIn = await client.ensureLoggedIn();
    if (!loggedIn) {
      writer.failStep(6, 'Login', 'Could not confirm login');
      writer.writeSummary(false);
      await client.close();
      process.exit(1);
    }

    // ── Step 6 ────────────────────────────────────────────────────────────
    let step6Body: string | null = loadSavedPortfolioStep(dayDir, 6);
    const step6Path = path.join(dayDir, 'portfolio_step_06.md');
    if (!force && step6Body && step6Body.length > 50) {
      console.log('[portfolio] Existing portfolio_step_06.md found — skipping prompt 6 send.');
      writer.recordSkipped(6, 'Portfolio news & thesis monitor', step6Path);
    } else {
      printBanner(6);
      if (!(await client.isChatReady())) {
        await client.reloadPage();
        await client.ensureLoggedIn();
      }
      let resp6: string;
      try {
        resp6 = await client.submitPromptAndWait(prompt6, 'portfolio6');
      } catch (e) {
        writer.failStep(6, 'Portfolio monitor', String(e));
        writer.writeSummary(false);
        await client.close();
        process.exit(1);
      }
      const sus6 = resp6.length < config.minResponseChars;
      if (sus6) writer.addWarning('Step 6 response unusually short');
      writer.writeStep6(resp6, sus6);
      step6Body = resp6;
      await sleep(3000);
    }

    const p7raw = loadPromptTemplate(config.promptsDir, PROMPT7_FILE);
    prompt7 = p7raw.replace(/\{\{PORTFOLIO_MONITOR_REPORT\}\}/g, step6Body || '');

    // ── Step 7 ────────────────────────────────────────────────────────────
    const step7Existing = loadSavedPortfolioStep(dayDir, 7);
    const step7Path = path.join(dayDir, 'portfolio_step_07.md');
    if (!force && step7Existing && step7Existing.length > 50) {
      console.log('[portfolio] Existing portfolio_step_07.md found — skipping prompt 7 send.');
      writer.recordSkipped(7, 'Portfolio action plan', step7Path);
    } else {
      printBanner(7);
      if (!(await client.isChatReady())) {
        await client.reloadPage();
        await client.ensureLoggedIn();
      }
      let resp7: string;
      try {
        resp7 = await client.submitPromptAndWait(prompt7, 'portfolio7');
      } catch (e) {
        writer.failStep(7, 'Portfolio action', String(e));
        writer.writeSummary(false);
        await client.close();
        process.exit(1);
      }
      const sus7 = resp7.length < config.minResponseChars;
      if (sus7) writer.addWarning('Step 7 response unusually short');
      writer.writeStep7(resp7, sus7);
      await sleep(1000);
    }

    writer.writeSummary(true);
    console.log('');
    console.log('╔══════════════════════════════════════════════════════════════════╗');
    console.log('║  ✓ Portfolio monitor complete.                                  ║');
    console.log(`║  Output: ${dayDir}`.padEnd(69) + '║');
    console.log('╚══════════════════════════════════════════════════════════════════╝');
    console.log('');

    await client.close();
    process.exit(0);
  } catch (err) {
    console.error('[portfolio] Unexpected error:', err);
    writer.writeSummary(false);
    await client.close();
    process.exit(1);
  }
}

function printBanner(step: number): void {
  const line = '═'.repeat(68);
  console.log(`\n${line}`);
  console.log(`  PORTFOLIO STEP ${step}`);
  console.log(`${line}\n`);
}

main().catch((e) => {
  console.error('[fatal]', e);
  process.exit(1);
});
