/**
 * run_theme_catalyst_radar.ts — Warren AI theme catalyst research (Prompt 6).
 *
 * Separate from the 5-step daily scan. Research only — no trade instructions.
 *
 * Env:
 *   WARREN_BROWSER_MODE=cdp (required for live run)
 *   THEME_CATALYST_DRY_RUN=true — print prompt, no browser
 *   THEME_CATALYST_FORCE=true — ignore duplicate protection for today
 */
import dotenv from 'dotenv';
dotenv.config();

import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { chromium } from 'playwright';

import { loadConfig } from './config';
import { WarrenClient } from './warren_client';

const CDP_URL = process.env.CHROME_CDP_URL || 'http://127.0.0.1:9222';
const CHAT_INPUT_SEL = '[data-test="chat-input-field"]';
const PROMPT_FILE = '06_theme_catalyst_radar.md';

function todayString(): string {
  return new Date().toISOString().slice(0, 10);
}

function stripHtmlComments(raw: string): string {
  return raw.replace(/<!--[\s\S]*?-->/g, '').trim();
}

function loadPromptTemplate(promptsDir: string): string {
  const fp = path.join(promptsDir, PROMPT_FILE);
  if (!fs.existsSync(fp)) throw new Error(`Prompt file not found: ${fp}`);
  return stripHtmlComments(fs.readFileSync(fp, 'utf-8'));
}

function loadManualCatalystsBlock(repoRoot: string): string {
  const script = path.join(repoRoot, 'warren_ai_daily', 'scripts', 'export_theme_catalyst_context.py');
  if (!fs.existsSync(script)) return '(no manual catalyst export script found)';
  try {
    return execSync(`python3 "${script}"`, { cwd: repoRoot, encoding: 'utf-8' }).trim();
  } catch {
    return '(manual catalyst config unavailable)';
  }
}

function loadThemeUniverseBlock(repoRoot: string): string {
  const themesPath = path.join(repoRoot, 'warren_ai_daily', 'config', 'themes.yaml');
  if (!fs.existsSync(themesPath)) return '(theme registry unavailable)';
  try {
    const raw = fs.readFileSync(themesPath, 'utf-8');
    const names = [...raw.matchAll(/^\s*-\s*id:\s*(\S+)/gm)].map((m) => m[1]);
    return names.length ? names.join(', ') : raw.slice(0, 2000);
  } catch {
    return '(theme registry unavailable)';
  }
}

function buildPrompt(config: ReturnType<typeof loadConfig>, date: string, repoRoot: string): string {
  const raw = loadPromptTemplate(config.promptsDir);
  return raw
    .replace(/\{\{DATE\}\}/g, date)
    .replace(/\{\{MANUAL_CATALYSTS_BLOCK\}\}/g, loadManualCatalystsBlock(repoRoot))
    .replace(/\{\{THEME_UNIVERSE\}\}/g, loadThemeUniverseBlock(repoRoot));
}

function extractResponseBody(savedMd: string): string {
  const lines = savedMd.split('\n');
  const dashIdx = lines.findIndex((l) => l.trim() === '---');
  if (dashIdx >= 0) {
    const rest = lines.slice(dashIdx + 1).join('\n').trim();
    if (rest.length > 20) return rest;
  }
  return savedMd.trim();
}

function duplicateComplete(dayDir: string): boolean {
  const md = path.join(dayDir, 'theme_catalyst_radar.md');
  const json = path.join(dayDir, 'theme_catalyst_radar.json');
  if (!fs.existsSync(md) || !fs.existsSync(json)) return false;
  try {
    const j = JSON.parse(fs.readFileSync(json, 'utf-8')) as { parse_ok?: boolean };
    return j.parse_ok === true;
  } catch {
    return false;
  }
}

function runParser(repoRoot: string, dayDir: string, date: string): void {
  const scriptPath = path.join(repoRoot, 'theme_catalyst_parser.py');
  execSync(`python3 "${scriptPath}" --day-dir "${dayDir}" --day "${date}"`, {
    cwd: repoRoot,
    stdio: 'inherit',
    timeout: 30_000,
  });
}

