/**
 * report_writer.ts — Write step outputs, final report, and run_summary.json.
 *
 * Output structure for a given run date (YYYY-MM-DD):
 *   output/YYYY-MM-DD/
 *     step_01.md          raw response from Warren AI for prompt 1
 *     step_02.md
 *     step_03.md
 *     step_04.md
 *     step_05.md
 *     final_action_plan.md  copy of step_05.md renamed for clarity
 *     run_summary.json      metadata: timestamps, success, file paths
 *     error.png             (only on failure)
 *     error.html            (only on failure)
 */
import fs from 'fs';
import path from 'path';

export interface StepRecord {
  step: number;
  label: string;
  startedAt: string;
  completedAt: string | null;
  success: boolean;
  outputFile: string | null;
  error: string | null;
  suspicious?: boolean;
  warning?: string;
}

export interface RunSummary {
  date: string;
  startedAt: string;
  completedAt: string | null;
  success: boolean;
  dryRun: boolean;
  steps: StepRecord[];
  files: string[];
  errorFile: string | null;
  validationPassed?: boolean;
  validationWarnings?: string[];
}

const STEP_LABELS: Record<number, string> = {
  1: 'Market Regime and Sector Readiness',
  2: 'Top 4 Sectors and 16 Ticker Candidates',
  3: 'Full Technical, Fundamental, and Valuation Table',
  4: 'Execution Plan and Ranking',
  5: 'Final Morning Decision Report',
};

export class ReportWriter {
  private readonly dayDir: string;
  private readonly summary: RunSummary;

  constructor(outputRoot: string, date: string, dryRun: boolean) {
    this.dayDir = path.join(outputRoot, date);

    if (!dryRun) {
      fs.mkdirSync(this.dayDir, { recursive: true });
    }

    this.summary = {
      date,
      startedAt: new Date().toISOString(),
      completedAt: null,
      success: false,
      dryRun,
      steps: [],
      files: [],
      errorFile: null,
    };
  }

  get directory(): string {
    return this.dayDir;
  }

  /** Store validation results into the summary. */
  setValidationResult(result: { passed: boolean; warnings: string[] }): void {
    this.summary.validationPassed = result.passed;
    this.summary.validationWarnings = result.warnings;
  }

  /** Write raw response text for a completed step. Returns the file path. */
  writeStep(step: number, content: string, suspicious = false): string {
    const filename = `step_0${step}.md`;
    const filepath = path.join(this.dayDir, filename);

    const suspiciousNote = suspicious
      ? `\n> ⚠ **WARNING: Response was unusually short (< threshold). May be incomplete.**\n`
      : '';

    const header = [
      `# Step ${step}: ${STEP_LABELS[step] ?? 'Unknown'}`,
      ``,
      `_Generated: ${new Date().toISOString()}_`,
      suspiciousNote,
      `---`,
      ``,
      '',
    ].join('\n');

    fs.writeFileSync(filepath, header + content, 'utf-8');
    this.summary.files.push(filepath);
    console.log(`[report] Saved: ${filepath}`);
    return filepath;
  }

  /** Mark a step as started. */
  beginStep(step: number): void {
    this.summary.steps.push({
      step,
      label: STEP_LABELS[step] ?? 'Unknown',
      startedAt: new Date().toISOString(),
      completedAt: null,
      success: false,
      outputFile: null,
      error: null,
    });
  }

  /** Mark a step as completed and record its output file. */
  completeStep(step: number, outputFile: string, suspicious = false): void {
    const record = this.summary.steps.find(s => s.step === step);
    if (record) {
      record.completedAt = new Date().toISOString();
      record.success = true;
      record.outputFile = outputFile;
      if (suspicious) {
        record.suspicious = true;
        record.warning = 'Response was shorter than minimum threshold — may be incomplete';
      }
    }
  }

  /** Mark a step as failed and record the error. */
  failStep(step: number, error: string): void {
    const record = this.summary.steps.find(s => s.step === step);
    if (record) {
      record.completedAt = new Date().toISOString();
      record.success = false;
      record.error = error;
    }
  }

  /** Write the final action plan (alias of step 5 for clarity). */
  writeFinalReport(content: string): string {
    const filepath = path.join(this.dayDir, 'final_action_plan.md');

    const header = [
      `# Final Morning Decision Report`,
      ``,
      `_Generated: ${new Date().toISOString()}_`,
      ``,
      `---`,
      ``,
      '',
    ].join('\n');

    fs.writeFileSync(filepath, header + content, 'utf-8');
    this.summary.files.push(filepath);
    console.log(`[report] Saved final plan: ${filepath}`);
    return filepath;
  }

  /** Finalize and write run_summary.json. */
  writeSummary(success: boolean): string {
    this.summary.completedAt = new Date().toISOString();
    this.summary.success = success;

    const filepath = path.join(this.dayDir, 'run_summary.json');
    fs.writeFileSync(filepath, JSON.stringify(this.summary, null, 2), 'utf-8');
    console.log(`[report] Run summary: ${filepath}`);
    return filepath;
  }

  /** Record an error artifact file path (screenshot or HTML). */
  recordErrorFile(filepath: string): void {
    this.summary.errorFile = filepath;
  }

  /** Print a dry-run summary to console instead of writing files. */
  printDryRunSummary(step: number, promptText: string): void {
    const line = '─'.repeat(72);
    console.log(`\n${line}`);
    console.log(`DRY RUN — Step ${step}: ${STEP_LABELS[step]}`);
    console.log(line);
    console.log(promptText);
    console.log(`${line}\n`);
  }
}
