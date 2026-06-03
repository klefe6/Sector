/**
 * inspect_page.ts — DOM inspection helper for debugging Warren AI selectors.
 *
 * Usage:
 *   npm run inspect-page
 *
 * Requires Chrome to already be running with --remote-debugging-port=9222
 * and Warren AI open in a tab.
 *
 * Writes to output/inspect_latest/:
 *   dom_summary.txt   — visible inputs, buttons, and message containers
 *   page.html         — full page HTML
 *   screenshot.png    — screenshot of the current page state
 */
import { chromium } from 'playwright';
import dotenv from 'dotenv';
import path from 'path';
import fs from 'fs';

dotenv.config();

const CDP_URL    = process.env.CHROME_CDP_URL    || 'http://127.0.0.1:9222';
const OUTPUT_DIR = path.resolve(process.env.OUTPUT_DIR || './output', 'inspect_latest');

async function main(): Promise<void> {
  console.log('');
  console.log('╔══════════════════════════════════════════════════════════════════╗');
  console.log('║  WARREN AI DOM INSPECTOR                                         ║');
  console.log('╚══════════════════════════════════════════════════════════════════╝');
  console.log(`CDP: ${CDP_URL}`);
  console.log(`Output: ${OUTPUT_DIR}`);
  console.log('');

  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  let browser;
  try {
    browser = await chromium.connectOverCDP(CDP_URL);
  } catch (err) {
    console.error(`Cannot connect via CDP at ${CDP_URL}.`);
    console.error(`Run: google-chrome --remote-debugging-port=9222 --remote-allow-origins='*' --user-data-dir="$HOME/.warren-ai-chrome-profile"`);
    console.error(`Then navigate to https://www.investing.com/warrenai`);
    process.exit(1);
  }

  const ctx    = browser.contexts()[0];
  const pages  = ctx.pages();
  const page   = pages.find((p) => p.url().includes('warrenai')) ?? pages[0];

  if (!page) {
    console.error('No open tabs found.');
    process.exit(1);
  }

  console.log(`Inspecting: ${page.url()}`);

  // Screenshot
  const screenshotPath = path.join(OUTPUT_DIR, 'screenshot.png');
  await page.screenshot({ path: screenshotPath, fullPage: false });
  console.log(`Screenshot: ${screenshotPath}`);

  // Full HTML
  const html = await page.content();
  const htmlPath = path.join(OUTPUT_DIR, 'page.html');
  fs.writeFileSync(htmlPath, html, 'utf-8');
  console.log(`HTML: ${htmlPath}`);

  // DOM summary
  const summary = await page.evaluate(() => {
    const lines: string[] = [];

    lines.push('=== PAGE URL ===');
    lines.push(window.location.href);
    lines.push('');

    // --- Textareas ---
    lines.push('=== TEXTAREAS ===');
    document.querySelectorAll('textarea').forEach((el, i) => {
      const rect = el.getBoundingClientRect();
      lines.push(`[${i}] visible=${rect.width > 0 && rect.height > 0} ` +
        `id="${el.id}" class="${el.className.substring(0, 80)}" ` +
        `placeholder="${el.placeholder}" rows=${el.rows}`);
    });
    lines.push('');

    // --- Contenteditable ---
    lines.push('=== CONTENTEDITABLE ELEMENTS ===');
    document.querySelectorAll('[contenteditable]').forEach((el, i) => {
      const rect = el.getBoundingClientRect();
      const h = el as HTMLElement;
      lines.push(`[${i}] visible=${rect.width > 0 && rect.height > 0} ` +
        `tag=${el.tagName.toLowerCase()} ` +
        `role="${el.getAttribute('role') ?? ''}" ` +
        `placeholder="${el.getAttribute('placeholder') ?? ''}" ` +
        `class="${h.className.substring(0, 80)}"`);
    });
    lines.push('');

    // --- Role textbox ---
    lines.push('=== ROLE=TEXTBOX ELEMENTS ===');
    document.querySelectorAll('[role="textbox"]').forEach((el, i) => {
      const rect = el.getBoundingClientRect();
      const h = el as HTMLElement;
      lines.push(`[${i}] visible=${rect.width > 0 && rect.height > 0} ` +
        `tag=${el.tagName.toLowerCase()} class="${h.className.substring(0, 80)}"`);
    });
    lines.push('');

    // --- Buttons (visible) ---
    lines.push('=== VISIBLE BUTTONS ===');
    document.querySelectorAll('button').forEach((el, i) => {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return;
      lines.push(`[${i}] type="${el.type}" ` +
        `aria-label="${el.getAttribute('aria-label') ?? ''}" ` +
        `title="${el.title}" ` +
        `disabled=${el.disabled} ` +
        `text="${el.innerText.substring(0, 60).replace(/\n/g, ' ')}" ` +
        `class="${el.className.substring(0, 80)}"`);
    });
    lines.push('');

    // --- Likely message/response containers ---
    lines.push('=== MESSAGE / RESPONSE CONTAINERS ===');
    const msgSelectors = [
      '[data-testid*="message"]',
      '[data-testid*="response"]',
      '[data-role]',
      '[data-message-author-role]',
      '[class*="message"]',
      '[class*="response"]',
      '[class*="assistant"]',
      '[class*="chat"]',
      'article',
    ];
    const seen = new Set<Element>();
    msgSelectors.forEach((sel) => {
      try {
        document.querySelectorAll(sel).forEach((el, i) => {
          if (seen.has(el)) return;
          seen.add(el);
          const rect = el.getBoundingClientRect();
          const h = el as HTMLElement;
          lines.push(`sel="${sel}" [${i}] tag=${el.tagName.toLowerCase()} ` +
            `visible=${rect.width > 0 && rect.height > 0} ` +
            `data-testid="${el.getAttribute('data-testid') ?? ''}" ` +
            `data-role="${el.getAttribute('data-role') ?? ''}" ` +
            `data-message-author-role="${el.getAttribute('data-message-author-role') ?? ''}" ` +
            `class="${h.className.substring(0, 80)}" ` +
            `text="${h.innerText.substring(0, 60).replace(/\n/g, ' ')}"`);
        });
      } catch { /* skip */ }
    });
    lines.push('');

    // --- Input[type=text] ---
    lines.push('=== INPUT[TYPE=TEXT] ELEMENTS ===');
    document.querySelectorAll('input[type="text"], input:not([type])').forEach((el, i) => {
      const rect = el.getBoundingClientRect();
      const e = el as HTMLInputElement;
      lines.push(`[${i}] visible=${rect.width > 0 && rect.height > 0} ` +
        `id="${e.id}" placeholder="${e.placeholder}" class="${e.className.substring(0, 80)}"`);
    });

    return lines.join('\n');
  });

  const summaryPath = path.join(OUTPUT_DIR, 'dom_summary.txt');
  fs.writeFileSync(summaryPath, summary, 'utf-8');
  console.log(`DOM summary: ${summaryPath}`);
  console.log('');
  console.log('── DOM Summary Preview ──────────────────────────────────────────');
  console.log(summary.substring(0, 3000));
  console.log('────────────────────────────────────────────────────────────────');
  console.log('');
  console.log(`Full summary at: ${summaryPath}`);

  await browser.close();
}

main().catch((err) => {
  console.error('[inspect] Fatal:', err);
  process.exit(1);
});
