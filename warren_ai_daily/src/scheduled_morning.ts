/**
 * scheduled_morning.ts — Automated daily runner for 7 AM scheduled execution.
 *
 * This script is invoked by the systemd user timer (warren-ai-daily.timer).
 * It performs all readiness checks before submitting any prompts, and
 * never creates duplicate runs for the same day.
 *
 * Logic:
 *   1. Check if today already has a complete successful run → exit cleanly
 *   2. Check if today has a partial run → use resume mode
 *   3. Check CDP reachability → log failure and exit if not reachable
 *   4. Check Warren AI tab and login → log failure and exit if not ready
 *   5. If all checks pass → run the daily CDP workflow (or resume if partial)
 *
 * Force override: set env WARREN_FORCE_RERUN=true to skip duplicate protection.
 * Dry-run test: set WARREN_SCHEDULE_DRY_RUN=true to check readiness only, no run.
 *
 * Logs are written to: output/scheduler_logs/YYYY-MM-DD.log
 */
import dotenv from 'dotenv';
import { chromium } from 'playwright';
import { execSync, spawn } from 'child_process';
import fs from 'fs';
import https from 'https';
import path from 'path';

dotenv.config();

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const CDP_URL    = process.env.CHROME_CDP_URL    || 'http://127.0.0.1:9222';
const OUTPUT_DIR = path.resolve(process.env.OUTPUT_DIR || './output');
const LOG_DIR    = path.join(OUTPUT_DIR, 'scheduler_logs');
const STATUS_FILE = path.join(OUTPUT_DIR, 'scheduled_morning_status.json');
const FORCE      = process.env.WARREN_FORCE_RERUN    === 'true';
const DRY_RUN    = process.env.WARREN_SCHEDULE_DRY_RUN === 'true';
const PROJECT_DIR = path.resolve(__dirname, '..');

// Warren AI's confirmed chat input selector
const CHAT_INPUT_SEL = '[data-test="chat-input-field"]';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function todayDir(): string {
  return path.join(OUTPUT_DIR, today());
}

function timestamp(): string {
  return new Date().toISOString();
}

class Logger {
  private readonly logPath: string;
  private lines: string[] = [];

  constructor() {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    this.logPath = path.join(LOG_DIR, `${today()}.log`);
    // Append to existing log if it already has entries for today
    if (fs.existsSync(this.logPath)) {
      this.lines.push('');
      this.lines.push('─'.repeat(60));
      this.lines.push(`[${timestamp()}] New scheduler invocation`);
    } else {
      this.lines.push(`Warren AI Scheduled Run — ${today()}`);
      this.lines.push('='.repeat(60));
      this.lines.push(`[${timestamp()}] Scheduler started`);
    }
  }

  log(msg: string): void {
    const line = `[${timestamp()}] ${msg}`;
    this.lines.push(line);
    console.log(line);
  }

  flush(): void {
    const content = this.lines.join('\n') + '\n';
    fs.appendFileSync(this.logPath, content, 'utf-8');
  }

  get path(): string { return this.logPath; }
}

// ---------------------------------------------------------------------------
// Readiness checks
// ---------------------------------------------------------------------------

function checkTodayStatus(): 'complete' | 'partial' | 'none' {
  const dir = todayDir();
  if (!fs.existsSync(dir)) return 'none';

  const summaryPath = path.join(dir, 'run_summary.json');
  if (fs.existsSync(summaryPath)) {
    try {
      const summary = JSON.parse(fs.readFileSync(summaryPath, 'utf-8'));
      if (summary.success === true) return 'complete';
    } catch { /* fall through */ }
  }

  // Check for partial: at least one step file exists
  const hasAnyStep = [1,2,3,4,5].some((n) =>
    fs.existsSync(path.join(dir, `step_0${n}.md`))
  );
  return hasAnyStep ? 'partial' : 'none';
}

