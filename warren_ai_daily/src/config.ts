/**
 * config.ts — Load and validate all configuration from environment variables.
 * Copy .env.example to .env and fill in your values before running.
 */
import path from 'path';
import dotenv from 'dotenv';

dotenv.config();

export type BrowserMode = 'persistent' | 'cdp';

export interface Config {
  /** Full URL to the Warren AI chat page */
  warrenUrl: string;
  /** If true, print prompts but do not submit them */
  dryRun: boolean;
  /** Max ms to wait for a single AI response */
  maxWaitMs: number;
  /** Persistent browser profile directory (holds login cookies) */
  browserProfileDir: string;
  /** Directory containing the 5 prompt markdown files */
  promptsDir: string;
  /** Root output directory; daily outputs go in output/YYYY-MM-DD/ */
  outputDir: string;
  /** How long (ms) to wait at startup for the user to log in manually */
  loginCheckTimeoutMs: number;
  /** Run browser without a visible window */
  headless: boolean;
  /** How often (ms) to sample the response text to detect completion */
  responseStabilizePollMs: number;
  /** How many ms of text stability before declaring response done */
  stableResponseMs: number;
  /**
   * 'persistent' — launch Chromium with persistent profile (default)
   * 'cdp'        — connect to an already-running Chrome via CDP
   */
  browserMode: BrowserMode;
  /** CDP endpoint URL when browserMode = 'cdp' */
  chromeCdpUrl: string;
  /**
   * If true, skip prompts whose step_NN.md already exists for today.
   * Useful when a mid-run failure requires restarting from a later step.
   */
  resume: boolean;
  /**
   * If true, leave the browser open after completion.
   * Useful for inspecting the final state or debugging.
   */
  keepBrowserOpen: boolean;
  /** Minimum response length below which the step is flagged as suspicious */
  minResponseChars: number;
}

function requireEnv(name: string): string {
  const v = process.env[name];
  if (!v) {
    throw new Error(
      `Missing required environment variable: ${name}\n` +
      `Copy .env.example to .env and set ${name}.`
    );
  }
  return v;
}

function optionalInt(name: string, fallback: number): number {
  const v = process.env[name];
  if (!v) return fallback;
  const n = parseInt(v, 10);
  if (isNaN(n)) throw new Error(`${name} must be an integer, got: ${v}`);
  return n;
}

export function loadConfig(): Config {
  const mode = (process.env.WARREN_BROWSER_MODE || 'persistent').toLowerCase();
  if (mode !== 'persistent' && mode !== 'cdp') {
    throw new Error(`WARREN_BROWSER_MODE must be 'persistent' or 'cdp', got: ${mode}`);
  }

  return {
    warrenUrl:               requireEnv('WARREN_AI_URL'),
    dryRun:                  process.env.DRY_RUN === 'true',
    maxWaitMs:               optionalInt('MAX_WAIT_MS', 180_000),
    browserProfileDir:       path.resolve(process.env.BROWSER_PROFILE_DIR || './browser_profile'),
    promptsDir:              path.resolve(process.env.PROMPTS_DIR || './prompts'),
    outputDir:               path.resolve(process.env.OUTPUT_DIR || './output'),
    loginCheckTimeoutMs:     optionalInt('LOGIN_CHECK_TIMEOUT_MS', 120_000),
    headless:                process.env.HEADLESS === 'true',
    responseStabilizePollMs: optionalInt('POLL_MS', 1_000),
    stableResponseMs:        optionalInt('STABLE_RESPONSE_MS', 8_000),
    browserMode:             mode as BrowserMode,
    chromeCdpUrl:            process.env.CHROME_CDP_URL || 'http://127.0.0.1:9222',
    resume:                  process.env.RESUME === 'true',
    keepBrowserOpen:         process.env.KEEP_BROWSER_OPEN === 'true',
    minResponseChars:        optionalInt('MIN_RESPONSE_CHARS', 500),
  };
}
