/**
 * warren_client.ts — Playwright automation for Warren AI chat interface.
 *
 * Two browser modes:
 *   persistent — Playwright launches its own Chromium (blocked by Cloudflare on investing.com)
 *   cdp        — Attach to an already-running Chrome the user launched manually.
 *                Since investing.com uses Cloudflare, CDP mode is REQUIRED.
 *                The user must:
 *                  1. Launch Chrome with --remote-debugging-port=9222
 *                  2. Navigate to https://www.investing.com/warrenai
 *                  3. Log in if needed
 *                  4. Run: npm run run-daily:cdp
 *
 * Design principles:
 *   - Multiple selector candidates tried in order; first visible match wins.
 *   - Every selector attempt is logged.
 *   - Response completion: text-stability window (STABLE_RESPONSE_MS) + stop-button.
 *   - Never bypasses bot detection — reports clearly.
 *   - Screenshot + HTML saved on any error.
 */
import { Browser, BrowserContext, Page, chromium } from 'playwright';
import * as readline from 'readline';
import path from 'path';
import fs from 'fs';
import { Config } from './config';
import {
  CHROME_CDP_LAUNCH_COMMAND,
  describeCdpPortProbe,
  parseCdpTcpTarget,
  probeTcpPort,
} from './cdp_instructions';

// ---------------------------------------------------------------------------
// Selector candidates
// Ordered from most-specific to most-generic. First visible match wins.
// Update these based on actual investing.com/warrenai DOM inspection.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// CONFIRMED selectors — live DOM inspection of investing.com/warrenai
// Chrome 146, inspected 2026-05-04 via CDP.
// Warren AI uses data-test attributes (not data-testid / aria-label).
// ---------------------------------------------------------------------------
export const SELECTORS = {
  // CONFIRMED: textarea with placeholder "Ask WarrenAI", data-test="chat-input-field"
  chatInput: [
    '[data-test="chat-input-field"]',
    'textarea[placeholder*="WarrenAI" i]',
    'textarea[placeholder*="Warren" i]',
    'textarea[placeholder*="Ask" i]',
    'textarea[rows]',
    'div[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"]',
    '[role="textbox"]',
    'textarea',
  ],

  // CONFIRMED: button[data-test="send-message-button"], disabled when input empty
  sendButton: [
    '[data-test="send-message-button"]',
    'button[aria-label*="send" i]',
    'button[title*="send" i]',
    'button[class*="rounded-full"]',
    'button[type="submit"]',
  ],

  // CONFIRMED: [data-test="answer-buttons-block"] appears when generation is complete
  // Use as primary completion signal (copy/like/dislike/regenerate row)
  generationDoneIndicator: [
    '[data-test="answer-buttons-block"]',
    '[data-test="regenerate-button"]',
    '[data-test="copy-button"]',
  ],

  stopButton: [
    'button[aria-label*="stop" i]',
    'button[aria-label*="cancel" i]',
    'button:has-text("Stop")',
    'button:has-text("Stop generating")',
  ],

  generatingIndicator: [
    '[aria-label*="generating" i]',
    '[class*="loading"]',
    '[class*="spinner"]',
    '[class*="thinking"]',
    '[class*="dots"]',
  ],

  // CONFIRMED: AI responses live in [data-test="chat-answers"] divs
  assistantMessage: [
    '[data-test="chat-answers"]',
    '[data-test="assistant-message"]',
    '[data-role="assistant"]',
    'article',
    '[role="article"]',
  ],

  loginRequiredIndicators: [
    '#email',
    '#loginPassword',
    'input[type="email"]',
    'input[type="password"]',
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
    'form[action*="login"]',
  ],

  cloudflareChallenge: [
    '#cf-challenge-running',
    '.cf-browser-verification',
    'iframe[src*="challenges.cloudflare.com"]',
    '#challenge-form',
    'div[id*="turnstile"]',
    'iframe[title*="Cloudflare"]',
  ],

  botBlockIndicators: [
    'iframe[src*="captcha"]',
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    '[class*="captcha"]',
    '[id*="captcha"]',
  ],
} as const;