async function checkCdpAndWarren(): Promise<{
  cdpReachable: boolean;
  warrenTabFound: boolean;
  chatVisible: boolean;
  tabUrl: string;
}> {
  let browser;
  try {
    browser = await chromium.connectOverCDP(CDP_URL, { timeout: 15000 });
  } catch {
    return { cdpReachable: false, warrenTabFound: false, chatVisible: false, tabUrl: '' };
  }

  try {
    const ctx   = browser.contexts()[0];
    const pages = ctx?.pages() ?? [];
    const warrenPage = pages.find((p) => p.url().includes('warrenai'));

    if (!warrenPage) {
      return { cdpReachable: true, warrenTabFound: false, chatVisible: false, tabUrl: pages[0]?.url() ?? '' };
    }

    let chatVisible = false;
    try {
      chatVisible = await warrenPage.locator(CHAT_INPUT_SEL).last()
        .isVisible({ timeout: 5000 }).catch(() => false);
    } catch { /* ignore */ }

    return {
      cdpReachable:  true,
      warrenTabFound: true,
      chatVisible,
      tabUrl:        warrenPage.url(),
    };
  } finally {
    await browser.close();
  }
}

// ---------------------------------------------------------------------------
// Run the daily workflow as a child process
// ---------------------------------------------------------------------------

function runDailyWorkflow(resume: boolean): Promise<{ success: boolean; exitCode: number }> {
  return new Promise((resolve) => {
    const script = resume ? 'run-daily:resume' : 'run-daily:cdp';
    const env = {
      ...process.env,
      DISPLAY: process.env.DISPLAY || ':1',
      WARREN_BROWSER_MODE: 'cdp',
    };

    const child = spawn('npm', ['run', script], {
      cwd: PROJECT_DIR,
      env,
      stdio: ['ignore', 'inherit', 'inherit'],
    });

    child.on('exit', (code) => {
      resolve({ success: code === 0, exitCode: code ?? 1 });
    });

    child.on('error', (err) => {
      console.error('[scheduler] Failed to spawn npm process:', err);
      resolve({ success: false, exitCode: 1 });
    });
  });
}

// ---------------------------------------------------------------------------
// Run status tracking
// ---------------------------------------------------------------------------

interface RunStatus {
  started_at:        string;
  finished_at:       string;
  bootstrap_status:  'ok' | 'failed' | 'skipped';
  cdp_check_status:  'ok' | 'failed' | 'skipped';
  capture_status:
    | 'ok'
    | 'failed'
    | 'skipped'
    | 'skipped_off_day'   // legacy alias — do not assign; use 'skipped'
    | 'dry_run'
    | 'already_complete';
  output_fresh:      boolean;
  latest_output_path: string;
  error_message:     string;
}

/** Write status to disk — best-effort, never throws. */
function writeRunStatus(status: Partial<RunStatus>): void {
  try {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    fs.writeFileSync(
      STATUS_FILE,
      JSON.stringify({ ...status, finished_at: status.finished_at || timestamp() }, null, 2),
      'utf-8',
    );
  } catch { /* best-effort */ }
}

/** Return true if today's run_summary.json exists and was written in the last 12 h. */
function checkOutputFresh(): { fresh: boolean; path: string } {
  const dir = todayDir();
  const summary = path.join(dir, 'run_summary.json');
  if (!fs.existsSync(summary)) return { fresh: false, path: dir };
  const ageMs = Date.now() - fs.statSync(summary).mtimeMs;
  return { fresh: ageMs < 12 * 60 * 60 * 1000, path: dir };
}

/**
 * Send a Telegram message when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are
 * configured in .env. Silently no-ops when not configured. Never throws.
 */
