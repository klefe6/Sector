/**
 * validate_only.ts — Re-run output validation against an existing day folder.
 *
 * Does not connect to Chrome, send any prompts, or modify Warren outputs.
 * Safe to run at any time against already-written output files.
 *
 * Usage:
 *   npm run validate                         # validates today's output folder
 *   npm run validate -- output/2026-05-26    # validates a specific folder
 */
import { runValidation } from './validator';
import path from 'path';
import fs from 'fs';

function todayString(): string {
  return new Date().toISOString().slice(0, 10);
}

const rawArg   = process.argv[2];
const outputDir = path.resolve(process.env.OUTPUT_DIR || './output');
const dayDir   = rawArg
  ? path.resolve(rawArg)
  : path.join(outputDir, todayString());

console.log('');
console.log('═'.repeat(60));
console.log('  Warren AI — Output Validation');
console.log(`  Folder: ${dayDir}`);
console.log('═'.repeat(60));
console.log('');

if (!fs.existsSync(dayDir)) {
  console.error(`  ✗ Folder not found: ${dayDir}`);
  console.error('  Pass an existing output folder as the first argument.');
  process.exit(1);
}

const result = runValidation(dayDir);

console.log('');
if (result.passed) {
  console.log('  ✓ All validation checks passed.');
} else {
  console.log(`  ✗ ${result.warnings.length} warning(s) — see validation_warnings.md`);
}
console.log('');

process.exit(result.passed ? 0 : 1);
