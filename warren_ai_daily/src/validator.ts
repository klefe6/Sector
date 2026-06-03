/**
 * validator.ts — Post-run validation of Warren AI daily outputs.
 *
 * Checks:
 *   1. All 5 step files exist
 *   2. run_summary.json exists and reports success: true
 *   3. final_action_plan.md exists
 *   4. final_action_plan.md is not too short (≥ 800 chars)
 *   5. final_action_plan.md contains an actionable section (trade/ticker mentions)
 *   6. step_02.md contains at least 16 ticker symbols
 *   7. Any step file marked suspicious in run_summary gets flagged
 *
 * Writes output/YYYY-MM-DD/validation_warnings.md only if issues are found.
 * Never deletes or modifies run outputs.
 */
import fs from 'fs';
import path from 'path';

export interface ValidationResult {
  passed: boolean;
  warnings: string[];
}

// Regex: 1-5 uppercase letters, optionally .B or similar (BRK.B, T, NVDA, XLRE)
const TICKER_RE = /\b[A-Z]{1,5}(?:\.[A-Z])?\b/g;

// Common non-ticker uppercase words to filter out
const NOISE = new Set([
  'A', 'I', 'S', 'P', 'Q', 'R', 'N', 'B', 'C', 'Y', 'M', 'D', 'H',
  'AT', 'IS', 'FOR', 'IT', 'OR', 'AND', 'THE', 'BUT', 'NOT', 'YES', 'NO',
  'OK', 'IN', 'ON', 'UP', 'BY', 'TO', 'OF', 'DO', 'IF',
  'RSI', 'EMA', 'SMA', 'ATR', 'CEO', 'IPO', 'ETF', 'SPY', 'USD', 'GDP',
  'VIX', 'PCT', 'YTD', 'TTM', 'LTM', 'YOY', 'QOQ', 'MOM',
  'US', 'EU', 'UK', 'FED', 'ECB', 'BOJ',
  'AM', 'PM', 'EST', 'EDT', 'UTC',
]);

function extractTickers(text: string): string[] {
  const matches = text.match(TICKER_RE) ?? [];
  return [...new Set(matches.filter((t) => t.length >= 2 && !NOISE.has(t)))];
}

// Patterns we expect in a good final report
const FINAL_REPORT_SECTIONS = [
  {
    key: 'trade_or_ticker',
    label: 'trade or ticker recommendation',
    patterns: [
      /Trade \d/i,
      /\benter\b/i,
      /\bentry\b/i,
      /\bbuy\b/i,
      /\bposition\b/i,
      /\bswing\b/i,
      /\bbreakout\b/i,
      /\bpullback\b/i,
    ],
  },
  {
    key: 'stop_or_risk',
    label: 'stop loss or risk management',
    patterns: [
      /stop/i,
      /risk/i,
      /R:R/i,
      /risk.reward/i,
      /loss limit/i,
    ],
  },
  {
    key: 'sector_or_regime',
    label: 'market regime or sector context',
    patterns: [
      /regime/i,
      /sector/i,
      /SPY/i,
      /VIX/i,
      /risk.on/i,
      /risk.off/i,
      /bull/i,
      /bear/i,
    ],
  },
];

export function validateStep2(text: string): string[] {
  const warnings: string[] = [];
  const tickers = extractTickers(text);
  if (tickers.length < 16) {
    warnings.push(
      `Step 2 contains only ${tickers.length} unique ticker symbols (expected ≥16). ` +
      `Found: ${tickers.slice(0, 25).join(', ')}`
    );
  }
  return warnings;
}

export function validateFinalReport(text: string): string[] {
  const warnings: string[] = [];

  // Length check
  if (text.length < 800) {
    warnings.push(
      `final_action_plan.md is only ${text.length} characters. Expected ≥800. ` +
      `The report may be incomplete or truncated.`
    );
  }

  // Section checks
  for (const section of FINAL_REPORT_SECTIONS) {
    const found = section.patterns.some((re) => re.test(text));
    if (!found) {
      warnings.push(
        `final_action_plan.md appears to be missing a ${section.label} section. ` +
        `Expected one of: ${section.patterns.map((p) => p.source).join(' | ')}`
      );
    }
  }

  return warnings;
}

