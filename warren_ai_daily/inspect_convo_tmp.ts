import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const CDP_URL = 'http://127.0.0.1:9222';
const OUT = '/home/klefe6/Coding Projects/rrg/warren_ai_daily/output/inspect_latest';

async function main() {
  const browser = await chromium.connectOverCDP(CDP_URL);
  const ctx = browser.contexts()[0];
  const pages = ctx.pages();
  const page = pages.find((p: any) => p.url().includes('warrenai')) ?? pages[0];
  
  console.log('Current URL:', page.url());
  
  // Click the first sidebar conversation (the "Daily Research" button)
  const convoBtn = page.locator('button').filter({ hasText: /Daily Research/ }).first();
  const text = await convoBtn.innerText().catch(() => '');
  console.log('Clicking conversation:', text.substring(0, 60));
  await convoBtn.click();
  await page.waitForTimeout(4000);
  
  console.log('URL after click:', page.url());
  
  // Screenshot after navigation
  await page.screenshot({ path: path.join(OUT, 'convo_screenshot.png'), fullPage: false });
  console.log('Screenshot taken: convo_screenshot.png');
  
  // Dump the response container structure
  const summary = await page.evaluate(() => {
    const lines: string[] = [];
    
    // data-test attributes present after navigation
    const withDataTest = Array.from(document.querySelectorAll('[data-test]'));
    lines.push('=== ALL data-test VALUES ===');
    const dtValues = [...new Set(withDataTest.map((el: any) => el.getAttribute('data-test') ?? ''))];
    dtValues.forEach(v => lines.push(`  data-test="${v}"`));
    lines.push('');
    
    // Look for chat / message containers
    lines.push('=== CHAT-SECTION CHILDREN ===');
    const chatSection = document.querySelector('[data-test="chat-section"]');
    if (chatSection) {
      const children = Array.from(chatSection.children);
      children.forEach((child: any, i) => {
        lines.push(`  child[${i}] tag=${child.tagName} class="${child.className?.substring(0, 120)}" data-test="${child.getAttribute('data-test') ?? ''}"`);
        Array.from(child.children).slice(0, 5).forEach((gc: any, j) => {
          lines.push(`    grandchild[${j}] tag=${gc.tagName} class="${gc.className?.substring(0, 120)}" data-test="${gc.getAttribute('data-test') ?? ''}"`);
        });
      });
    } else {
      lines.push('  (no [data-test="chat-section"] found)');
    }
    lines.push('');
    
    // Elements with substantial text (likely response blocks)
    lines.push('=== ELEMENTS WITH SUBSTANTIAL TEXT (>200 chars) ===');
    const all = Array.from(document.querySelectorAll('div, article, section, p'));
    let count = 0;
    all.forEach((el: any, i) => {
      const text = el.textContent?.trim() ?? '';
      if (text.length < 200) return;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      if (count++ > 50) return;
      const dtParent = el.closest('[data-test]')?.getAttribute('data-test') ?? '';
      const cls = (el.className ?? '').substring(0, 100);
      lines.push(`[${i}] tag=${el.tagName} data-test="${el.getAttribute('data-test') ?? ''}" ` +
        `nearest-data-test="${dtParent}" ` +
        `class="${cls}" ` +
        `chars=${text.length} preview="${text.substring(0, 80).replace(/\n/g,' ')}"`);
    });
    lines.push('');
    
    // Buttons present now
    const btns = Array.from(document.querySelectorAll('button'));
    lines.push(`=== BUTTONS (total: ${btns.length}) ===`);
    btns.slice(0, 20).forEach((btn: any, i) => {
      const t = btn.textContent?.trim().replace(/\n/g,' ').substring(0, 80) ?? '';
      lines.push(`[${i}] type="${btn.getAttribute('type') ?? ''}" data-test="${btn.getAttribute('data-test') ?? ''}" class="${(btn.className ?? '').substring(0,80)}" text="${t}"`);
    });
    
    return lines.join('\n');
  });
  
  const summaryPath = path.join(OUT, 'convo_dom_summary.txt');
  fs.writeFileSync(summaryPath, summary, 'utf-8');
  console.log('Written:', summaryPath);
  console.log('\n' + summary.substring(0, 6000));
  
  await browser.close();
}

main().catch((e: any) => { console.error(e); process.exit(1); });
