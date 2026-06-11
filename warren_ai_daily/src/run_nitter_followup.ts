/**
 * run_nitter_followup.ts — Nitter-informed Warren AI follow-up (second pass).
 *
 * Runs AFTER the normal Warren morning scan. It reads the latest Nitter
 * research, turns the strongest leads into a few extra Warren prompts, asks
 * Warren to VERIFY each lead independently, and saves the results separately.
 *
 * This pass is fully optional and isolated:
 *   - OFF unless WARREN_NITTER_FOLLOWUP_ENABLED=true (live submission).
 *   - Dry-run (WARREN_NITTER_FOLLOWUP_DRY_RUN=true or `:dry` script) generates
 *     prompts without touching the browser, regardless of the enabled flag.
 *   - It NEVER alters the normal Warren workflow or its output files.
 *   - It skips cleanly (never errors the normal run) when Nitter data is
 *     missing/stale, when Warren has 0/0 credits, or when nothing qualifies.
 *   - Idempotent: will not re-run a completed pass for the same date unless
 *     WARREN_NITTER_FOLLOWUP_FORCE=true.
 *
 * Output (under OUTPUT_DIR):
 *   output/YYYY-MM-DD/nitter_followup/leads.json
 *   output/YYYY-MM-DD/nitter_followup/prompts.json
 *   output/YYYY-MM-DD/nitter_followup/results.json
 *   output/YYYY-MM-DD/nitter_followup/result_NN_<label>.md   (per lead response)
 *   output/latest_nitter_followup.json                       (Cockpit pointer)
 *
 * Env:
 *   WARREN_BROWSER_MODE=cdp (required for live submission)
 */
import dotenv from 'dotenv';
dotenv.config();

import { spawnSync } from 'child_process';
import fs from 'fs';
import path from 'path';

import { loadConfig, type Config } from './config';
import { WarrenClient } from './warren_client';
import { runPreflight } from './warren_preflight';

const OUTPUT_DIR = path.resolve(process.env.OUTPUT_DIR || './output');
const PROJECT_DIR = path.resolve(__dirname, '..');
const BOOTSTRAP_SCRIPT = path.join(PROJECT_DIR, 'scripts', 'bootstrap_warren_chrome.sh');
const PY_MODULE = path.join(PROJECT_DIR, 'scripts', 'nitter_followup.py');
const LATEST_POINTER = path.join(OUTPUT_DIR, 'latest_nitter_followup.json');
const DRY_RUN_LATEST_POINTER = path.join(OUTPUT_DIR, 'latest_nitter_followup_dry_run.json');

function getNewYorkDateString(now = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  const value = (type: string) => parts.find((p) => p.type === type)?.value || '';
  return `${value('year')}-${value('month')}-${value('day')}`;
}
function ts(): string { return new Date().toISOString(); }
function sleep(ms: number): Promise<void> { return new Promise((r) => setTimeout(r, ms)); }

function argvHasDryRun(argv = process.argv.slice(2)): boolean {
  return argv.some((arg) => arg === '--dry-run' || arg === '--dryrun');
}

if (argvHasDryRun()) {
  process.env.WARREN_NITTER_FOLLOWUP_DRY_RUN = 'true';
}

// ---------------------------------------------------------------------------
// Types mirrored from nitter_followup.py output
// ---------------------------------------------------------------------------

interface PyLead {
  ticker: string;
  theme: string;
  direction_bias: string;
  confidence: number;
  why_from_nitter: string[];
  supporting_terms: string[];
  risk_notes: string[];
  mentions: number;
}

interface PyPrompt { lead: PyLead; prompt: string; }

interface PyResult {
  status: string;            // ok | skipped-missing | skipped-stale | skipped-no-leads | skipped-error
  freshness: string;         // fresh | stale | missing | error
  freshness_status?: string; // same_day | age_fresh_but_previous_day | stale | missing | error
  source_file: string;
  file_date: string;
  source_file_date?: string;
  run_date?: string;
  age_hours?: number | null;
  date_matches_run?: boolean;
  source_file_mtime?: string;
  source_file_age_hours?: number | null;
  expected_source_file?: string;
  source_selection_reason?: string;
  source_date_warning?: string;
  warning?: string;
  generated_at: string;
  skip_reason?: string;
  leads: PyLead[];
  prompts: PyPrompt[];
  candidates_considered?: number;
}

interface LeadResult {
  label: string;
  ticker: string;
  theme: string;
  direction_bias: string;
  confidence: number;
  status: 'ok' | 'failed' | 'skipped' | 'dry-run';
  warren_action: string;
  warren_score: number | null;
  summary: string;
  risk_notes: string[];
  prompt_file: string | null;
  response_excerpt: string;
  response_file: string | null;
  error: string | null;
}