// ---------------------------------------------------------------------------
// WarrenClient
// ---------------------------------------------------------------------------

export class WarrenClient {
  private browser: Browser | null = null;
  private context: BrowserContext | null = null;
  private page: Page | null = null;

  constructor(private readonly config: Config) {}

  // ── Browser lifecycle ──────────────────────────────────────────────────

  async launch(): Promise<void> {
    if (this.config.browserMode === 'cdp') {
      await this._launchCdp();
    } else {
      await this._launchPersistent();
    }
  }

  private async _launchPersistent(): Promise<void> {
    fs.mkdirSync(this.config.browserProfileDir, { recursive: true });
    console.log(`[browser] Mode: persistent — profile at ${this.config.browserProfileDir}`);
    console.warn(
      '[browser] WARNING: investing.com uses Cloudflare protection which blocks\n' +
      '  Playwright\'s automated Chromium. If you see a "Performing security\n' +
      '  verification" page, switch to CDP mode. See README for instructions.'
    );
    this.context = await chromium.launchPersistentContext(
      this.config.browserProfileDir,
      {
        headless: this.config.headless,
        viewport: { width: 1440, height: 900 },
        userAgent:
          'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ' +
          '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        args: ['--no-sandbox'],
      }
    );
  }

  private async _launchCdp(): Promise<void> {
    console.log(`[browser] Mode: CDP — connecting to ${this.config.chromeCdpUrl}`);
    try {
      this.browser = await chromium.connectOverCDP(this.config.chromeCdpUrl, { timeout: 60_000 });
    } catch (err) {
      const tcp = parseCdpTcpTarget(this.config.chromeCdpUrl);
      let portProbe: 'open' | 'closed' | 'unknown' = 'unknown';
      if (tcp) portProbe = await probeTcpPort(tcp.host, tcp.port);
      const diag = describeCdpPortProbe(this.config.chromeCdpUrl, portProbe, tcp);
      throw new Error(
        `\n${'═'.repeat(65)}\n` +
        `CANNOT CONNECT TO CHROME VIA CDP\n` +
        `\n` +
        `Expected Chrome with remote debugging at: ${this.config.chromeCdpUrl}\n` +
        `${diag}\n` +
        `\n` +
        `Open the dedicated Warren AI Chrome window first, log in, pass security checks,\n` +
        `then re-run your npm script.\n` +
        `\n` +
        `Launch Chrome (copy/paste as one line):\n` +
        `\n` +
        `  ${CHROME_CDP_LAUNCH_COMMAND}\n` +
        `\n` +
        `Then navigate to: https://www.investing.com/warrenai\n` +
        `${'═'.repeat(65)}\n` +
        `Original error: ${err}`
      );
    }
    const contexts = this.browser.contexts();
    this.context = contexts[0] ?? await this.browser.newContext();
  }

  /**
   * In CDP mode: find the tab already showing Warren AI, or use the first tab.
   * In persistent mode: create a new page.
   */
  async openPage(): Promise<void> {
    if (!this.context) throw new Error('[browser] Context not launched');

    if (this.config.browserMode === 'cdp') {
      const pages = this.context.pages();
      if (pages.length === 0) {
        this.page = await this.context.newPage();
        console.log('[browser] CDP: no open tabs found — created a new one');
        return;
      }
      // Prefer a tab already on investing.com/warrenai
      const warrenTab = pages.find((p) => p.url().includes('warrenai'));
      if (warrenTab) {
        this.page = warrenTab;
        console.log(`[browser] CDP: reusing existing Warren AI tab (${this.page.url()})`);
      } else {
        this.page = pages[0];
        console.log(`[browser] CDP: no Warren AI tab found — using first tab (${this.page.url()})`);
      }
    } else {
      this.page = await this.context.newPage();
    }
  }

  async close(): Promise<void> {
    if (this.config.keepBrowserOpen) {
      console.log('[browser] Run complete. Browser left open because KEEP_BROWSER_OPEN=true.');
      return;
    }
    if (this.config.browserMode === 'cdp') {
      await this.browser?.close();
    } else {
      await this.context?.close();
    }
  }

