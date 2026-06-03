/**
 * cdp_instructions.ts — Shared CDP readiness text and lightweight TCP probe.
 * Used by morning-full, morning check, and warren_client error paths.
 */
import { spawnSync } from 'child_process';
import fs from 'fs';
import net from 'net';

/** Default CDP HTTP endpoint (see .env CHROME_CDP_URL). */
export const CHROME_CDP_DEFAULT_URL = 'http://127.0.0.1:9222';

/**
 * Exact Chrome launch line for the dedicated Warren AI profile.
 * Chrome 111+ requires --remote-allow-origins for CDP WebSocket clients (e.g. Playwright).
 * (Keep in sync with DAILY_RUN.md and Dashboard copy.)
 */
export const CHROME_CDP_LAUNCH_COMMAND =
  'google-chrome --remote-debugging-port=9222 --remote-allow-origins=\'*\' --user-data-dir="$HOME/.warren-ai-chrome-profile"';

export type CdpTcpProbe = 'open' | 'closed' | 'unknown';

export function parseCdpTcpTarget(cdpUrl: string): { host: string; port: number } | null {
  try {
    const u = new URL(cdpUrl);
    if (!u.port) {
      return { host: u.hostname, port: u.protocol === 'https:' ? 443 : 80 };
    }
    const port = parseInt(u.port, 10);
    if (Number.isNaN(port)) return null;
    return { host: u.hostname, port };
  } catch {
    return null;
  }
}

/** True if something accepts TCP on host:port (does not validate CDP protocol). */
export function probeTcpPort(host: string, port: number, timeoutMs = 2500): Promise<CdpTcpProbe> {
  return new Promise((resolve) => {
    const sock = net.createConnection({ host, port });
    let settled = false;
    const finish = (v: CdpTcpProbe) => {
      if (settled) return;
      settled = true;
      sock.removeAllListeners();
      try {
        sock.destroy();
      } catch {
        /* ignore */
      }
      resolve(v);
    };
    sock.setTimeout(timeoutMs);
    sock.once('connect', () => finish('open'));
    sock.once('error', () => finish('closed'));
    sock.once('timeout', () => finish('closed'));
  });
}

export function describeCdpPortProbe(
  cdpUrl: string,
  probe: CdpTcpProbe,
  tcp: { host: string; port: number } | null,
): string {
  if (!tcp) return 'Could not parse CHROME_CDP_URL for a TCP check.';
  const ep = `${tcp.host}:${tcp.port}`;
  if (probe === 'closed') {
    return `Diagnosis: Nothing is accepting TCP connections on ${ep} — the debugging port is closed or CHROME_CDP_URL points to the wrong host/port.`;
  }
  if (probe === 'open') {
    return (
      `Diagnosis: ${ep} accepts TCP, but Playwright could not complete a CDP session. ` +
      'On Chrome 111+, add --remote-allow-origins=\'*\' next to --remote-debugging-port, ' +
      'then fully quit and relaunch Chrome. Extra Warren AI tabs do not fix this — it is a browser launch flag. ' +
      'Also confirm nothing non-Chrome is bound to that port.'
    );
  }
  return `Diagnosis: Could not determine whether ${ep} is open (probe inconclusive).`;
}

export interface CdpBlockedPreambleOptions {
  /** Shown after "then rerun:" (e.g. npm run morning-full). */
  primaryRerun?: string;
  /** Banner title line (workflow-specific). */
  title?: string;
}

/** True if argv text includes Chrome's CDP origin allowlist flag (any spelling Chrome accepts). */
export function cmdlineHasRemoteAllowOrigins(argvText: string): boolean {
  return /--remote-allow-origins(?:=|\s+)/i.test(argvText);
}

function readListenerPid(port: number): string | null {
  const r = spawnSync('lsof', ['-t', `-iTCP:${port}`, '-sTCP:LISTEN'], {
    encoding: 'utf-8',
    stdio: ['ignore', 'pipe', 'ignore'],
  });
  if (r.status !== 0) return null;
  const pid = (r.stdout || '').trim().split(/\n/)[0];
  return pid && /^\d+$/.test(pid) ? pid : null;
}

function readProcessCmdline(pid: string): string | null {
  if (process.platform === 'linux') {
    try {
      const raw = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf-8');
      const s = raw.replace(/\0/g, ' ').trim();
      return s || null;
    } catch {
      return null;
    }
  }
  const ps = spawnSync('ps', ['-ww', '-p', pid, '-o', 'args='], {
    encoding: 'utf-8',
    stdio: ['ignore', 'pipe', 'ignore'],
  });
  if (ps.status !== 0) return null;
  const s = (ps.stdout || '').trim();
  return s || null;
}

/**
 * When TCP is open but CDP attach fails, inspect the OS listener (best-effort) and
 * report whether Chrome was started without --remote-allow-origins (common cause).
 */
export function getCdpListenerCmdlineHintLines(port: number): string[] {
  const pid = readListenerPid(port);
  if (!pid) return [];
  const cmd = readProcessCmdline(pid);
  if (!cmd) return [];
  const has = cmdlineHasRemoteAllowOrigins(cmd);
  const shortCmd = cmd.length > 200 ? `${cmd.slice(0, 197)}…` : cmd;
  if (has) {
    return [
      '',
      `  Listener: PID ${pid} has --remote-allow-origins in its command line, but CDP still failed.`,
      '  Try: fully quit every Chrome using this port, then relaunch with the one-liner below.',
      '  For a machine-readable check: npm run diagnose-cdp',
    ];
  }
  return [
    '',
    `  Listener: PID ${pid} on port ${port} is missing --remote-allow-origins (verified from OS).`,
    `  Command (truncated): ${shortCmd}`,
    '  Quit this Chrome instance completely, then start again using the one-liner below.',
    '  Re-run: npm run diagnose-cdp — to confirm Playwright can attach before morning-full.',
  ];
}

/** Multi-line human instructions when CDP is not usable (console / logs). */
export function formatCdpBlockedPreambleLines(
  cdpUrl: string,
  portProbe: CdpTcpProbe,
  options?: CdpBlockedPreambleOptions,
): string[] {
  const primaryRerun = options?.primaryRerun ?? 'npm run morning-full';
  const title =
    options?.title ??
    'CHROME CDP REQUIRED — this command cannot attach to Chrome';
  const titleLine = title.length > 62 ? `${title.slice(0, 59)}...` : title;
  const tcp = parseCdpTcpTarget(cdpUrl);
  const diag = describeCdpPortProbe(cdpUrl, portProbe, tcp);
  const listenerHints = portProbe === 'open' && tcp ? getCdpListenerCmdlineHintLines(tcp.port) : [];
  return [
    '',
    '╔════════════════════════════════════════════════════════════════════╗',
    `║  ${titleLine.padEnd(62)}      ║`,
    '╚════════════════════════════════════════════════════════════════════╝',
    '',
    `  CDP URL: ${cdpUrl}`,
    `  ${diag}`,
    ...listenerHints,
    '',
    '  ▶ Open the dedicated Warren AI Chrome window first, log in, pass any',
    '    security checks, then rerun:',
    '',
    `      ${primaryRerun}`,
    '',
    '  ▶ Launch Chrome with remote debugging (copy/paste as one line):',
    '',
    `      ${CHROME_CDP_LAUNCH_COMMAND}`,
    '',
    '  Then open https://www.investing.com/warrenai in that window and confirm',
    '  the Warren AI chat input is visible before re-running.',
    '',
  ];
}