export function runValidation(dayDir: string): ValidationResult {
  const warnings: string[] = [];

  // ── 1. All 5 step files exist ──────────────────────────────────────────
  for (let step = 1; step <= 5; step++) {
    const fp = path.join(dayDir, `step_0${step}.md`);
    if (!fs.existsSync(fp)) {
      warnings.push(`step_0${step}.md is missing from ${dayDir}`);
    } else {
      const size = fs.statSync(fp).size;
      if (size < 100) {
        warnings.push(`step_0${step}.md exists but is very small (${size} bytes) — may be empty`);
      }
    }
  }

  // ── 2. run_summary.json ───────────────────────────────────────────────
  const summaryPath = path.join(dayDir, 'run_summary.json');
  if (!fs.existsSync(summaryPath)) {
    warnings.push('run_summary.json is missing');
  } else {
    try {
      const summary = JSON.parse(fs.readFileSync(summaryPath, 'utf-8'));
      if (!summary.success) {
        warnings.push('run_summary.json reports success: false — run did not complete cleanly');
      }
      // Check for suspicious steps flagged during the run
      const suspiciousSteps = (summary.steps ?? []).filter(
        (s: { suspicious?: boolean; step: number }) => s.suspicious
      );
      for (const s of suspiciousSteps) {
        warnings.push(
          `Step ${s.step} was flagged as suspicious during the run (response too short). ` +
          `Review step_0${s.step}.md for completeness.`
        );
      }
    } catch {
      warnings.push('run_summary.json exists but could not be parsed as JSON');
    }
  }

  // ── 3. final_action_plan.md ───────────────────────────────────────────
  const finalPath  = path.join(dayDir, 'final_action_plan.md');
  const step5Path  = path.join(dayDir, 'step_05.md');
  const reportPath = fs.existsSync(finalPath) ? finalPath : step5Path;

  if (!fs.existsSync(reportPath)) {
    warnings.push('final_action_plan.md and step_05.md are both missing');
  } else {
    const raw  = fs.readFileSync(reportPath, 'utf-8');
    // Strip the header we write (lines before the --- separator)
    const sep  = raw.indexOf('\n---\n');
    const body = sep >= 0 ? raw.slice(sep + 5).trim() : raw.trim();
    warnings.push(...validateFinalReport(body));
  }

  // ── 4. step_02 ticker count ────────────────────────────────────────────
  const step2Path = path.join(dayDir, 'step_02.md');
  if (fs.existsSync(step2Path)) {
    const step2 = fs.readFileSync(step2Path, 'utf-8');
    warnings.push(...validateStep2(step2));
  }

  // ── Write or skip validation_warnings.md ─────────────────────────────
  const warningsPath = path.join(dayDir, 'validation_warnings.md');

  if (warnings.length > 0) {
    const content = [
      `# Validation Warnings`,
      ``,
      `_Generated: ${new Date().toISOString()}_`,
      ``,
      `The following issues were detected. Run outputs have NOT been deleted or modified.`,
      ``,
      ...warnings.map((w, i) => `${i + 1}. ${w}`),
      ``,
      `---`,
      ``,
      `To re-run the daily workflow: \`npm run run-daily:cdp\``,
      `To re-run from a specific failed step: \`npm run run-daily:resume\``,
    ].join('\n');
    fs.writeFileSync(warningsPath, content, 'utf-8');
    console.log(`[validation] ⚠ ${warnings.length} warning(s) — written to: ${warningsPath}`);
    warnings.forEach((w, i) => console.log(`  ${i + 1}. ${w}`));
  } else {
    // Remove stale warnings file from a previous run if all checks now pass
    if (fs.existsSync(warningsPath)) {
      fs.unlinkSync(warningsPath);
      console.log('[validation] ✓ All checks passed — removed stale validation_warnings.md');
    } else {
      console.log('[validation] ✓ All checks passed');
    }
  }

  return { passed: warnings.length === 0, warnings };
}