async function sendTelegramAlert(message: string): Promise<void> {
  const token  = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_CHAT_ID;
  if (!token || !chatId) return;
  const payload = JSON.stringify({ chat_id: chatId, text: message });
  return new Promise((resolve) => {
    const req = https.request(
      `https://api.telegram.org/bot${token}/sendMessage`,
      {
        method: 'POST',
        headers: {
          'Content-Type':   'application/json',
          'Content-Length': Buffer.byteLength(payload),
        },
      },
      (res) => { res.resume(); resolve(); },
    );
    req.on('error', () => resolve());
    req.setTimeout(6000, () => { req.destroy(); resolve(); });
    req.write(payload);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const logger = new Logger();
  const startedAt = timestamp();
  const status: Partial<RunStatus> = {
    started_at:        startedAt,
    bootstrap_status:  'skipped',
    cdp_check_status:  'skipped',
    capture_status:    'skipped',
    output_fresh:      false,
    latest_output_path: todayDir(),
    error_message:     '',
  };

  logger.log(`Project dir: ${PROJECT_DIR}`);
  logger.log(`Output dir: ${OUTPUT_DIR}`);
  logger.log(`FORCE_RERUN: ${FORCE}, DRY_RUN: ${DRY_RUN}`);

  // ── Step 0: Day-of-week check ─────────────────────────────────────────
  const now = new Date();
  const dayOfWeek = now.getDay(); // 0=Sunday, 1=Monday, 2=Tuesday, ..., 6=Saturday
  
  if (dayOfWeek === 0 || dayOfWeek === 1) {
    const dayName = dayOfWeek === 0 ? 'Sunday' : 'Monday';
    logger.log(`Today is ${dayName} — automation is DISABLED (only runs Tue–Sat).`);
    logger.log('Exiting cleanly without running Warren AI workflow.');
    status.capture_status = 'skipped';
    status.error_message = `Scheduled off-day (${dayName})`;
    writeRunStatus({ ...status, finished_at: timestamp() });
    logger.flush();
    process.exit(0);
    return;
  }
  logger.log(`Day of week check passed (day ${dayOfWeek} — Tue–Sat allowed) ✓`);

  // ── Step 1: Duplicate protection ─────────────────────────────────────
  const todayStatus = checkTodayStatus();
  logger.log(`Today (${today()}) status: ${todayStatus}`);

  if (todayStatus === 'complete' && !FORCE) {
    logger.log('Run already completed successfully for today. Exiting cleanly.');
    logger.log('Set WARREN_FORCE_RERUN=true to override duplicate protection.');
    status.capture_status    = 'already_complete';
    status.output_fresh      = true;
    status.latest_output_path = todayDir();
    writeRunStatus(status);
    logger.flush();
    process.exit(0);
    return;
  }

  if (todayStatus === 'partial') {
    logger.log('Partial run detected — will use RESUME mode to continue from last completed step.');
  }

  if (todayStatus === 'none') {
    logger.log('No run found for today — will start a fresh full run.');
  }

  if (FORCE) {
    logger.log('FORCE_RERUN=true — duplicate protection bypassed.');
  }

  // ── Step 1.5: Bootstrap Chrome ───────────────────────────────────────
  // Starts Chrome if not running; opens Warren tab if missing.
  // Does NOT bypass login or Cloudflare — only fixes missing browser state.
  const bootstrapScript = path.resolve(PROJECT_DIR, 'scripts', 'bootstrap_warren_chrome.sh');
  logger.log(`Bootstrapping Chrome CDP via ${bootstrapScript}…`);
  try {
    execSync(`bash "${bootstrapScript}"`, {
      env: { ...process.env, DISPLAY: process.env.DISPLAY || ':1' },
      stdio: ['ignore', 'inherit', 'inherit'],
      timeout: 45_000,
    });
    logger.log('Bootstrap succeeded ✓');
  } catch (err: any) {
    const bsMsg = `Chrome could not start or CDP unreachable after bootstrap (exit ${err.status ?? '?'}).`;
    logger.log(`FAIL: ${bsMsg}`);
    logger.log(`Chrome launch log: /tmp/warren-chrome-bootstrap.log`);
    status.bootstrap_status = 'failed';
    status.error_message    = bsMsg;
    writeRunStatus(status);
    await sendTelegramAlert(`⛔ Warren AI: ${bsMsg}\nLog: /tmp/warren-chrome-bootstrap.log`);
    logger.flush();
    process.exit(1);
  }
  status.bootstrap_status = 'ok';

  // ── Step 2: CDP check ────────────────────────────────────────────────
  logger.log(`Checking CDP at ${CDP_URL}…`);
  const cdp = await checkCdpAndWarren();

  if (!cdp.cdpReachable) {
    const msg = 'Chrome CDP is not reachable even after bootstrap.';
    logger.log(`FAIL: ${msg}`);
    logger.log('  Run: google-chrome --remote-debugging-port=9222 \\');
    logger.log("         --remote-allow-origins='*' \\");
    logger.log('         --user-data-dir="$HOME/.warren-ai-chrome-profile"');
    logger.log(`Log written: ${logger.path}`);
    status.cdp_check_status = 'failed';
    status.error_message    = msg;
    writeRunStatus(status);
    await sendTelegramAlert(`⛔ Warren AI: ${msg}`);
    logger.flush();
    process.exit(1);
    return;
  }
  logger.log('CDP reachable ✓');

  if (!cdp.warrenTabFound) {
    const msg = `Warren AI tab not found. Current tab: ${cdp.tabUrl || 'none'}`;
    logger.log(`FAIL: ${msg}`);
    logger.log('ACTION REQUIRED: Open https://www.investing.com/warrenai in Chrome');
    status.cdp_check_status = 'failed';
    status.error_message    = msg;
    writeRunStatus(status);
    await sendTelegramAlert(`⛔ Warren AI: ${msg}\nOpen https://www.investing.com/warrenai in Chrome.`);
    logger.flush();
    process.exit(1);
    return;
  }
  logger.log(`Warren AI tab found: ${cdp.tabUrl} ✓`);

  if (!cdp.chatVisible) {
    const msg = 'Chat input not visible — login or Cloudflare/security check required.';
    logger.log(`FAIL: ${msg}`);
    logger.log('ACTION REQUIRED: Log in to InvestingPro in the Chrome window.');
    status.cdp_check_status = 'failed';
    status.error_message    = msg;
    writeRunStatus(status);
    await sendTelegramAlert(`🔐 Warren AI: ${msg}\nLog in at https://www.investing.com/warrenai`);
    logger.flush();
    process.exit(1);
    return;
  }
  logger.log('Chat input visible ✓');
  status.cdp_check_status = 'ok';

  // ── Step 3: Dry-run exit ─────────────────────────────────────────────
  if (DRY_RUN) {
    logger.log('');
    logger.log('WARREN_SCHEDULE_DRY_RUN=true — skipping actual run (readiness checks only).');
    logger.log('All checks PASSED. Would have run: ' + (todayStatus === 'partial' ? 'run-daily:resume' : 'run-daily:cdp'));
    logger.log(`Log written: ${logger.path}`);
    status.capture_status = 'dry_run';
    writeRunStatus(status);
    logger.flush();
    process.exit(0);
    return;
  }

  // ── Step 4: Run ──────────────────────────────────────────────────────
  const useResume = todayStatus === 'partial';
  logger.log(`Starting ${useResume ? 'RESUME' : 'FULL'} run…`);
  logger.flush(); // flush before spawning so log exists if it crashes

  const result = await runDailyWorkflow(useResume);

  status.capture_status = result.success ? 'ok' : 'failed';
  logger.log(`Run ${result.success ? 'SUCCEEDED' : 'FAILED'} (exit code: ${result.exitCode})`);
  logger.log(`Output dir: ${todayDir()}`);

  // Post-run: read and log the summary
  const summaryPath = path.join(todayDir(), 'run_summary.json');
  if (fs.existsSync(summaryPath)) {
    try {
      const s = JSON.parse(fs.readFileSync(summaryPath, 'utf-8'));
      const steps = (s.steps ?? []).map(
        (st: { step: number; success: boolean; suspicious?: boolean }) =>
          `step${st.step}:${st.success ? 'ok' : 'fail'}${st.suspicious ? '(suspicious)' : ''}`
      ).join(' ');
      logger.log(`Summary: success=${s.success} validationPassed=${s.validationPassed} | ${steps}`);
    } catch { /* ignore */ }
  }

  // Check for validation warnings
  const warningsPath = path.join(todayDir(), 'validation_warnings.md');
  if (fs.existsSync(warningsPath)) {
    logger.log('⚠ validation_warnings.md was created — review outputs manually.');
  }

  // Freshness check — confirms output files were actually written today
  const fresh = checkOutputFresh();
  status.output_fresh       = fresh.fresh;
  status.latest_output_path = fresh.path;

  if (!result.success) {
    const msg = `Warren AI run failed (exit ${result.exitCode}).`;
    status.error_message = msg;
    await sendTelegramAlert(`❌ Warren AI: ${msg}\nCheck logs: ${logger.path}`);
  } else if (!fresh.fresh) {
    const msg = 'Run reported success but no fresh output found (run_summary.json missing or older than 12 h).';
    logger.log(`WARN: ${msg}`);
    status.error_message = msg;
    await sendTelegramAlert(`❌ Warren AI: ${msg}`);
  }

  writeRunStatus(status);
  logger.log(`Status written: ${STATUS_FILE}`);
  logger.log(`Log written: ${logger.path}`);
  logger.flush();
  process.exit(result.success ? 0 : 1);
}

main().catch(async (err) => {
  const logger = new Logger();
  logger.log(`FATAL: ${err}`);
  logger.flush();
  console.error('[scheduler] Fatal:', err);
  writeRunStatus({ started_at: timestamp(), finished_at: timestamp(), error_message: String(err) });
  await sendTelegramAlert(`⛔ Warren AI: Fatal scheduler error — ${err}`);
  process.exit(1);
});