async function checkCdpReady(): Promise<boolean> {
  let browser;
  try {
    browser = await chromium.connectOverCDP(CDP_URL, { timeout: 15000 });
  } catch {
    return false;
  }
  try {
    const ctx = browser.contexts()[0];
    const pages = ctx?.pages() ?? [];
    const warrenPage = pages.find((p) => p.url().toLowerCase().includes('warrenai'));
    if (!warrenPage) return false;
    return warrenPage.locator(CHAT_INPUT_SEL).last().isVisible({ timeout: 5000 }).catch(() => false);
  } finally {
    await browser.close();
  }
}

async function main(): Promise<void> {
  const dryRun = process.env.THEME_CATALYST_DRY_RUN === 'true';
  const force = process.env.THEME_CATALYST_FORCE === 'true';

  const config = loadConfig();
  const date = todayString();
  const warrenRoot = path.resolve(__dirname, '..');
  const repoRoot = path.resolve(warrenRoot, '..');
  const dayDir = path.join(config.outputDir, date);

  console.log('');
  console.log('╔══════════════════════════════════════════════════════════════════╗');
  console.log('║       WARREN AI — THEME CATALYST RADAR (prompt 6)               ║');
  console.log(`║  Date: ${date}   Dry-run: ${dryRun ? 'YES' : 'NO '}   Force: ${force ? 'YES' : 'NO '}              ║`);
  console.log('╚══════════════════════════════════════════════════════════════════╝');
  console.log('');

  const promptText = buildPrompt(config, date, repoRoot);

  if (dryRun) {
    console.log('── Prompt 6 preview (first 2000 chars) ───────────────────────────\n');
    console.log(promptText.slice(0, 2000) + (promptText.length > 2000 ? '\n…' : ''));
    console.log('\n[theme-catalyst] THEME_CATALYST_DRY_RUN=true — no prompt sent.');
    process.exit(0);
  }

  if (config.browserMode !== 'cdp') {
    console.error('[theme-catalyst] WARREN_BROWSER_MODE must be "cdp".');
    console.error('  Example: npm run theme-catalyst-radar');
    process.exit(1);
  }

  if (!force && duplicateComplete(dayDir)) {
    console.log('[theme-catalyst] Today’s theme catalyst radar already completed.');
    console.log('            Set THEME_CATALYST_FORCE=true to rerun.');
    process.exit(0);
  }

  const ready = await checkCdpReady();
  if (!ready) {
    console.error('[theme-catalyst] Chrome CDP / Warren AI tab not ready.');
    console.error(`  Launch Chrome with CDP on ${CDP_URL} and open Warren AI.`);
    process.exit(1);
  }

  const client = new WarrenClient(config);
  let responseText = '';

  try {
    await client.launch();
    await client.openPage();
    const loggedIn = await client.ensureLoggedIn();
    if (!loggedIn) {
      console.error('[theme-catalyst] Could not confirm Warren AI login.');
      process.exit(1);
    }
    responseText = await client.submitPromptAndWait(promptText, 'theme_catalyst_radar');
  } catch (err) {
    console.error(`[theme-catalyst] Submission failed: ${err}`);
    process.exit(1);
  } finally {
    await client.close();
  }

  fs.mkdirSync(dayDir, { recursive: true });
  const mdPath = path.join(dayDir, 'theme_catalyst_radar.md');
  const header = [
    '# Theme Catalyst Radar — Warren Research',
    '',
    `_Generated: ${new Date().toISOString()}_`,
    '',
    '---',
    '',
  ].join('\n');
  fs.writeFileSync(mdPath, header + responseText, 'utf-8');
  console.log(`[theme-catalyst] ✓ theme_catalyst_radar.md written (${responseText.length} chars)`);

  try {
    runParser(repoRoot, dayDir, date);
    console.log('[theme-catalyst] ✓ Parser completed — latest JSON updated.');
  } catch (err) {
    console.warn(`[theme-catalyst] ⚠ Parser failed: ${err}`);
    process.exit(2);
  }
}

main().catch((err) => {
  console.error('[theme-catalyst] Fatal:', err);
  process.exit(1);
});