  // ── Login readiness detection ──────────────────────────────────────────

  /**
   * Ensure the Warren AI chat page is loaded and the input is accessible.
   *
   * In CDP mode: if the tab is already on the right URL with chat visible, proceed.
   * If not on the right URL, navigate there.
   * If chat input is missing, ask the user to log in and press Enter.
   */
  async ensureLoggedIn(): Promise<boolean> {
    if (!this.page) throw new Error('[browser] Page not open');

    const currentUrl = this.page.url();
    const alreadyOnPage = currentUrl.includes('investing.com/warrenai') ||
                          currentUrl.includes('warrenai');

    // Check for Cloudflare challenge first
    if (await this._anyVisible(SELECTORS.cloudflareChallenge)) {
      console.error(
        '\n' + '═'.repeat(65) + '\n' +
        'CLOUDFLARE CHALLENGE DETECTED\n' +
        '\n' +
        'investing.com is blocking this browser with a bot challenge.\n' +
        'This happens with Playwright\'s automated Chromium.\n' +
        '\n' +
        'SOLUTION — Use CDP mode with your real Chrome:\n' +
        '\n' +
        '  1. Open a terminal and run:\n' +
        '     google-chrome --remote-debugging-port=9222 \\\n' +
        "       --remote-allow-origins='*' \\\n" +
        '       --user-data-dir="$HOME/.warren-ai-chrome-profile"\n' +
        '\n' +
        '  2. In that Chrome, go to: https://www.investing.com/warrenai\n' +
        '     Log in if prompted.\n' +
        '\n' +
        '  3. Keep Chrome open and run:\n' +
        '     npm run run-daily:cdp\n' +
        '\n' +
        '  Current WARREN_BROWSER_MODE=' + this.config.browserMode + '\n' +
        '═'.repeat(65)
      );
      return false;
    }

    if (alreadyOnPage) {
      console.log(`[browser] Already on Warren AI page (${currentUrl}) — skipping navigation`);
      await this.page.waitForTimeout(2000);
    } else {
      console.log(`[browser] Navigating to ${this.config.warrenUrl}`);
      await this.page.goto(this.config.warrenUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 });
      await this.page.waitForTimeout(3000);
    }

    // Re-check for Cloudflare after navigation
    if (await this._anyVisible(SELECTORS.cloudflareChallenge)) {
      console.error('[browser] Cloudflare challenge appeared after navigation. See solution above.');
      return false;
    }

    // Already logged in with chat visible?
    if (await this._findSelector(SELECTORS.chatInput, false)) {
      console.log('[browser] Chat input visible ✓');
      return true;
    }

    // Login form visible?
    const loginVisible = await this._anyVisible(SELECTORS.loginRequiredIndicators);

    // Either way — ask user to log in and press Enter
    console.log('\n' + '─'.repeat(60));
    if (loginVisible) {
      console.log('[browser] LOGIN REQUIRED — login form is visible');
    } else {
      console.log('[browser] Chat input not found — page may need login or is still loading');
    }
    console.log(`URL: ${this.page.url()}`);
    console.log('');
    console.log('Please complete login (or wait for the page to finish loading).');
    console.log('When the Warren AI chat input is visible, press Enter here.');
    console.log('─'.repeat(60));

    await this._waitForEnter();
    await this.page.waitForTimeout(2000);

    if (await this._findSelector(SELECTORS.chatInput, false)) {
      console.log('[browser] Chat input visible after login ✓');
      return true;
    }

