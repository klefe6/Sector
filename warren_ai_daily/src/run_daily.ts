/**
 * run_daily.ts — Main orchestrator for the Warren AI daily swing trading workflow.
 *
 * Modes:
 *   npm run run-daily           — full run, persistent browser profile
 *   npm run run-daily:resume    — skip steps that already have output files for today
 *   npm run run-daily:cdp       — connect to an already-running Chrome via CDP
 *   npm run run-daily:visible   — headless=false, keeps browser open after run
 *   npm run dry-run             — print resolved prompts, no browser
 */
import { loadConfig } from './config';
import { buildPrompt, buildThemeScanPrompt, StepOutputs } from './prompt_loader';
import { ReportWriter } from './report_writer';
import { WarrenClient } from './warren_client';
import { runValidation } from './validator';
import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STEP_LABELS: Record<number, string> = {
  1: 'Market Regime and Sector Readiness',
  2: 'Top 4 Sectors and 16 Ticker Candidates',
  3: 'Full Technical, Fundamental, and Valuation Table',
  4: 'Execution Plan and Ranking',
  5: 'Final Morning Decision Report',
};

const THEME_SCAN_LABEL = 'Theme Opportunity Scan and Weekly Theme Scoring';

function todayString(): string {
  return new Date().toISOString().slice(0, 10);
}

function nowTimeString(): string {
  return new Date().toLocaleTimeString('en-US', { hour12: false });
}

function sleep(ms: number): Promise<void> {
  return new Promise((res) => setTimeout(res, ms));
}

function printBanner(step: number, extra = ''): void {
  const line = '═'.repeat(68);
  console.log(`\n${line}`);
  console.log(`  STEP ${step}: ${STEP_LABELS[step]?.toUpperCase()}${extra ? '  ' + extra : ''}`);
  console.log(`${line}\n`);
}

/**
 * Attempt to load a saved step output file.
 * Returns null if the file does not exist or is empty.
 */
function loadSavedStep(dayDir: string, step: number): string | null {
  const filepath = path.join(dayDir, `step_0${step}.md`);
  if (!fs.existsSync(filepath)) return null;
  const raw = fs.readFileSync(filepath, 'utf-8').trim();
  // Strip the header we wrote (lines starting with #, _, ---)
  const lines = raw.split('\n');
  const bodyStart = lines.findIndex((l) => l.startsWith('---'));
  const body = bodyStart >= 0 ? lines.slice(bodyStart + 1).join('\n').trim() : raw;
  return body.length > 10 ? body : null;
}

// ---------------------------------------------------------------------------
// Theme Scan (Step 2b) — optional, non-fatal
// ---------------------------------------------------------------------------

async function runThemeScan(
  client: WarrenClient,
  config: ReturnType<typeof loadConfig>,
  outputs: StepOutputs,
  date: string,
  dayDir: string,
  timeStr: string,
): Promise<void> {
  const line = '─'.repeat(68);
  console.log(`\n${line}`);
  console.log(`  STEP 2b: ${THEME_SCAN_LABEL.toUpperCase()}`);
  console.log(`  (Optional — non-fatal if theme prompt missing or response short)`);
  console.log(`${line}\n`);

  // Skip if already done today
  const themeScanFile = path.join(dayDir, 'theme_scan.md');
  if (fs.existsSync(themeScanFile)) {
    const existing = fs.readFileSync(themeScanFile, 'utf-8');
    if (existing.length > 200) {
      console.log('[run:theme] ✓ theme_scan.md already exists for today — skipping.');
      runThemeParser(dayDir, config);
      return;
    }
  }

  // Build prompt
  let promptText: string | null;
  try {
    promptText = buildThemeScanPrompt(config.promptsDir, outputs, date, timeStr, config.outputDir);
  } catch (err) {
    console.warn(`[run:theme] ⚠ Could not build theme scan prompt: ${err} — skipping.`);
    return;
  }
  if (!promptText) {
    console.log('[run:theme] ℹ 02b_theme_scan.md not found — skipping theme scan.');
    return;
  }

  console.log(`[run:theme] Theme scan prompt ready (${promptText.length} chars)`);
  console.log('[run:theme] Pausing 4s before theme scan…');
  await sleep(4000);

  // Reload page so theme scan starts in a clean chat context
  if (!(await client.isChatReady())) {
    const ok = await client.reloadPage();
    if (!ok) {
      console.warn('[run:theme] ⚠ Chat not ready for theme scan — skipping.');
      return;
    }
  }

  // Submit
  let responseText: string;
  try {
    responseText = await client.submitPromptAndWait(promptText, 'theme_scan');
  } catch (err) {
    console.warn(`[run:theme] ⚠ Theme scan submission failed: ${err} — skipping.`);
    return;
  }

  if (responseText.length < 300) {
    console.warn(`[run:theme] ⚠ Theme scan response very short (${responseText.length} chars) — saving anyway.`);
  }

  // Save theme_scan.md
  fs.mkdirSync(dayDir, { recursive: true });
  const header = [
    `# Theme Opportunity Scan`,
    ``,
    `_Generated: ${new Date().toISOString()}_`,
    ``,
    `---`,
    ``,
  ].join('\n');
  fs.writeFileSync(themeScanFile, header + responseText, 'utf-8');
  console.log(`[run:theme] ✓ theme_scan.md written (${responseText.length} chars)`);

  // Run Python parser to extract JSON outputs
  runThemeParser(dayDir, config);
}

