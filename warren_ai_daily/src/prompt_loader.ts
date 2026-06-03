/**
 * prompt_loader.ts — Load prompt markdown files and substitute placeholders.
 *
 * Future portfolio workflow (not wired): see `prompts/portfolio_news_thesis_monitor.md`
 * (Prompt 6 — inject {{CURRENT_PORTFOLIO}}) and `prompts/portfolio_action_plan.md`
 * (Prompt 7 — inject {{PORTFOLIO_MONITOR_REPORT}} from Prompt 6 output).
 * See `prompts/README_portfolio_monitoring.md`.
 *
 * Placeholder tokens in prompt files:
 *   {{DATE}}                        → today's date (YYYY-MM-DD)
 *   {{TIME}}                        → current time (HH:MM)
 *   {{STEP_01_OUTPUT}}              → full output from step 1
 *   {{STEP_02_OUTPUT}}              → full output from step 2
 *   {{STEP_03_OUTPUT}}              → full output from step 3
 *   {{STEP_04_OUTPUT}}              → full output from step 4
 *   {{RRG_MANDATORY_SECTORS}}       → RRG Leading/Improving sectors (from rrg_sectors.json)
 *   [PASTE THE 16 TICKERS FROM PROMPT 2]  → ticker list extracted from step 2
 *   [PASTE TABLE FROM PROMPT 3]           → full output from step 3
 *   [PASTE OUTPUTS FROM PROMPTS 1 TO 4]   → concatenated outputs 1–4
 */
import fs from 'fs';
import path from 'path';

export interface StepOutputs {
  step01?: string;
  step02?: string;
  step03?: string;
  step04?: string;
}

// ---------------------------------------------------------------------------
// RRG sector injection
// ---------------------------------------------------------------------------

interface RrgSectorEntry {
  etf: string;
  quadrant: string;
  direction: string;
  score: number;
}

interface RrgSectorsFile {
  generated_at: string;
  mandatory_sectors: RrgSectorEntry[];
  top4_by_score: RrgSectorEntry[];
}

/**
 * Try to load rrg_sectors.json from the output directory.
 * Returns null silently if the file is missing or malformed — the runner
 * continues without RRG injection rather than crashing.
 */
function loadRrgSectors(outputDir: string): RrgSectorsFile | null {
  const p = path.join(outputDir, 'rrg_sectors.json');
  if (!fs.existsSync(p)) return null;
  try {
    return JSON.parse(fs.readFileSync(p, 'utf-8')) as RrgSectorsFile;
  } catch {
    console.warn('[prompt_loader] Could not parse rrg_sectors.json — skipping RRG injection.');
    return null;
  }
}

/**
 * Build the {{RRG_MANDATORY_SECTORS}} replacement text for prompt 02.
 * If no file is found, returns an empty string so the placeholder is simply removed.
 */
