/**
 * morning_full.ts — Combined daily workflow: new ideas (prompts 1-5) + portfolio monitor (prompts 6-7).
 *
 * Commands:
 *   npm run morning-full        — run both workflows sequentially (skips anything already done)
 *   npm run morning-full:dry    — print exactly what would happen, touch nothing
 *
 * Env overrides:
 *   MORNING_FORCE_IDEAS=true     — rerun new ideas even if already complete today
 *   MORNING_FORCE_PORTFOLIO=true — rerun portfolio monitor even if already complete today
 *
 * The 7 AM scheduler currently runs only new ideas (npm run scheduled-morning → run-daily:cdp).
 * To switch it to morning-full, see DAILY_RUN.md § "Scheduler".
 */
import dotenv from 'dotenv';
dotenv.config();

import { execSync, spawnSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { chromium } from 'playwright';
import {
  CHROME_CDP_LAUNCH_COMMAND,
  formatCdpBlockedPreambleLines,
  parseCdpTcpTarget,
  probeTcpPort,
  type CdpTcpProbe,
} from './cdp_instructions';

const CDP_URL    = process.env.CHROME_CDP_URL || 'http://127.0.0.1:9222';
const OUTPUT_DIR = path.resolve(process.env.OUTPUT_DIR || './output');
const PROJECT_DIR = path.resolve(__dirname, '..');
const AUTOMATION_LAST_FAILURE = 'automation_last_failure.json';
const SCHEDULED_STATUS_FILE = 'scheduled_morning_status.json';
const BOOTSTRAP_SCRIPT = path.join(PROJECT_DIR, 'scripts', 'bootstrap_warren_chrome.sh');

const DRY_RUN         = process.env.MORNING_DRY_RUN         === 'true';
const FORCE_IDEAS     = process.env.MORNING_FORCE_IDEAS      === 'true';
const FORCE_PORTFOLIO = process.env.MORNING_FORCE_PORTFOLIO  === 'true';

const CHAT_INPUT_SEL = '[data-test="chat-input-field"]';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function today(): string { return new Date().toISOString().slice(0, 10); }
function todayDir(): string { return path.join(OUTPUT_DIR, today()); }
function ts(): string { return new Date().toISOString(); }

interface NewIdeasStatus { complete: boolean; partial: boolean; files: string[] }
interface PortfolioStatus { complete: boolean; partial: boolean; hasHoldings: boolean; holdingCount: number }
interface CdpStatus {
  reachable: boolean;
  warrenTab: boolean;
  chatVisible: boolean;
  /** TCP reachability of CHROME_CDP_URL host:port when CDP session failed or unknown. */
  portProbe: CdpTcpProbe;
}

function writeAutomationLastFailure(payload: Record<string, unknown>): void {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  fs.writeFileSync(
    path.join(OUTPUT_DIR, AUTOMATION_LAST_FAILURE),
    JSON.stringify(payload, null, 2) + '\n',
    'utf-8',
  );
}

function clearAutomationLastFailure(): void {
  const p = path.join(OUTPUT_DIR, AUTOMATION_LAST_FAILURE);
  try {
    if (fs.existsSync(p)) fs.unlinkSync(p);
  } catch {
    /* ignore */
  }
}

/** Mirror scheduled_morning status file for ops / dashboard checks. */
function writeScheduledStatus(payload: Record<string, unknown>): void {
  try {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    fs.writeFileSync(
      path.join(OUTPUT_DIR, SCHEDULED_STATUS_FILE),
      JSON.stringify({ finished_at: ts(), ...payload }, null, 2) + '\n',
      'utf-8',
    );
  } catch {
    /* best-effort */
  }
}

function runChromeBootstrap(): { ok: boolean; message: string } {
  if (!fs.existsSync(BOOTSTRAP_SCRIPT)) {
    return { ok: false, message: `Bootstrap script not found: ${BOOTSTRAP_SCRIPT}` };
  }
  try {
    execSync(`bash "${BOOTSTRAP_SCRIPT}"`, {
      env: { ...process.env, DISPLAY: process.env.DISPLAY || ':1' },
      stdio: ['ignore', 'inherit', 'inherit'],
      timeout: 45_000,
    });
    return { ok: true, message: '' };
  } catch (err: unknown) {
    const status = (err as { status?: number }).status;
    return {
      ok: false,
      message: `Chrome bootstrap failed (exit ${status ?? '?'}). See /tmp/warren-chrome-bootstrap.log`,
    };
  }
}

function checkNewIdeas(): NewIdeasStatus {
  const dir = todayDir();
  if (!fs.existsSync(dir)) return { complete: false, partial: false, files: [] };
  const files = fs.readdirSync(dir);
  const hasAll5 = [1,2,3,4,5].every((n) => files.includes(`step_0${n}.md`));
  const hasSummary = files.includes('run_summary.json');
  let success = false;
  if (hasSummary) {
    try {
      success = JSON.parse(fs.readFileSync(path.join(dir, 'run_summary.json'), 'utf-8')).success === true;
    } catch { /* ignore */ }
  }
  const anyStep = [1,2,3,4,5].some((n) => files.includes(`step_0${n}.md`));
  return { complete: hasAll5 && success, partial: anyStep && !success, files };
}

function checkPortfolioMonitor(): PortfolioStatus {
  const dir = todayDir();
  // Holdings
  const pfPath = path.join(PROJECT_DIR, 'config', 'current_portfolio.json');
  let holdingCount = 0;
  if (fs.existsSync(pfPath)) {
    try {
      const pf = JSON.parse(fs.readFileSync(pfPath, 'utf-8'));
      holdingCount = (pf.positions ?? []).length;
    } catch { /* ignore */ }
  }

  if (!fs.existsSync(dir)) return { complete: false, partial: false, hasHoldings: holdingCount > 0, holdingCount };
  const files = fs.readdirSync(dir);
  const has6 = files.includes('portfolio_step_06.md');
  const has7 = files.includes('portfolio_step_07.md');
  const hasSummary = files.includes('portfolio_summary.json');
  let success = false;
  if (hasSummary) {
    try {
      success = JSON.parse(fs.readFileSync(path.join(dir, 'portfolio_summary.json'), 'utf-8')).success === true;
    } catch { /* ignore */ }
  }
  return {
    complete: has6 && has7 && success,
    partial:  (has6 || has7) && !success,
    hasHoldings: holdingCount > 0,
    holdingCount,
  };
}

async function checkCdp(): Promise<CdpStatus> {
  let browser;
  try {
    browser = await chromium.connectOverCDP(CDP_URL, { timeout: 15000 });
  } catch {
    const tcp = parseCdpTcpTarget(CDP_URL);
    let portProbe: CdpTcpProbe = 'unknown';
    if (tcp) portProbe = await probeTcpPort(tcp.host, tcp.port);
    return { reachable: false, warrenTab: false, chatVisible: false, portProbe };
  }
  try {
    const ctx = browser.contexts()[0];
    const pages = ctx?.pages() ?? [];
    const wp = pages.find((p) => p.url().includes('warrenai'));
    if (!wp) return { reachable: true, warrenTab: false, chatVisible: false, portProbe: 'open' };
    const chatVisible = await wp.locator(CHAT_INPUT_SEL).last().isVisible({ timeout: 3000 }).catch(() => false);
    return { reachable: true, warrenTab: true, chatVisible, portProbe: 'open' };
  } finally {
    await browser.close();
  }
}

function runExport(): { ok: boolean; msg: string } {
  try {
    const r = spawnSync('python3', ['scripts/export_portfolio_from_db.py'], {
      cwd: PROJECT_DIR, stdio: 'pipe', encoding: 'utf-8',
    });
    if (r.status !== 0) return { ok: false, msg: (r.stderr || r.stdout || 'exit non-zero').slice(0, 200) };
    return { ok: true, msg: (r.stdout || '').trim() };
  } catch (e) {
    return { ok: false, msg: String(e) };
  }
}

function runNpm(script: string): { ok: boolean; exitCode: number } {
  const r = spawnSync('npm', ['run', script], {
    cwd: PROJECT_DIR,
    env: { ...process.env, WARREN_BROWSER_MODE: 'cdp', DISPLAY: process.env.DISPLAY || ':1' },
    stdio: 'inherit',
  });
  return { ok: r.status === 0, exitCode: r.status ?? 1 };
}

function writeCombinedSummary(report: MorningSummary): void {
  fs.mkdirSync(todayDir(), { recursive: true });

  // JSON
  fs.writeFileSync(
    path.join(todayDir(), 'full_morning_summary.json'),
    JSON.stringify(report, null, 2) + '\n',
    'utf-8',
  );

  // Markdown
  const lines = [
    `# Full Morning Summary — ${report.date}`,
    '',
    `_Generated: ${report.generatedAt}_`,
    '',
    '---',
    '',
    '## Workflow status',
    '',
    `| Workflow | Status |`,
    `|----------|--------|`,
    `| New ideas (prompts 1–5) | ${report.newIdeas.status} |`,
    `| Portfolio export | ${report.portfolioExport.status} |`,
    `| Portfolio monitor (prompts 6–7) | ${report.portfolioMonitor.status} |`,
    '',
    '## Output paths',
    '',
    `- Output folder:          \`${report.paths.dayDir}\``,
    `- Final action plan:      \`${report.paths.finalActionPlan || '(not found)'}\``,
    `- Portfolio monitor step6:\`${report.paths.portfolioStep6 || '(not found)'}\``,
    `- Portfolio monitor step7:\`${report.paths.portfolioStep7 || '(not found)'}\``,
    '',
  ];

  if (report.newIdeas.topActionable.length > 0) {
    lines.push('## Top actionable new buys');
    lines.push('');
    lines.push(report.newIdeas.topActionable.map((t, i) => `${i + 1}. ${t}`).join('\n'));
    lines.push('');
  }

  if (report.portfolioMonitor.holdings.length > 0) {
    lines.push('## Current holdings monitored');
    lines.push('');
    lines.push(report.portfolioMonitor.holdings.map((h) => `- ${h}`).join('\n'));
    lines.push('');
  }

  if (report.portfolioMonitor.needsAttention.length > 0) {
    lines.push('## Holdings needing attention today');
    lines.push('');
    lines.push(report.portfolioMonitor.needsAttention.map((h) => `- ⚠ ${h}`).join('\n'));
    lines.push('');
  }

  if (report.overlap.length > 0) {
    lines.push('## Already-owned overlap with new scan');
    lines.push('');
    lines.push(report.overlap.map((t) => `- ${t}`).join('\n'));
    lines.push('');
  }

  if (report.warnings.length > 0) {
    lines.push('## Warnings');
    lines.push('');
    lines.push(report.warnings.map((w) => `- ⚠ ${w}`).join('\n'));
    lines.push('');
  }

  lines.push('## Next manual action');
  lines.push('');
  lines.push(report.nextAction);
  lines.push('');

  fs.writeFileSync(
    path.join(todayDir(), 'full_morning_summary.md'),
    lines.join('\n'),
    'utf-8',
  );
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MorningSummary {
  date: string;
  generatedAt: string;
  newIdeas: {
    status: string;
    topActionable: string[];
    outputFile: string;
  };
  portfolioExport: { status: string };
  portfolioMonitor: {
    status: string;
    holdings: string[];
    needsAttention: string[];
  };
  paths: {
    dayDir: string;
    finalActionPlan: string;
    portfolioStep6: string;
    portfolioStep7: string;
  };
  overlap: string[];
  warnings: string[];
  nextAction: string;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const date   = today();
  const dir    = todayDir();
  const line   = '═'.repeat(70);

  console.log('');
  console.log(line);
  console.log('  WARREN AI — FULL MORNING WORKFLOW');
  console.log(`  ${date}   Dry-run: ${DRY_RUN ? 'YES' : 'NO'}   Force-ideas: ${FORCE_IDEAS ? 'YES' : 'NO'}   Force-portfolio: ${FORCE_PORTFOLIO ? 'YES' : 'NO'}`);
  console.log(line);
  console.log('');

  // ── Day-of-week check (only run Tue–Sat) ─────────────────────────────
  const now = new Date();
  const dayOfWeek = now.getDay(); // 0=Sunday, 1=Monday, 2=Tuesday, ..., 6=Saturday
  
  if (dayOfWeek === 0 || dayOfWeek === 1) {
    const dayName = dayOfWeek === 0 ? 'Sunday' : 'Monday';
    console.log(`[morning-full] Today is ${dayName} — automation is DISABLED (only runs Tue–Sat).`);
    console.log('[morning-full] Exiting cleanly without running Warren AI workflow.');
    writeScheduledStatus({
      started_at: ts(),
      bootstrap_status: 'skipped',
      cdp_check_status: 'skipped',
      capture_status: 'skipped',
      output_fresh: false,
      latest_output_path: dir,
      error_message: `Scheduled off-day (${dayName})`,
    });
    console.log('');
    process.exit(0);
    return;
  }
  console.log(`[morning-full] Day of week check passed (day ${dayOfWeek} — Tue–Sat) ✓\n`);

  // ── Pre-checks ─────────────────────────────────────────────────────────
  const niStatus   = checkNewIdeas();
  const pfStatus   = checkPortfolioMonitor();  // before export
  const cdp        = await checkCdp();

  // ── Determine actions (before export) ──────────────────────────────────
  const runIdeas = !niStatus.complete || FORCE_IDEAS;
  const runPf    = pfStatus.hasHoldings && (!pfStatus.complete || FORCE_PORTFOLIO);
  const skipPf   = !pfStatus.hasHoldings;
  const needsCdp = runIdeas || runPf;

  // ── DRY-RUN branch ─────────────────────────────────────────────────────
  if (DRY_RUN) {
    console.log('┌─ DRY-RUN PREVIEW ───────────────────────────────────────────────┐');
    console.log('│');

    // Export
    console.log(`│  Portfolio export:       will run (to refresh config/current_portfolio.json)`);

    // New ideas
    if (niStatus.complete && !FORCE_IDEAS) {
      console.log(`│  New ideas (1-5):        SKIP — already complete (${niStatus.files.length} files present)`);
    } else if (niStatus.partial && !FORCE_IDEAS) {
      console.log(`│  New ideas (1-5):        RESUME — partial run detected`);
    } else {
      console.log(`│  New ideas (1-5):        RUN — no complete run for today`);
    }

    // Re-read holdings after an imaginary export
    const pfAfterExport = checkPortfolioMonitor();
    if (!pfAfterExport.hasHoldings) {
      console.log(`│  Portfolio export:       holdings = 0 in positions.db active statuses`);
      console.log(`│  Portfolio monitor:      SKIP — no holdings found after export`);
    } else if (pfStatus.complete && !FORCE_PORTFOLIO) {
      console.log(`│  Portfolio monitor (6-7):SKIP — already complete`);
    } else if (pfStatus.partial && !FORCE_PORTFOLIO) {
      console.log(`│  Portfolio monitor (6-7):RESUME — step 6 exists; step 7 missing`);
    } else {
      console.log(`│  Portfolio monitor (6-7):RUN — ${pfAfterExport.holdingCount} holding(s)`);
    }

    // CDP
    if (needsCdp) {
      if (!cdp.reachable) {
        console.log('│  Chrome CDP:             BLOCKED — not reachable at ' + CDP_URL);
        console.log(`│    TCP ${cdp.portProbe === 'closed' ? '(port closed / refused)' : cdp.portProbe === 'open' ? '(port open — not a CDP Chrome?)' : '(probe inconclusive)'}`);
      } else if (!cdp.warrenTab) console.log('│  Chrome CDP:             BLOCKED — Warren AI tab not found');
      else if (!cdp.chatVisible) console.log('│  Chrome CDP:             BLOCKED — chat input not visible (login required?)');
      else                     console.log('│  Chrome CDP:             READY ✓');
    } else {
      console.log('│  Chrome CDP:             not needed (everything already done)');
    }

    // Website freshness
    console.log('│');
    console.log(`│  Website data freshness:`);
    console.log(`│    New ideas:            ${niStatus.complete ? 'FRESH (today)' : 'STALE or MISSING'}`);
    console.log(`│    Portfolio monitor:    ${pfStatus.complete ? 'FRESH (today)' : pfStatus.partial ? 'PARTIAL' : 'NONE'}`);
    console.log('│');
    console.log(`│  Combined summary would be written to:`);
    console.log(`│    ${dir}/full_morning_summary.json`);
    console.log(`│    ${dir}/full_morning_summary.md`);
    console.log('│');
    console.log('└────────────────────────────────────────────────────────────────────┘');
    console.log('');
    console.log('[morning-full:dry] No files written. No prompts sent.');
    process.exit(0);
  }

  // ── LIVE branch ────────────────────────────────────────────────────────

  writeScheduledStatus({
    started_at: ts(),
    bootstrap_status: 'skipped',
    cdp_check_status: 'skipped',
    capture_status: 'skipped',
    output_fresh: false,
    latest_output_path: dir,
    error_message: '',
  });

  console.log('[morning-full] Bootstrapping Chrome CDP…');
  const bootstrap = runChromeBootstrap();
  if (!bootstrap.ok) {
    console.error(`[morning-full] ✗ ${bootstrap.message}`);
    writeAutomationLastFailure({
      kind: 'bootstrap_failed',
      label: bootstrap.message,
      at: new Date().toISOString(),
    });
    writeScheduledStatus({
      bootstrap_status: 'failed',
      cdp_check_status: 'skipped',
      capture_status: 'failed',
      output_fresh: false,
      latest_output_path: dir,
      error_message: bootstrap.message,
    });
    writeCombinedSummary({
      date,
      generatedAt: ts(),
      newIdeas: { status: 'blocked-bootstrap', topActionable: [], outputFile: '' },
      portfolioExport: { status: 'skipped' },
      portfolioMonitor: { status: 'skipped', holdings: [], needsAttention: [] },
      paths: {
        dayDir: dir,
        finalActionPlan: path.join(dir, 'final_action_plan.md'),
        portfolioStep6: path.join(dir, 'portfolio_step_06.md'),
        portfolioStep7: path.join(dir, 'portfolio_step_07.md'),
      },
      overlap: [],
      warnings: [bootstrap.message],
      nextAction: 'Fix Chrome CDP bootstrap, then rerun: npm run morning-full',
    });
    process.exit(1);
  }
  console.log('[morning-full]   ✓ Bootstrap OK');

  const warnings: string[] = [];
  const summary: MorningSummary = {
    date,
    generatedAt: ts(),
    newIdeas: { status: 'skipped', topActionable: [], outputFile: '' },
    portfolioExport: { status: 'skipped' },
    portfolioMonitor: { status: 'skipped', holdings: [], needsAttention: [] },
    paths: {
      dayDir: dir,
      finalActionPlan: path.join(dir, 'final_action_plan.md'),
      portfolioStep6: path.join(dir, 'portfolio_step_06.md'),
      portfolioStep7: path.join(dir, 'portfolio_step_07.md'),
    },
    overlap: [],
    warnings,
    nextAction: 'Review final_action_plan.md and portfolio_step_07.md.',
  };

  // ── Step 1: Export portfolio ────────────────────────────────────────────
  console.log('[morning-full] Step 1: Export portfolio from positions.db…');
  const expResult = runExport();
  if (expResult.ok) {
    console.log(`[morning-full]   ✓ ${expResult.msg || 'Export OK'}`);
    summary.portfolioExport.status = 'ok';
  } else {
    console.warn(`[morning-full]   ⚠ Export failed: ${expResult.msg}`);
    summary.portfolioExport.status = 'failed';
    warnings.push(`Portfolio export failed: ${expResult.msg}`);
  }

  // Re-read portfolio status after export
  const pfFresh = checkPortfolioMonitor();
  const ideasFresh = checkNewIdeas();

  // ── Step 2: CDP check (only if we need to run anything) ─────────────────
  const willRunIdeas = !ideasFresh.complete || FORCE_IDEAS;
  const willRunPf    = pfFresh.hasHoldings && (!pfFresh.complete || FORCE_PORTFOLIO);

  if (willRunIdeas || willRunPf) {
    if (!cdp.reachable) {
      const tcp = parseCdpTcpTarget(CDP_URL);
      const detailParts = [
        `CDP URL: ${CDP_URL}`,
        `TCP probe: ${cdp.portProbe}${tcp ? ` (${tcp.host}:${tcp.port})` : ''}`,
        `Launch: ${CHROME_CDP_LAUNCH_COMMAND}`,
      ];
      const msg = `Chrome CDP not reachable at ${CDP_URL}. Launch: ${CHROME_CDP_LAUNCH_COMMAND}`;
      console.error('[morning-full] ✗ Chrome CDP not reachable — see instructions below.\n');
      formatCdpBlockedPreambleLines(CDP_URL, cdp.portProbe, {
        title: 'CHROME CDP REQUIRED — morning-full cannot attach',
      }).forEach((line) => console.error(line));
      warnings.push(msg);
      writeAutomationLastFailure({
        kind: 'cdp_unreachable',
        label: 'Chrome CDP not reachable',
        at: new Date().toISOString(),
        cdpUrl: CDP_URL,
        portProbe: cdp.portProbe,
        tcpTarget: tcp ? `${tcp.host}:${tcp.port}` : null,
        launchCommand: CHROME_CDP_LAUNCH_COMMAND,
        detail: detailParts.join(' | '),
      });
      summary.newIdeas.status       = willRunIdeas ? 'blocked-no-cdp' : summary.newIdeas.status;
      summary.portfolioMonitor.status = willRunPf ? 'blocked-no-cdp' : summary.portfolioMonitor.status;
      summary.nextAction = (
        'Open the dedicated Warren AI Chrome window (remote debugging), log in, pass security checks, '
        + 'then rerun: npm run morning-full'
      );
      writeCombinedSummary(summary);
      process.exit(1);
    }
    if (!cdp.warrenTab || !cdp.chatVisible) {
      const msg = !cdp.warrenTab
        ? 'Warren AI tab not found. Navigate to https://www.investing.com/warrenai'
        : 'Chat not visible — log in to InvestingPro';
      console.error(`[morning-full] ✗ ${msg}`);
      warnings.push(msg);
      summary.newIdeas.status         = willRunIdeas ? 'blocked-no-login' : summary.newIdeas.status;
      summary.portfolioMonitor.status = willRunPf ? 'blocked-no-login' : summary.portfolioMonitor.status;
      writeCombinedSummary(summary);
      process.exit(1);
    }
    console.log('[morning-full]   ✓ CDP ready');
  }

  // ── Step 3: New ideas (prompts 1-5) ─────────────────────────────────────
  if (!willRunIdeas) {
    const reason = ideasFresh.complete ? 'already complete' : 'up to date';
    console.log(`[morning-full] Step 2: New ideas — SKIPPING (${reason})`);
    summary.newIdeas.status = `skipped-${reason.replace(/ /g, '-')}`;
  } else {
    const script = ideasFresh.partial && !FORCE_IDEAS ? 'run-daily:resume' : 'run-daily:cdp';
    console.log(`[morning-full] Step 2: New ideas — running: npm run ${script}`);
    const r = runNpm(script);
    summary.newIdeas.status = r.ok ? 'complete' : `failed-exit-${r.exitCode}`;
    if (!r.ok) {
      warnings.push(`New ideas run failed (exit ${r.exitCode}). Check output logs.`);
    }
  }

  // Collect top actionable from today's final_action_plan if it now exists
  try {
    const fapPath = path.join(dir, 'final_action_plan.md');
    if (fs.existsSync(fapPath)) {
      const fap = fs.readFileSync(fapPath, 'utf-8');
      // Quick regex — same pattern as warren_loader.py
      const actionable = [...fap.matchAll(/(?:Rank|^)\s*\d+\s+\|\s*([A-Z]{1,5})\s*\|/gm)]
        .map((m) => m[1]).filter(Boolean).slice(0, 8);
      if (actionable.length > 0) summary.newIdeas.topActionable = actionable;
    }
  } catch { /* best effort */ }

  // ── Step 4: Portfolio monitor (prompts 6-7) ──────────────────────────────
  if (!pfFresh.hasHoldings) {
    console.log('[morning-full] Step 3: Portfolio monitor — SKIPPING (no holdings in config/current_portfolio.json)');
    summary.portfolioMonitor.status = 'skipped-no-holdings';
  } else if (!willRunPf) {
    const reason = pfFresh.complete ? 'already complete' : 'up to date';
    console.log(`[morning-full] Step 3: Portfolio monitor — SKIPPING (${reason})`);
    summary.portfolioMonitor.status = `skipped-${reason.replace(/ /g, '-')}`;
  } else {
    // Resume if step 6 already exists
    const step6Exists = fs.existsSync(path.join(dir, 'portfolio_step_06.md'));
    const pfEnv = step6Exists ? 'PORTFOLIO_SKIP_EXPORT=true' : 'PORTFOLIO_SKIP_EXPORT=true';
    console.log(`[morning-full] Step 3: Portfolio monitor — running${step6Exists ? ' (step 6 exists, will skip re-send)' : ''}…`);
    const r = spawnSync('npm', ['run', 'portfolio-monitor'], {
      cwd: PROJECT_DIR,
      env: { ...process.env, WARREN_BROWSER_MODE: 'cdp', PORTFOLIO_SKIP_EXPORT: 'true', DISPLAY: process.env.DISPLAY || ':1' },
      stdio: 'inherit',
    });
    summary.portfolioMonitor.status = r.status === 0 ? 'complete' : `failed-exit-${r.status}`;
    if (r.status !== 0) {
      warnings.push(`Portfolio monitor failed (exit ${r.status}). Check output logs.`);
    }
  }

  // Holdings list from portfolio JSON
  try {
    const pfPath = path.join(PROJECT_DIR, 'config', 'current_portfolio.json');
    const pf = JSON.parse(fs.readFileSync(pfPath, 'utf-8'));
    summary.portfolioMonitor.holdings = (pf.positions ?? []).map((p: { ticker: string }) => p.ticker).filter(Boolean);
  } catch { /* best effort */ }

  // Overlap: holdings also in new-ideas actionable list
  if (summary.newIdeas.topActionable.length > 0 && summary.portfolioMonitor.holdings.length > 0) {
    summary.overlap = summary.newIdeas.topActionable.filter((t) =>
      summary.portfolioMonitor.holdings.includes(t)
    );
  }

  // ── Write combined summary ───────────────────────────────────────────────
  writeCombinedSummary(summary);

  // ── Final report ─────────────────────────────────────────────────────────
  const allDone = !summary.newIdeas.status.startsWith('failed') &&
                  !summary.portfolioMonitor.status.startsWith('failed');
  console.log('');
  console.log(line);
  console.log('  MORNING FULL — COMPLETE');
  console.log(`  New ideas:         ${summary.newIdeas.status}`);
  console.log(`  Portfolio monitor: ${summary.portfolioMonitor.status}`);
  console.log(`  Combined summary:  ${dir}/full_morning_summary.md`);
  if (warnings.length > 0) {
    console.log(`  Warnings (${warnings.length}):`);
    warnings.forEach((w) => console.log(`    ⚠ ${w}`));
  }
  console.log(line);
  console.log('');

  const ideasOk = summary.newIdeas.status === 'complete' || summary.newIdeas.status.startsWith('skipped');
  const pfOk =
    summary.portfolioMonitor.status === 'complete' ||
    summary.portfolioMonitor.status.startsWith('skipped');
  const freshSummary = fs.existsSync(path.join(dir, 'run_summary.json'));
  writeScheduledStatus({
    bootstrap_status: 'ok',
    cdp_check_status: 'ok',
    capture_status: allDone ? 'ok' : 'failed',
    output_fresh: freshSummary,
    latest_output_path: dir,
    error_message: allDone ? '' : warnings.join('; ') || 'One or more workflows failed',
  });

  if (allDone) {
    clearAutomationLastFailure();
  }
  process.exit(allDone ? 0 : 1);
}

main().catch((e) => {
  console.error('[morning-full] Fatal:', e);
  process.exit(1);
});
