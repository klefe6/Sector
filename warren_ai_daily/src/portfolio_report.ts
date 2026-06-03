/**
 * portfolio_report.ts — Write portfolio monitor outputs under output/YYYY-MM-DD/.
 */
import fs from 'fs';
import path from 'path';
import type { PortfolioPosition } from './portfolio_loader';

export interface PortfolioStepRecord {
  step: number;
  label: string;
  success: boolean;
  outputFile: string | null;
  error: string | null;
}

export interface PortfolioSummary {
  date: string;
  startedAt: string;
  completedAt: string | null;
  success: boolean;
  dryRun: boolean;
  positionsUsed: PortfolioPosition[];
  steps: PortfolioStepRecord[];
  files: string[];
  warnings: string[];
}

export class PortfolioReportWriter {
  private readonly dayDir: string;
  private readonly summary: PortfolioSummary;

  constructor(outputRoot: string, date: string, dryRun: boolean, positions: PortfolioPosition[]) {
    this.dayDir = path.join(outputRoot, date);
    if (!dryRun) fs.mkdirSync(this.dayDir, { recursive: true });
    this.summary = {
      date,
      startedAt: new Date().toISOString(),
      completedAt: null,
      success: false,
      dryRun,
      positionsUsed: positions,
      steps: [],
      files: [],
      warnings: [],
    };
  }

  get directory(): string {
    return this.dayDir;
  }

  writeStep6(content: string, suspicious = false): string {
    return this._writeStep(6, 'Portfolio news & thesis monitor', content, suspicious);
  }

  writeStep7(content: string, suspicious = false): string {
    return this._writeStep(7, 'Portfolio action plan', content, suspicious);
  }

  private _writeStep(step: number, label: string, content: string, suspicious: boolean): string {
    const filename = `portfolio_step_0${step}.md`;
    const filepath = path.join(this.dayDir, filename);
    const suspiciousNote = suspicious
      ? `\n> ⚠ **WARNING: Response was unusually short.**\n`
      : '';
    const body = [
      `# Portfolio Step ${step}: ${label}`,
      '',
      `_Generated: ${new Date().toISOString()}_`,
      suspiciousNote,
      '---',
      '',
      content,
      '',
    ].join('\n');
    if (!this.summary.dryRun) {
      fs.writeFileSync(filepath, body, 'utf-8');
      if (!this.summary.files.includes(filepath)) this.summary.files.push(filepath);
    }
    this.summary.steps.push({
      step,
      label,
      success: true,
      outputFile: this.summary.dryRun ? null : filepath,
      error: null,
    });
    return filepath;
  }

  failStep(step: number, label: string, err: string): void {
    this.summary.steps.push({ step, label, success: false, outputFile: null, error: err });
  }

  /** Mark a step as satisfied by an existing output file (no new submission). */
  recordSkipped(step: number, label: string, existingPath: string): void {
    this.summary.steps.push({
      step,
      label,
      success: true,
      outputFile: existingPath,
      error: null,
    });
    if (!this.summary.dryRun && fs.existsSync(existingPath) && !this.summary.files.includes(existingPath)) {
      this.summary.files.push(existingPath);
    }
  }

  addWarning(w: string): void {
    this.summary.warnings.push(w);
  }

  writeSummary(success: boolean): void {
    this.summary.completedAt = new Date().toISOString();
    this.summary.success = success;
    const fp = path.join(this.dayDir, 'portfolio_summary.json');
    if (!this.summary.dryRun) {
      fs.writeFileSync(fp, JSON.stringify(this.summary, null, 2) + '\n', 'utf-8');
      this.summary.files.push(fp);
    }
  }
}