interface FollowupSummary {
  status: string;            // ok | dry-run | skipped-* | failed
  date: string;
  generated_at: string;
  source_file: string;
  source_file_date: string;
  source_file_mtime: string;
  source_file_age_hours: number | null;
  expected_source_file: string;
  source_selection_reason: string;
  source_date_warning: string;
  nitter_freshness: string;
  freshness_status: string;
  run_date: string;
  age_hours: number | null;
  date_matches_run: boolean;
  warning: string;
  credits: { used: number; total: number } | null;
  dry_run: boolean;
  submitted_to_warren: boolean;
  skip_reason: string;
  output_dir: string;
  leads_count: number;
  results: LeadResult[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function leadLabel(l: PyLead): string {
  if (l.ticker && l.theme) return `${l.ticker} (${l.theme})`;
  return l.ticker || l.theme || 'unknown';
}

function safeSlug(s: string): string {
  return s.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || 'lead';
}

/** Run the Python lead generator and parse its JSON stdout. */
function generateLeads(cfg: Config, followupDir: string, date: string): PyResult | null {
  const args = [
    PY_MODULE,
    '--source-dir', cfg.nitter.sourceDir,
    '--max', String(cfg.nitter.maxPrompts),
    '--min-confidence', String(cfg.nitter.minConfidence),
    '--max-age-hours', String(cfg.nitter.maxAgeHours),
    '--date', date,
    '--out', followupDir,
    '--emit',
  ];
  const r = spawnSync('python3', args, { cwd: PROJECT_DIR, encoding: 'utf-8' });
  if (r.status !== 0) {
    console.error('[nitter] Lead generator failed:', (r.stderr || '').slice(0, 500));
    return null;
  }
  try {
    return JSON.parse(r.stdout) as PyResult;
  } catch (e) {
    console.error('[nitter] Could not parse lead generator output:', e);
    return null;
  }
}

/** Extract Warren's final action + 0–100 score from a response, best-effort. */
function extractActionAndScore(text: string): { action: string; score: number | null } {
  let score: number | null = null;
  const scoreMatch =
    text.match(/(?:score[^\d]{0,20})(\d{1,3})\s*(?:\/\s*100)?/i) ||
    text.match(/\b(\d{1,3})\s*\/\s*100\b/);
  if (scoreMatch) {
    const n = parseInt(scoreMatch[1], 10);
    if (!isNaN(n) && n >= 0 && n <= 100) score = n;
  }

  let action = '';
  const actionRegions = [
    text.match(/final action[^\n]*?[:\-]\s*([^\n]+)/i),
    text.match(/\b(Strong candidate|Starter position|Avoid|Watch)\b/i),
  ];
  for (const m of actionRegions) {
    if (!m) continue;
    const region = m[1] || m[0];
    const found = region.match(/Strong candidate|Starter position|Avoid|Watch/i);
    if (found) { action = found[0]; break; }
  }
  // Normalize capitalization
  if (/strong candidate/i.test(action)) action = 'Strong candidate';
  else if (/starter position/i.test(action)) action = 'Starter position';
  else if (/avoid/i.test(action)) action = 'Avoid';
  else if (/watch/i.test(action)) action = 'Watch';
  return { action: action || 'Unclear', score };
}

function writeLatestPointer(summary: FollowupSummary): void {
  try {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    const pointer = summary.dry_run ? DRY_RUN_LATEST_POINTER : LATEST_POINTER;
    fs.writeFileSync(pointer, JSON.stringify(summary, null, 2) + '\n', 'utf-8');
  } catch (e) {
    console.warn('[nitter] Could not write latest pointer:', e);
  }
}

function writeResults(followupDir: string, summary: FollowupSummary): void {
  try {
    fs.mkdirSync(followupDir, { recursive: true });
    fs.writeFileSync(
      path.join(followupDir, 'results.json'),
      JSON.stringify(summary, null, 2) + '\n',
      'utf-8',
    );
  } catch (e) {
    console.warn('[nitter] Could not write results.json:', e);
  }
  writeLatestPointer(summary);
}

/**
 * Idempotency: a completed LIVE pass exists for today. A prior dry-run is a
 * preview only and must NOT block a subsequent live submission.
 */
function alreadyComplete(followupDir: string): boolean {
  const fp = path.join(followupDir, 'results.json');
  if (!fs.existsSync(fp)) return false;
  try {
    const j = JSON.parse(fs.readFileSync(fp, 'utf-8')) as FollowupSummary;
    return j.status === 'ok';
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const cfg = loadConfig();
  const date = getNewYorkDateString();
  const dayDir = path.join(OUTPUT_DIR, date);
  const followupDir = path.join(dayDir, cfg.nitter.dryRun ? 'nitter_followup_dry_run' : 'nitter_followup');
  const line = '═'.repeat(70);

  console.log('');
  console.log(line);
  console.log('  WARREN AI — NITTER-INFORMED FOLLOW-UP (second pass)');
  console.log(`  ${date}   Enabled: ${cfg.nitter.enabled ? 'YES' : 'NO'}   ` +
    `Dry-run: ${cfg.nitter.dryRun ? 'YES' : 'NO'}   Force: ${cfg.nitter.force ? 'YES' : 'NO'}`);
  console.log(`  Source dir: ${cfg.nitter.sourceDir}`);
  console.log(`  Max prompts: ${cfg.nitter.maxPrompts}   Min confidence: ${cfg.nitter.minConfidence}` +
    `   Max age: ${cfg.nitter.maxAgeHours}h`);
  console.log(line);
  console.log('');

  const baseSummary = (): FollowupSummary => ({
    status: 'skipped',
    date,
    generated_at: ts(),
    source_file: '',
    source_file_date: '',
    source_file_mtime: '',
    source_file_age_hours: null,
    expected_source_file: path.join(cfg.nitter.sourceDir, `${date}_nitter.txt`),
    source_selection_reason: '',
    source_date_warning: '',
    nitter_freshness: 'unknown',
    freshness_status: 'missing',
    run_date: date,
    age_hours: null,
    date_matches_run: false,
    warning: '',
    credits: null,
    dry_run: cfg.nitter.dryRun,
    submitted_to_warren: false,
    skip_reason: '',
    output_dir: followupDir,
    leads_count: 0,
    results: [],
  });

  // ── Gate: live submission requires the enabled flag ──────────────────────
  if (!cfg.nitter.enabled && !cfg.nitter.dryRun) {
    console.log('[nitter] WARREN_NITTER_FOLLOWUP_ENABLED is false — skipping (set it true to enable).');
    const s = baseSummary();
    s.status = 'skipped-disabled';
    s.skip_reason = 'WARREN_NITTER_FOLLOWUP_ENABLED=false';
    writeResults(followupDir, s);
    process.exit(0);
  }

  // ── Idempotency ─────────────────────────────────────────────────────────
  if (!cfg.nitter.dryRun && !cfg.nitter.force && alreadyComplete(followupDir)) {
    console.log('[nitter] Today\'s follow-up already completed — skipping (set FORCE=true to rerun).');
    process.exit(0);
  }

  // ── Generate leads + prompts (Python) ────────────────────────────────────
  const py = generateLeads(cfg, followupDir, date);
  if (!py) {
    const s = baseSummary();
    s.status = 'skipped-error';
    s.skip_reason = 'Lead generator failed (see logs).';
    writeResults(followupDir, s);
    process.exit(0); // never fail the normal run
  }

  console.log(`[nitter] Source: ${py.source_file || '(none)'} (${py.freshness}` +
    `${py.freshness_status ? ` / ${py.freshness_status}` : ''})`);
  if (py.warning) {
    console.warn(`[nitter] Source warning: ${py.warning}`);
  }
  console.log(`[nitter] Status: ${py.status}` +
    (py.candidates_considered != null ? `  candidates: ${py.candidates_considered}` : ''));

  const summary = baseSummary();
  summary.source_file = py.source_file;
  summary.source_file_date = py.source_file_date || py.file_date || '';
  summary.source_file_mtime = py.source_file_mtime || '';
  summary.source_file_age_hours = py.source_file_age_hours ?? py.age_hours ?? null;
  summary.expected_source_file = py.expected_source_file || summary.expected_source_file;
  summary.source_selection_reason = py.source_selection_reason || '';
  summary.source_date_warning = py.source_date_warning || py.warning || '';
  summary.nitter_freshness = py.freshness;
  summary.freshness_status = py.freshness_status || py.freshness;
  summary.run_date = py.run_date || date;
  summary.age_hours = py.age_hours ?? summary.source_file_age_hours;
  summary.date_matches_run = py.date_matches_run ?? false;
  summary.warning = py.warning || summary.source_date_warning;
  summary.leads_count = py.prompts.length;

  // ── Clean skips (missing / stale / no-leads) ─────────────────────────────
  if (!cfg.nitter.dryRun && (py.status !== 'ok' || py.prompts.length === 0)) {
    summary.status = py.status.startsWith('skipped') ? py.status : 'skipped-no-leads';
    summary.skip_reason = py.skip_reason || 'No qualifying leads.';
    console.log(`[nitter] ${summary.skip_reason}`);
    writeResults(followupDir, summary);
    process.exit(0);
  }

  console.log(`[nitter] ${py.prompts.length} lead(s) selected for Warren follow-up:`);
  py.prompts.forEach((p, i) =>
    console.log(`   ${i + 1}. ${leadLabel(p.lead)}  ` +
      `[${p.lead.direction_bias}, conf ${p.lead.confidence}]`));

  // ── Dry-run: stop before touching the browser ────────────────────────────
  if (cfg.nitter.dryRun) {
    fs.mkdirSync(followupDir, { recursive: true });
    summary.status = 'dry-run';
    summary.dry_run = true;
    summary.submitted_to_warren = false;
    summary.credits = null;
    if (py.status !== 'ok' || py.prompts.length === 0) {
      summary.skip_reason = py.skip_reason || 'No qualifying leads.';
    }
    summary.results = py.prompts.map((p, i) => {
      const promptFile = path.join(
        followupDir,
        `prompt_${String(i + 1).padStart(2, '0')}_${safeSlug(leadLabel(p.lead))}.txt`,
      );
      fs.writeFileSync(promptFile, p.prompt + '\n', 'utf-8');
      return {
        label: leadLabel(p.lead),
        ticker: p.lead.ticker,
        theme: p.lead.theme,
        direction_bias: p.lead.direction_bias,
        confidence: p.lead.confidence,
        status: 'dry-run',
        warren_action: '(dry-run)',
        warren_score: null,
        summary: p.lead.why_from_nitter.slice(0, 2).join(' | '),
        risk_notes: p.lead.risk_notes,
        prompt_file: promptFile,
        response_excerpt: '',
        response_file: null,
        error: null,
      };
    });
    writeResults(followupDir, summary);
    console.log('\n[nitter] DRY-RUN — prompts generated, nothing sent to Warren AI.');
    console.log(`[nitter] Prompts saved to: ${path.join(followupDir, 'prompts.json')}`);
    console.log(`[nitter] Dry-run results saved to: ${path.join(followupDir, 'results.json')}`);
    console.log(`[nitter] Dry-run latest pointer: ${DRY_RUN_LATEST_POINTER}`);
    if (py.prompts[0]) {
      console.log('\n── Example prompt (lead 1) ──────────────────────────────────────────\n');
      console.log(py.prompts[0].prompt);
    }
    process.exit(0);
  }

  if (cfg.nitter.dryRun) {
    throw new Error('dry-run guard failed before Warren submission path');
  }

  // ── LIVE submission ───────────────────────────────────────────────────────
  if (cfg.browserMode !== 'cdp') {
    summary.status = 'skipped-error';
    summary.skip_reason = 'WARREN_BROWSER_MODE must be "cdp" for live follow-up.';
    console.error(`[nitter] ${summary.skip_reason}`);
    writeResults(followupDir, summary);
    process.exit(0);
  }

  // Preflight (reuse the hardened Warren preflight: Chrome + readiness).
  console.log('[nitter] Running Warren preflight…');
  const preflight = await runPreflight({
    cdpUrl:                    cfg.chromeCdpUrl,
    outputDir:                 OUTPUT_DIR,
    bootstrapScript:           BOOTSTRAP_SCRIPT,
    warrenUrl:                 cfg.warrenUrl,
    chromeProfileDir:          process.env.CHROME_PROFILE_DIR
                                 || `${process.env.HOME ?? '/root'}/.warren-ai-chrome-profile`,
    maxAttempts:               2,
    retryDelayMs:              10_000,
    playwrightAttachTimeoutMs: 28_000,
    chatVisibilityTimeoutMs:   20_000,
    telegramBotToken:          process.env.TELEGRAM_BOT_TOKEN,
    telegramChatId:            process.env.TELEGRAM_CHAT_ID,
  });
  if (!preflight.ok) {
    summary.status = 'skipped-error';
    summary.skip_reason = preflight.finalError || `Warren not ready: ${preflight.status}`;
    console.error(`[nitter] Preflight failed — skipping follow-up: ${summary.skip_reason}`);
    writeResults(followupDir, summary);
    process.exit(0);
  }

  const client = new WarrenClient(cfg);
  try {
    await client.launch();
    await client.openPage();
    const loggedIn = await client.ensureLoggedIn();
    if (!loggedIn) {
      summary.status = 'skipped-error';
      summary.skip_reason = 'Could not confirm Warren AI login.';
      writeResults(followupDir, summary);
      await client.close();
      process.exit(0);
    }

    // Credit guard: skip cleanly if Warren reports 0/0 credits.
    const credits = await client.readCredits();
    if (credits) {
      summary.credits = { used: credits.used, total: credits.total };
      console.log(`[nitter] Credits: ${credits.used}/${credits.total}`);
      if (credits.exhausted) {
        summary.status = 'skipped-no-credits';
        summary.skip_reason = 'Warren AI shows 0/0 credits — skipping follow-up to avoid wasted run.';
        console.warn(`[nitter] ${summary.skip_reason}`);
        writeResults(followupDir, summary);
        await client.close();
        process.exit(0);
      }
    } else {
      console.log('[nitter] Credit balance not readable — proceeding (will not assume exhausted).');
    }

    fs.mkdirSync(followupDir, { recursive: true });

    // Submit each prompt; isolate per-lead failures.
    const results: LeadResult[] = [];
    let anyOk = false;
    for (let i = 0; i < py.prompts.length; i++) {
      const { lead, prompt } = py.prompts[i];
      const label = leadLabel(lead);
      const stepName = `nitter${i + 1}`;
      console.log(`\n${'─'.repeat(68)}\n  NITTER FOLLOW-UP ${i + 1}/${py.prompts.length}: ${label}\n${'─'.repeat(68)}`);

      const result: LeadResult = {
        label,
        ticker: lead.ticker,
        theme: lead.theme,
        direction_bias: lead.direction_bias,
        confidence: lead.confidence,
        status: 'failed',
        warren_action: 'Unclear',
        warren_score: null,
        summary: lead.why_from_nitter.slice(0, 2).join(' | '),
        risk_notes: lead.risk_notes,
        prompt_file: null,
        response_excerpt: '',
        response_file: null,
        error: null,
      };

      try {
        await client.prepareForPrompt(stepName);
        if (!(await client.isChatReady())) {
          await client.reloadPage();
          await client.ensureLoggedIn();
          await client.prepareForPrompt(stepName);
        }
        summary.submitted_to_warren = true;
        const response = await client.submitPromptAndWait(prompt, stepName);
        const { action, score } = extractActionAndScore(response);
        result.status = 'ok';
        result.warren_action = action;
        result.warren_score = score;
        result.response_excerpt = response.slice(0, 600);

        const fname = `result_${String(i + 1).padStart(2, '0')}_${safeSlug(label)}.md`;
        const fpath = path.join(followupDir, fname);
        const md = [
          `# Nitter Follow-Up ${i + 1}: ${label}`,
          '',
          `_Generated: ${ts()}_`,
          '',
          `- Direction bias (Nitter): ${lead.direction_bias}`,
          `- Confidence (Nitter ranking): ${lead.confidence}`,
          `- Warren final action: ${action}`,
          `- Warren score: ${score ?? 'n/a'}`,
          `- Risk notes: ${lead.risk_notes.join('; ')}`,
          '',
          '---',
          '',
          '## Nitter signal',
          lead.why_from_nitter.map((w) => `- ${w}`).join('\n'),
          '',
          '## Warren response',
          '',
          response,
          '',
        ].join('\n');
        fs.writeFileSync(fpath, md, 'utf-8');
        result.response_file = fpath;
        anyOk = true;
        console.log(`[nitter] ✓ ${label}: action=${action} score=${score ?? 'n/a'}`);
      } catch (e) {
        result.status = 'failed';
        result.error = String(e).slice(0, 300);
        console.warn(`[nitter] ✗ ${label}: ${result.error}`);
      }
      results.push(result);
      await sleep(2000);
    }

    summary.results = results;
    summary.status = anyOk ? 'ok' : 'failed';
    summary.skip_reason = anyOk ? '' : 'All Nitter follow-up prompts failed.';
    writeResults(followupDir, summary);

    console.log('');
    console.log(line);
    console.log('  NITTER FOLLOW-UP — COMPLETE');
    console.log(`  Leads sent:     ${results.length}`);
    console.log(`  Succeeded:      ${results.filter((r) => r.status === 'ok').length}`);
    console.log(`  Output:         ${followupDir}`);
    console.log(line);
    console.log('');

    await client.close();
    process.exit(0);
  } catch (err) {
    summary.status = 'failed';
    summary.skip_reason = `Unexpected error: ${String(err).slice(0, 200)}`;
    console.error('[nitter] Unexpected error:', err);
    writeResults(followupDir, summary);
    try { await client.close(); } catch { /* ignore */ }
    process.exit(0); // never fail the normal run
  }
}

main().catch((e) => {
  console.error('[nitter] Fatal:', e);
  process.exit(0); // isolation: a follow-up crash must not break the morning run
});