function buildRrgSectorsBlock(outputDir: string): string {
  const data = loadRrgSectors(outputDir);
  if (!data || !data.mandatory_sectors || data.mandatory_sectors.length === 0) {
    return '';
  }

  const lines: string[] = [
    '---',
    '## ⚠ Mandatory RRG Sectors — Must Be Included in the Scan',
    '',
    'The following sectors are ranked **Leading or Improving** by the deterministic',
    'RRG (Relative Rotation Graph) model. They must be included in your evaluation',
    'even if your step 01 analysis ranked them lower.',
    '',
    '**Do not skip them.** You may rank their candidates lower if technical setups',
    'are poor, but you must evaluate and list 4 ticker candidates for each.',
    '',
    '| Sector | Quadrant | Direction | RRG Score |',
    '|--------|----------|-----------|-----------|',
  ];

  for (const s of data.mandatory_sectors) {
    lines.push(`| ${s.etf} | ${s.quadrant} | ${s.direction} | ${s.score > 0 ? '+' : ''}${s.score} |`);
  }

  const etfList = data.mandatory_sectors.map((s) => s.etf).join(', ');
  lines.push('');
  lines.push(`**Merge these with your step 01 sector selections.** Final scan sectors = Warren AI selections ∪ { ${etfList} }`);
  lines.push('---');

  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// Theme universe injection
// ---------------------------------------------------------------------------

interface ThemeEntry {
  description: string;
  tickers: Record<string, string>;
}

interface ThemesFile {
  themes: Record<string, ThemeEntry>;
}

/**
 * Try to load themes.yaml (parsed via js-yaml) or themes.json fallback.
 * Returns null silently if the file is missing, malformed, or js-yaml is unavailable.
 */
function loadThemesFile(configDir: string): ThemesFile | null {
  const yamlPath = path.join(configDir, 'themes.yaml');
  if (fs.existsSync(yamlPath)) {
    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const jsYaml = require('js-yaml');
      const raw = fs.readFileSync(yamlPath, 'utf-8');
      return jsYaml.load(raw) as ThemesFile;
    } catch {
      // Fall through to JSON fallback
    }
  }
  const jsonPath = path.join(configDir, 'themes.json');
  if (fs.existsSync(jsonPath)) {
    try {
      return JSON.parse(fs.readFileSync(jsonPath, 'utf-8')) as ThemesFile;
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * Build the {{THEME_UNIVERSE}} replacement block for prompt 02.
 * Returns empty string if no themes are configured (placeholder is simply removed).
 */
function buildThemeUniverseBlock(outputDir: string): string {
  // Config lives at <warren_ai_daily>/config/ — outputDir is <warren_ai_daily>/output/
  const configDir = path.resolve(outputDir, '..', 'config');
  const themes    = loadThemesFile(configDir);

  if (!themes?.themes || Object.keys(themes.themes).length === 0) {
    return '';
  }

  const lines: string[] = [
    '---',
    '## User-Defined Investment Themes — Additional Idea Universe',
    '',
    'These are user-curated thematic ideas. They are **NOT classic sectors**.',
    "Do not relabel any ticker's classic sector (XLK, XLC, etc.) because of theme membership.",
    'A ticker may belong to multiple themes.',
    '',
    'For each theme, evaluate candidate tickers using your standard execution discipline:',
    'liquidity, price above trend, clean pullback or breakout setup, RSI / extension check,',
    'fair value upside if available, and entry quality.',
    '',
    '**Do not force a ticker into final recommendations only because it belongs to a hot theme.**',
    'Theme membership is an idea source, not a buy signal.',
    '',
    'For each theme, return:',
    '- Theme name',
    '- Ticker, classic sector ETF',
    '- Setup (Pullback / Breakout / Consolidation / Extended / Reject)',
    '- Entry, Stop, Target, R:R if setup qualifies',
    '- Pass or Reject with main rejection reason',
    '',
  ];

  for (const [themeName, entry] of Object.entries(themes.themes)) {
    lines.push(`### Theme: ${themeName}`);
    if (entry.description) {
      lines.push(`_${entry.description}_`);
      lines.push('');
    }
    lines.push('| Ticker | Rationale |');
    lines.push('|--------|-----------|');
    for (const [ticker, rationale] of Object.entries(entry.tickers || {})) {
      lines.push(`| ${ticker} | ${rationale} |`);
    }
    lines.push('');
  }
  lines.push('---');
  return lines.join('\n');
}

const PROMPT_FILES: Record<number, string> = {
  1: '01_market_regime.md',
  2: '02_sector_tickers.md',
  3: '03_research_table.md',
  4: '04_execution_plan.md',
  5: '05_final_report.md',
};

const THEME_SCAN_PROMPT_FILE = '02b_theme_scan.md';

// ---------------------------------------------------------------------------
// Prior theme scores injection (for {{PRIOR_THEME_SCORES}})
// ---------------------------------------------------------------------------

interface ThemeScoreEntry {
  score: number;
  label: string;
  direction: string;
}

interface ThemeScoresFile {
  as_of?: string;
  themes?: Record<string, ThemeScoreEntry>;
}

function loadPriorThemeScores(outputDir: string): ThemeScoresFile | null {
  const p = path.join(outputDir, 'theme_scores_latest.json');
  if (!fs.existsSync(p)) return null;
  try {
    return JSON.parse(fs.readFileSync(p, 'utf-8')) as ThemeScoresFile;
  } catch {
    return null;
  }
}

function buildPriorThemeScoresBlock(outputDir: string): string {
  const data = loadPriorThemeScores(outputDir);
  if (!data?.themes || Object.keys(data.themes).length === 0) return '';

  const lines: string[] = [
    '---',
    `## Prior Weekly Theme Scores (as of ${data.as_of ?? 'unknown'})`,
    '',
    'Use these as your baseline for computing Score Change and Direction.',
    '',
    '| Theme | Prior Score | Prior Label |',
    '|-------|-------------|-------------|',
  ];

  for (const [name, entry] of Object.entries(data.themes)) {
    lines.push(`| ${name} | ${entry.score} | ${entry.label} |`);
  }
  lines.push('---');
  return lines.join('\n');
}

/**
 * Load and return the raw text of a prompt file.
 */
export function loadRawPrompt(promptsDir: string, step: number): string {
  const filename = PROMPT_FILES[step];
  if (!filename) throw new Error(`Unknown prompt step: ${step}`);
  const filepath = path.join(promptsDir, filename);
  if (!fs.existsSync(filepath)) {
    throw new Error(`Prompt file not found: ${filepath}`);
  }
  return fs.readFileSync(filepath, 'utf-8');
}

/**
 * Attempt to extract the flat ticker list from step 2 output.
 * Looks for a line that starts with "All 16 Tickers:" or similar.
 * Falls back to the full step 2 output if no list is found.
 */
function extractTickerList(step02Output: string): string {
  const lines = step02Output.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (/all\s+\d+\s+tickers?:/i.test(line) || /^tickers?:/i.test(line)) {
      // The ticker list is on the same line or the next non-empty line
      const sameLinePart = line.replace(/^.*?:\s*/, '').trim();
      if (sameLinePart.length > 0) return sameLinePart;
      for (let j = i + 1; j < lines.length; j++) {
        const next = lines[j].trim();
        if (next.length > 0) return next;
      }
    }
  }
  // No dedicated list found — return full output as fallback
  console.warn(
    '[prompt_loader] Could not find "All N Tickers:" line in step 2 output. ' +
    'Pasting full step 2 output as ticker list.'
  );
  return step02Output;
}

/**
 * Apply all placeholder substitutions to a prompt template.
 */
export function resolvePrompt(
  template: string,
  step: number,
  outputs: StepOutputs,
  date: string,
  time: string,
  outputDir?: string,
): string {
  const combined1to4 = [
    outputs.step01 ? `## Step 1 Output\n\n${outputs.step01}` : '',
    outputs.step02 ? `## Step 2 Output\n\n${outputs.step02}` : '',
    outputs.step03 ? `## Step 3 Output\n\n${outputs.step03}` : '',
    outputs.step04 ? `## Step 4 Output\n\n${outputs.step04}` : '',
  ].filter(Boolean).join('\n\n---\n\n');

  let result = template;

  // Simple token replacements
  result = result.replaceAll('{{DATE}}', date);
  result = result.replaceAll('{{TIME}}', time);
  result = result.replaceAll('{{STEP_01_OUTPUT}}', outputs.step01 || '(not yet available)');
  result = result.replaceAll('{{STEP_02_OUTPUT}}', outputs.step02 || '(not yet available)');
  result = result.replaceAll('{{STEP_03_OUTPUT}}', outputs.step03 || '(not yet available)');
  result = result.replaceAll('{{STEP_04_OUTPUT}}', outputs.step04 || '(not yet available)');

  // RRG mandatory sectors injection (step 02 only)
  if (step === 2 && outputDir) {
    const rrgBlock = buildRrgSectorsBlock(outputDir);
    result = result.replaceAll('{{RRG_MANDATORY_SECTORS}}', rrgBlock);
    if (rrgBlock) {
      console.log('[prompt_loader] ✓ RRG mandatory sectors injected into step 02 prompt.');
    } else {
      console.log('[prompt_loader] ℹ No rrg_sectors.json found — step 02 uses Warren AI selection only.');
    }

    // Theme universe injection (step 02 only)
    const themeBlock = buildThemeUniverseBlock(outputDir);
    result = result.replaceAll('{{THEME_UNIVERSE}}', themeBlock);
    if (themeBlock) {
      console.log('[prompt_loader] ✓ Theme universe injected into step 02 prompt.');
    } else {
      console.log('[prompt_loader] ℹ No themes.yaml found — step 02 skips theme injection.');
    }
  } else {
    result = result.replaceAll('{{RRG_MANDATORY_SECTORS}}', '');
    result = result.replaceAll('{{THEME_UNIVERSE}}', '');
  }

  // {{PRIOR_THEME_SCORES}} — always replaced (only populated in theme scan prompt)
  if (outputDir) {
    const priorBlock = buildPriorThemeScoresBlock(outputDir);
    result = result.replaceAll('{{PRIOR_THEME_SCORES}}', priorBlock);
  } else {
    result = result.replaceAll('{{PRIOR_THEME_SCORES}}', '');
  }

  // Legacy bracket-style placeholders used in the prompt files
  if (outputs.step02) {
    result = result.replaceAll(
      '[PASTE THE 16 TICKERS FROM PROMPT 2]',
      extractTickerList(outputs.step02),
    );
  }
  if (outputs.step03) {
    result = result.replaceAll('[PASTE TABLE FROM PROMPT 3]', outputs.step03);
  }
  result = result.replaceAll(
    '[PASTE OUTPUTS FROM PROMPTS 1 TO 4]',
    combined1to4 || '(prior outputs not yet available)',
  );

  return result;
}

/**
 * Load and fully resolve a prompt for a given step.
 */
export function buildPrompt(
  promptsDir: string,
  step: number,
  outputs: StepOutputs,
  date: string,
  time: string,
  outputDir?: string,
): string {
  const template = loadRawPrompt(promptsDir, step);
  return resolvePrompt(template, step, outputs, date, time, outputDir);
}

/**
 * Build the dedicated Theme Scan prompt (02b_theme_scan.md).
 * Returns null if the prompt file does not exist (theme scan is optional).
 */
export function buildThemeScanPrompt(
  promptsDir: string,
  outputs: StepOutputs,
  date: string,
  time: string,
  outputDir?: string,
): string | null {
  const filepath = path.join(promptsDir, THEME_SCAN_PROMPT_FILE);
  if (!fs.existsSync(filepath)) {
    console.warn(`[prompt_loader] Theme scan prompt not found: ${filepath} — skipping theme scan.`);
    return null;
  }
  const template = fs.readFileSync(filepath, 'utf-8');
  // Reuse resolvePrompt with step=2 so that RRG + THEME_UNIVERSE + PRIOR_THEME_SCORES are all injected.
  // We pass step=2 only to trigger the RRG/theme injection branch; the prompt file itself is different.
  return resolvePrompt(template, 2, outputs, date, time, outputDir);
}
