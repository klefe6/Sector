/**
 * portfolio_loader.ts — Load portfolio JSON and build Warren AI text block.
 *
 * Primary file: config/current_portfolio.json (synced from positions.db via
 * npm run export-portfolio, or edited manually).
 */
import fs from 'fs';
import path from 'path';

export interface PortfolioPosition {
  ticker: string;
  company?: string | null;
  side?: string | null;
  quantity?: number | null;
  avg_cost?: number | null;
  sector?: string | null;
  thesis?: string | null;
  notes?: string | null;
}

export interface PortfolioFile {
  updated_at?: string;
  source?: string;
  positions: PortfolioPosition[];
}

const DEFAULT_REL = path.join('config', 'current_portfolio.json');

export function portfolioJsonPath(): string {
  const override = process.env.PORTFOLIO_JSON_PATH;
  if (override) return path.resolve(override);
  return path.resolve(process.cwd(), DEFAULT_REL);
}

export function loadPortfolioFile(): PortfolioFile {
  const fp = portfolioJsonPath();
  if (!fs.existsSync(fp)) {
    return { updated_at: '', positions: [], source: 'missing' };
  }
  try {
    const raw = fs.readFileSync(fp, 'utf-8');
    const j = JSON.parse(raw) as PortfolioFile;
    if (!Array.isArray(j.positions)) j.positions = [];
    return j;
  } catch {
    return { updated_at: '', positions: [], source: 'parse_error' };
  }
}

function _fmt(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number' && !Number.isFinite(v)) return '—';
  return String(v).trim() || '—';
}

/**
 * Warren-friendly block for {{CURRENT_PORTFOLIO}} substitution.
 */
export function formatPortfolioBlock(file: PortfolioFile): string {
  const rows = file.positions || [];
  if (rows.length === 0) return '(No current holdings.)';

  const header =
    'Ticker | Company | Side | Quantity | Avg Cost | Sector | Thesis or Notes';
  const lines = [header, '---'];
  for (const p of rows) {
    const note = [p.thesis, p.notes].filter(Boolean).join(' — ');
    lines.push(
      [
        _fmt(p.ticker),
        _fmt(p.company),
        _fmt(p.side || 'Long'),
        _fmt(p.quantity),
        _fmt(p.avg_cost),
        _fmt(p.sector),
        note.replace(/\|/g, '/').replace(/\n/g, ' ').slice(0, 500) || '—',
      ].join(' | '),
    );
  }
  return lines.join('\n');
}

export function isPortfolioEmpty(file: PortfolioFile): boolean {
  return !file.positions || file.positions.length === 0;
}