    console.error('[browser] Chat input still not found after login attempt.');
    return false;
  }

  /** Check chat input is present without navigating. */
  async isChatReady(): Promise<boolean> {
    return !!(await this._findSelector(SELECTORS.chatInput, false));
  }

  /** Reload and wait for chat to reappear. */
  async reloadPage(): Promise<boolean> {
    if (!this.page) return false;
    // In CDP mode, don't reload the user's page — ask them instead
    if (this.config.browserMode === 'cdp') {
      console.log('[browser] CDP mode — not reloading. Checking for chat input…');
      await this.page.waitForTimeout(2000);
      return !!(await this._findSelector(SELECTORS.chatInput, false));
    }
    console.log('[browser] Reloading page…');
    await this.page.reload({ waitUntil: 'domcontentloaded' });
    await this.page.waitForTimeout(3000);
    const ready = !!(await this._findSelector(SELECTORS.chatInput, false));
    if (ready) console.log('[browser] Chat ready after reload ✓');
    else console.warn('[browser] Chat input still missing after reload');
    return ready;
  }

  // ── Chat interaction ────────────────────────────────────────────────────

  /**
   * Fill the chat input, submit, wait for the new response to stabilise,
   * and return the final response text.
   */
  async submitPromptAndWait(promptText: string, stepName: string): Promise<string> {
    if (!this.page) throw new Error('[browser] Page not open');

    if (await this._anyVisible(SELECTORS.cloudflareChallenge)) {
      throw new Error('Cloudflare challenge appeared mid-run. Run has been interrupted.');
    }

    // Wait for the textarea to be enabled before attempting to fill.
    // Warren AI disables the input while it is generating a previous response.
    await this._waitForInputEnabled(stepName);

    const inputSel = await this._findSelector(SELECTORS.chatInput);
    if (!inputSel) {
      throw new Error('Could not find chat input. Tried:\n  ' + SELECTORS.chatInput.join('\n  '));
    }

    const inputEl = this.page.locator(inputSel).last();

    // Snapshot the current response count/text before submitting
    const prevResponseText = await this._getLatestResponseText();

    console.log(`[chat:${stepName}] Filling prompt (${promptText.length} chars)…`);
    await inputEl.click({ timeout: 5000 });
    await this.page.waitForTimeout(400);

    // Determine element type to choose fill strategy
    const tagName: string = await inputEl.evaluate((el) => el.tagName.toLowerCase());
    const isContentEditable: boolean = await inputEl.evaluate(
      (el) => (el as HTMLElement).isContentEditable
    );

    if (tagName === 'textarea' || tagName === 'input') {
      // Standard: clear + fill
      await inputEl.fill('');
      await this.page.waitForTimeout(100);
      await inputEl.fill(promptText);
      console.log(`[chat:${stepName}] Used textarea fill (selector: ${inputSel})`);
    } else if (isContentEditable) {
      // contenteditable: select all + type via clipboard injection
      await this.page.keyboard.press('Control+a');
      await this.page.waitForTimeout(100);
      await inputEl.evaluate((el: Element, text: string) => {
        (el as HTMLElement).focus();
        document.execCommand('selectAll');
        document.execCommand('insertText', false, text);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }, promptText);
      console.log(`[chat:${stepName}] Used contenteditable inject (selector: ${inputSel})`);
    } else {
      // Fallback: try fill anyway
      await inputEl.fill(promptText).catch(async () => {
        await inputEl.type(promptText, { delay: 10 });
      });
      console.log(`[chat:${stepName}] Used fallback fill (selector: ${inputSel})`);
    }

    await this.page.waitForTimeout(600);

    // Submit strategy:
    //   1. Click send button if found and enabled
    //   2. Try Enter key (works for single-line inputs and some chat UIs)
    //   3. Try Control+Enter (common for multiline textarea chat UIs)
    //   4. Try Meta+Enter (macOS equivalent)
    const sent = await this._clickSendButton(stepName);
    if (!sent) {
      // Try Enter first, then wait briefly to see if it submitted
      console.log(`[chat:${stepName}] No send button — trying keyboard submit`);
      await this.page.keyboard.press('Enter');
      await this.page.waitForTimeout(800);
      // If a new response hasn't started, try Control+Enter
      const afterEnter = await this._getLatestResponseText();
      if (afterEnter === prevResponseText) {
        console.log(`[chat:${stepName}] Enter did not submit — trying Control+Enter`);
        await this.page.keyboard.press('Control+Enter');
        await this.page.waitForTimeout(800);
      }
    }

    console.log(`[chat:${stepName}] Submitted. Waiting for response…`);
    return await this._waitForNewResponse(prevResponseText, stepName);
  }

  // ── Error capture ───────────────────────────────────────────────────────

  async screenshotOnError(outputDir: string, filename = 'error.png'): Promise<string> {
    if (!this.page) return '';
    const filepath = path.join(outputDir, filename);
    try {
      fs.mkdirSync(outputDir, { recursive: true });
      await this.page.screenshot({ path: filepath, fullPage: true });
      console.log(`[error] Screenshot: ${filepath}`);
    } catch (e) {
      console.error(`[error] Screenshot failed: ${e}`);
    }
    return filepath;
  }

  async htmlSnapshotOnError(outputDir: string, filename = 'error.html'): Promise<string> {
    if (!this.page) return '';
    const filepath = path.join(outputDir, filename);
    try {
      fs.mkdirSync(outputDir, { recursive: true });
      const html = await this.page.content();
      fs.writeFileSync(filepath, html, 'utf-8');
      console.log(`[error] HTML snapshot: ${filepath}`);
    } catch (e) {
      console.error(`[error] HTML snapshot failed: ${e}`);
    }
    return filepath;
  }

  async screenshot(filepath: string): Promise<void> {
    if (!this.page) return;
    try {
      fs.mkdirSync(path.dirname(filepath), { recursive: true });
      await this.page.screenshot({ path: filepath, fullPage: false });
    } catch { /* best effort */ }
  }

  async getPageUrl(): Promise<string> {
    return this.page?.url() ?? '';
  }

  // ── DOM inspection helpers (used by inspect_page.ts) ──────────────────

  async getPage(): Promise<Page | null> {
    return this.page;
  }

  // ── Private helpers ─────────────────────────────────────────────────────

  /**
   * Wait for Warren AI to finish generating a response.
   *
   * Warren AI flow:
   *   1. After submit, briefly shows a loading placeholder:
   *      "Assessing the Metrics, Technical Analysis… verdict incoming!"
   *   2. Streams the actual response (can take 60-180s for full analysis)
   *   3. When done: [data-test="answer-buttons-block"] appears (copy/like/regen row)
   *      AND the input textarea re-enables
   *
   * Strategy:
   *   - Count [data-test="answer-buttons-block"] BEFORE submitting → prevDoneCount
   *   - PRIMARY done signal: count increases to prevDoneCount+1
   *   - FALLBACK: text stable for STABLE_RESPONSE_MS AND length > 500 chars
   *     AND not matching a known loading placeholder
   *   - Never declare done on loading placeholder text alone
   */
  private async _waitForNewResponse(prevText: string, stepName: string): Promise<string> {
    if (!this.page) throw new Error('[browser] Page not open');

    const { maxWaitMs, responseStabilizePollMs: pollMs, stableResponseMs } = this.config;
    const startTime = Date.now();

    // Count existing "done" indicator blocks before this submission
    const prevDoneCount = await this._countElements('[data-test="answer-buttons-block"]');
    const prevAnswerCount = await this._countElements('[data-test="chat-answers"]');
    console.log(`[chat:${stepName}] Pre-submit: ${prevAnswerCount} answers, ${prevDoneCount} done-blocks`);

    // Wait for submission to register in the DOM
    await this.page.waitForTimeout(3000);

    let lastText     = '';
    let lastChangeAt = Date.now();
    let phase: 'waiting_for_start' | 'receiving' = 'waiting_for_start';

    while (Date.now() - startTime < maxWaitMs) {
      const currentText    = await this._getLatestResponseText();
      const currentDone    = await this._countElements('[data-test="answer-buttons-block"]');
      const currentAnswers = await this._countElements('[data-test="chat-answers"]');
      const doneAppeared   = currentDone > prevDoneCount;
      const isLoading      = this._isLoadingPlaceholder(currentText);
      const isNew          = currentText.length > 0 &&
                             currentText !== prevText &&
                             !isLoading;
      const hasChanged     = currentText !== lastText;

      if (hasChanged) {
        lastText     = currentText;
        lastChangeAt = Date.now();
      }

      const msSinceChange = Date.now() - lastChangeAt;
      const isStable      = msSinceChange >= stableResponseMs;

      if (phase === 'waiting_for_start' && isNew) phase = 'receiving';

      const elapsed = ((Date.now() - startTime) / 1000).toFixed(0);
      process.stdout.write(
        `\r[chat:${stepName}] ${phase}${isLoading?' [loading]':''} — ${currentText.length}c` +
        ` — ${elapsed}s — answers:${prevAnswerCount}→${currentAnswers}` +
        ` done:${prevDoneCount}→${currentDone}`
      );

      // PRIMARY: Warren AI's done-block appeared for this new response
      if (doneAppeared && currentAnswers > prevAnswerCount) {
        process.stdout.write('\n');
        await this.page.waitForTimeout(800); // let last tokens render
        const finalText = await this._getLatestResponseText();
        console.log(`[chat:${stepName}] ✓ Complete via done-block (${finalText.length} chars)`);
        return finalText;
      }

      // FALLBACK: only if text is substantial AND stable AND not a loading placeholder
      if (isNew && isStable && currentText.length >= this.config.minResponseChars) {
        process.stdout.write('\n');
        console.log(`[chat:${stepName}] ✓ Complete via stability (${currentText.length} chars)`);
        return currentText;
      }

      await this.page.waitForTimeout(pollMs);
    }

    process.stdout.write('\n');

    const finalText = await this._getLatestResponseText();
    if (finalText.length > 0 && finalText !== prevText && !this._isLoadingPlaceholder(finalText)) {
      console.warn(`[chat:${stepName}] ⚠ MAX_WAIT_MS reached — partial (${finalText.length} chars)`);
      return finalText;
    }

    throw new Error(`Response timed out after ${maxWaitMs}ms for "${stepName}"`);
  }

  /**
   * Detect Warren AI's loading placeholder text.
   * This text appears while Warren AI is fetching real-time data.
   * It is NOT the actual response — ignore it in done detection.
   */
  private _isLoadingPlaceholder(text: string): boolean {
    const loading = [
      'Assessing the Metrics',
      'verdict incoming',
      'Technical Analysis, News, Analysis',
      'Loading',
      'Generating',
    ];
    return loading.some((phrase) => text.includes(phrase));
  }

  private async _findSelector(
    candidates: readonly string[],
    required = true,
  ): Promise<string | null> {
    if (!this.page) throw new Error('[browser] Page not open');

    for (const sel of candidates) {
      try {
        const visible = await this.page.locator(sel).last()
          .isVisible({ timeout: 1200 }).catch(() => false);
        if (visible) {
          console.log(`[selector] ✓ ${sel}`);
          return sel;
        }
      } catch { /* try next */ }
    }

    if (required) {
      console.error(`[selector] ✗ None matched:\n  ${candidates.join('\n  ')}`);
    }
    return null;
  }

  private async _anyVisible(candidates: readonly string[]): Promise<boolean> {
    if (!this.page) return false;
    for (const sel of candidates) {
      try {
        const visible = await this.page.locator(sel).first()
          .isVisible({ timeout: 400 }).catch(() => false);
        if (visible) return true;
      } catch { /* continue */ }
    }
    return false;
  }

  private async _getLatestResponseText(): Promise<string> {
    if (!this.page) return '';

    // PRIMARY: [data-test="chat-answers"] — CONFIRMED Warren AI response container
    try {
      const els = this.page.locator('[data-test="chat-answers"]');
      const count = await els.count().catch(() => 0);
      if (count > 0) {
        const last = els.nth(count - 1);
        const visible = await last.isVisible({ timeout: 400 }).catch(() => false);
        if (visible) {
          const text = (await last.innerText({ timeout: 2000 })).trim();
          if (text.length > 0) return text;
        }
      }
    } catch { /* fall through */ }

    // FALLBACK: try other assistant message selectors
    for (const sel of SELECTORS.assistantMessage.slice(1)) {
      try {
        const el      = this.page.locator(sel).last();
        const visible = await el.isVisible({ timeout: 400 }).catch(() => false);
        if (visible) {
          const text = (await el.innerText({ timeout: 1000 })).trim();
          if (text.length > 0) return text;
        }
      } catch { /* try next */ }
    }

    // LAST RESORT: find the last substantial visible text block that isn't user input
    try {
      const blocks = await this.page.evaluate(() => {
        const skipTags = new Set(['TEXTAREA', 'INPUT', 'SCRIPT', 'STYLE', 'NOSCRIPT']);
        const els = Array.from(document.querySelectorAll(
          '[data-test="chat-answers"], article, [data-role="assistant"], ' +
          '[class*="answer"], [class*="response"], [class*="assistant"]'
        ));
        return els
          .filter((el) => {
            if (skipTags.has(el.tagName)) return false;
            const rect = el.getBoundingClientRect();
            const text = el.textContent?.trim() ?? '';
            return rect.width > 0 && rect.height > 0 && text.length > 80;
          })
          .map((el) => el.textContent?.trim() ?? '');
      });
      if (blocks.length > 0) return blocks[blocks.length - 1];
    } catch { /* ignore */ }

    return '';
  }

  /** Count how many elements match a selector (for tracking new responses). */
  private async _countElements(sel: string): Promise<number> {
    if (!this.page) return 0;
    try {
      return await this.page.locator(sel).count();
    } catch {
      return 0;
    }
  }

  /**
   * Wait until the chat input textarea is enabled (not disabled).
   * Warren AI disables the input while generating a previous response.
   * Polls every 2s, up to MAX_WAIT_MS.
   */
  private async _waitForInputEnabled(stepName: string): Promise<void> {
    if (!this.page) return;
    const deadline = Date.now() + this.config.maxWaitMs;
    let attempt = 0;
    while (Date.now() < deadline) {
      const sel = '[data-test="chat-input-field"]';
      try {
        const disabled = await this.page.locator(sel).last()
          .isDisabled({ timeout: 1000 }).catch(() => false);
        if (!disabled) {
          if (attempt > 0) console.log(`\n[chat:${stepName}] Input enabled ✓`);
          return;
        }
      } catch { /* continue */ }
      attempt++;
      if (attempt % 5 === 0) {
        const secs = Math.round((Date.now() - (deadline - this.config.maxWaitMs)) / 1000);
        process.stdout.write(`\r[chat:${stepName}] Waiting for input to enable… ${secs}s`);
      }
      await this.page.waitForTimeout(2000);
    }
    process.stdout.write('\n');
    console.warn(`[chat:${stepName}] Input still disabled after wait — proceeding anyway`);
  }

  private async _clickSendButton(stepName: string): Promise<boolean> {
    if (!this.page) return false;
    const sel = await this._findSelector(SELECTORS.sendButton, false);
    if (!sel) return false;
    try {
      const btn = this.page.locator(sel).last();
      // Warren AI: send button starts disabled (empty input), becomes enabled after fill.
      // Wait up to 3s for it to become enabled before deciding to fall back to Enter.
      const disabled = await btn.isDisabled({ timeout: 1000 }).catch(() => false);
      if (disabled) {
        console.log(`[chat:${stepName}] Send button disabled — waiting up to 3s for it to enable…`);
        try {
          // Poll for enabled state (Playwright's waitFor only accepts visibility states)
          let isStillDisabled = true;
          const deadline = Date.now() + 3000;
          while (Date.now() < deadline) {
            isStillDisabled = await btn.isDisabled({ timeout: 300 }).catch(() => false);
            if (!isStillDisabled) break;
            await this.page!.waitForTimeout(300);
          }
          if (isStillDisabled) throw new Error('still disabled');
        } catch {
          console.log(`[chat:${stepName}] Send button still disabled after wait — using Enter key`);
          return false;
        }
      }
      await btn.click({ timeout: 5000 });
      console.log(`[chat:${stepName}] ✓ Clicked send: ${sel}`);
      return true;
    } catch (e) {
      console.log(`[chat:${stepName}] Send click failed (${e}) — falling back to Enter`);
      return false;
    }
  }

  private _waitForEnter(): Promise<void> {
    return new Promise((resolve) => {
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
      rl.question('', () => { rl.close(); resolve(); });
    });
  }
}