function runThemeParser(dayDir: string, config: ReturnType<typeof loadConfig>): void {
  try {
    const scriptPath = path.join(__dirname, '..', '..', 'theme_scan_parser.py');
    const projectRoot = path.join(__dirname, '..', '..');
    const envArgs = `THEME_SCAN_DIR="${dayDir}"`;
    execSync(
      `${envArgs} python3 "${scriptPath}" --day-dir "${dayDir}"`,
      { cwd: projectRoot, stdio: 'inherit', timeout: 30_000 },
    );
    console.log('[run:theme] ✓ Theme scan parser completed.');
  } catch (err) {
    console.warn(`[run:theme] ⚠ Theme scan parser failed: ${err} — diagnostics will be inferred.`);
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const config = loadConfig();
  const date   = todayString();
  const writer = new ReportWriter(config.outputDir, date, config.dryRun);

  console.log('');
  console.log('╔══════════════════════════════════════════════════════════════════╗');
  console.log('║          WARREN AI DAILY SWING TRADING RUNNER                   ║');
  console.log(`║  Date: ${date}  Mode: ${config.browserMode.toUpperCase().padEnd(10)}  Resume: ${config.resume ? 'YES' : 'NO '}           ║`);
  console.log('╚══════════════════════════════════════════════════════════════════╝');
  console.log('');

  // ── Day-of-week check (only run Tue–Sat) ─────────────────────────────
  const now = new Date();
  const dayOfWeek = now.getDay(); // 0=Sunday, 1=Monday, 2=Tuesday, ..., 6=Saturday
  
  if (dayOfWeek === 0 || dayOfWeek === 1) {
    const dayName = dayOfWeek === 0 ? 'Sunday' : 'Monday';
    console.log(`[run] Today is ${dayName} — automation is DISABLED (only runs Tue–Sat).`);
    console.log('[run] Exiting cleanly without running Warren AI workflow.');
    process.exit(0);
    return;
  }
  console.log(`[run] Day of week check passed (day ${dayOfWeek} — Tue–Sat) ✓`);

  // ── Dry-run mode ─────────────────────────────────────────────────────────
  if (config.dryRun) {
    console.log('[dry-run] DRY_RUN=true — prompts will be printed, no browser launched.\n');
    const outputs: StepOutputs = {};
    for (let step = 1; step <= 5; step++) {
      const promptText = buildPrompt(config.promptsDir, step, outputs, date, nowTimeString(), config.outputDir);
      writer.printDryRunSummary(step, promptText);
      const placeholder = `[DRY RUN — step ${step} output]`;
      if (step === 1) outputs.step01 = placeholder;
      if (step === 2) outputs.step02 = placeholder;
      if (step === 3) outputs.step03 = placeholder;
      if (step === 4) outputs.step04 = placeholder;
    }
    console.log('[dry-run] Done.');
    process.exit(0);
  }

  // ── Validate prompts directory ───────────────────────────────────────────
  if (!fs.existsSync(config.promptsDir)) {
    console.error(`[error] Prompts directory not found: ${config.promptsDir}`);
    process.exit(1);
  }

  // ── Resume: load any already-saved step outputs ──────────────────────────
  const outputs: StepOutputs = {};
  const dayDir = path.join(config.outputDir, date);

  if (config.resume) {
    console.log('[resume] Checking for previously saved outputs…');
    for (let step = 1; step <= 4; step++) {
      const saved = loadSavedStep(dayDir, step);
      if (saved) {
        console.log(`[resume] ✓ Step ${step} already done (${saved.length} chars) — will skip`);
        if (step === 1) outputs.step01 = saved;
        if (step === 2) outputs.step02 = saved;
        if (step === 3) outputs.step03 = saved;
        if (step === 4) outputs.step04 = saved;
      }
    }
  }

  // ── Launch browser ───────────────────────────────────────────────────────
  const client = new WarrenClient(config);
  let lastStepFailed = false;

  try {
    await client.launch();
    await client.openPage();

    // Confirm login
    const loggedIn = await client.ensureLoggedIn();
    if (!loggedIn) {
      console.error('[run] Could not confirm login. Saving artifacts and aborting.');
      fs.mkdirSync(dayDir, { recursive: true });
      await client.screenshotOnError(dayDir);
      await client.htmlSnapshotOnError(dayDir);
      writer.writeSummary(false);
      await client.close();
      process.exit(1);
    }

    // ── Run each step ─────────────────────────────────────────────────────
    for (let step = 1; step <= 5; step++) {
      const label = STEP_LABELS[step];

      // Resume: skip if we already have output
      const alreadySaved = loadSavedStep(dayDir, step);
      if (config.resume && alreadySaved) {
        printBanner(step, '[SKIPPED — saved output loaded]');
        writer.beginStep(step);
        // Mark as skipped in the summary
        const fakeFile = path.join(dayDir, `step_0${step}.md`);
        writer.completeStep(step, fakeFile);
        continue;
      }

      printBanner(step);
      writer.beginStep(step);

      // Recovery: if previous step failed or chat disappeared, reload
      if (lastStepFailed || !(await client.isChatReady())) {
        console.log('[run] Chat input missing or prior step failed — reloading page…');
        const ready = await client.reloadPage();
        if (!ready) {
          // If still broken, ensure login
          const ok = await client.ensureLoggedIn();
          if (!ok) {
            const msg = `Chat unavailable before step ${step} and could not recover`;
            writer.failStep(step, msg);
            await client.screenshotOnError(dayDir);
            writer.writeSummary(false);
            await client.close();
            process.exit(1);
          }
        }
        lastStepFailed = false;
      }

      // Before step 2, export current RRG sector scores so the prompt can
      // include any Leading/Improving sectors missed by Warren AI's step 01.
      if (step === 2) {
        try {
          const scriptPath = path.join(__dirname, '..', 'scripts', 'export_rrg_sectors.py');
          const projectRoot = path.join(__dirname, '..', '..');
          execSync(`python3 "${scriptPath}"`, {
            cwd: projectRoot,
            stdio: 'inherit',
            timeout: 30_000,
          });
        } catch (err) {
          console.warn(`[run] ⚠ Could not export RRG sectors: ${err}. Continuing without RRG injection.`);
        }
      }

      // Build prompt with prior outputs substituted in
      let promptText: string;
      try {
        promptText = buildPrompt(config.promptsDir, step, outputs, date, nowTimeString(), config.outputDir);
      } catch (err) {
        const msg = `Failed to load prompt for step ${step}: ${err}`;
        console.error(`[run] ${msg}`);
        writer.failStep(step, msg);
        writer.writeSummary(false);
        await client.close();
        process.exit(1);
      }

      console.log(`[run] Prompt ready (${promptText.length} chars)`);

      // Submit and wait
      let responseText: string;
      try {
        responseText = await client.submitPromptAndWait(promptText, `step${step}`);
      } catch (err) {
        const msg = `Step ${step} submission/response failed: ${err}`;
        console.error(`[run] ${msg}`);
        writer.failStep(step, String(err));
        lastStepFailed = true;

        const errorPng  = await client.screenshotOnError(dayDir, `error_step${step}.png`);
        const errorHtml = await client.htmlSnapshotOnError(dayDir, `error_step${step}.html`);
        writer.recordErrorFile(errorPng || errorHtml);
        writer.writeSummary(false);
        await client.close();
        process.exit(1);
      }

      // Suspicion check: flag short responses but continue
      const suspicious = responseText.length < config.minResponseChars;
      if (suspicious) {
        console.warn(
          `[run] ⚠ Step ${step} response is only ${responseText.length} chars ` +
          `(threshold: ${config.minResponseChars}). Flagging as suspicious but continuing.`
        );
      }

      // Save output
      const outputFile = writer.writeStep(step, responseText, suspicious);
      writer.completeStep(step, outputFile);
      lastStepFailed = false;

      // Cache for placeholder substitution in later prompts
      if (step === 1) outputs.step01 = responseText;
      if (step === 2) outputs.step02 = responseText;
      if (step === 3) outputs.step03 = responseText;
      if (step === 4) outputs.step04 = responseText;

      if (step === 5) {
        writer.writeFinalReport(responseText);
      }

      // ── Step 2b: Theme Opportunity Scan (optional, runs after step 2) ───
      if (step === 2) {
        await runThemeScan(client, config, outputs, date, dayDir, nowTimeString());
      }

      if (step < 5) {
        console.log(`[run] Step ${step} done. Pausing 3s…`);
        await sleep(3000);
      }
    }

    // ── Validation ────────────────────────────────────────────────────────
    // Write summary before validation so run_summary.json exists on disk
    // when the validator checks for it (it runs after all 5 steps succeed).
    writer.writeSummary(true);

    console.log('\n[run] Running output validation…');
    const validation = runValidation(dayDir);
    writer.setValidationResult(validation);

    // ── Done — overwrite summary to include validationPassed field ─────────
    writer.writeSummary(true);

    console.log('');
    console.log('╔══════════════════════════════════════════════════════════════════╗');
    console.log('║  ✓ All 5 steps completed.                                       ║');
    if (!validation.passed) {
      console.log('║  ⚠ Validation warnings present — see validation_warnings.md    ║');
    }
    console.log(`║  Output: ${dayDir}`.padEnd(69) + '║');
    console.log('╚══════════════════════════════════════════════════════════════════╝');
    console.log('');

    await client.close();
    process.exit(0);

  } catch (err) {
    console.error(`\n[run] Unexpected error: ${err}`);
    try {
      fs.mkdirSync(dayDir, { recursive: true });
      await client.screenshotOnError(dayDir);
      await client.htmlSnapshotOnError(dayDir);
    } catch { /* best effort */ }
    writer.writeSummary(false);
    await client.close();
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('[fatal]', err);
  process.exit(1);
});
