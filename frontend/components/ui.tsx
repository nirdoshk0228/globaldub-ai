"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CHECK_LABEL, Flag, LANG_NAME, NATIVE } from "@/lib/api";

/** Fetch on mount and keep polling while `active(data)` is true. */
export function usePoll<T>(load: () => Promise<T>, active: (d: T | null) => boolean, ms = 2000, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeRef = useRef(active);
  activeRef.current = active;

  const tick = useCallback(async () => {
    try {
      const d = await load();
      setData(d);
      setError(null);
      if (activeRef.current(d)) timer.current = setTimeout(tick, ms);
    } catch (e) {
      setError((e as Error).message);
      timer.current = setTimeout(tick, ms * 3);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    tick();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [tick]);

  const refresh = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    return tick();
  }, [tick]);

  return { data, error, refresh, setData };
}

const STATUS_STYLE: Record<string, string> = {
  ready: "bg-ok-soft text-ok",
  auto_ok: "bg-ok-soft text-ok",
  approved: "bg-ok-soft text-ok",
  edited: "bg-brand-soft text-brand",
  partial: "bg-warn-soft text-warn",
  flagged: "bg-warn-soft text-warn",
  processing: "bg-brand-soft text-brand",
  running: "bg-brand-soft text-brand",
  queued: "bg-line/60 text-muted",
  pending: "bg-line/60 text-muted",
  kept: "bg-line/60 text-ink",
  failed: "bg-bad-soft text-bad",
};

const STATUS_TEXT: Record<string, string> = {
  auto_ok: "Passed QA",
  approved: "Approved",
  edited: "Edited",
  flagged: "Needs review",
  pending: "Pending",
  kept: "Kept original",
  ready: "Ready",
  partial: "Partly ready",
  processing: "Processing",
  running: "Processing",
  queued: "Queued",
  failed: "Failed",
};

export function StatusPill({ status }: { status: string }) {
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLE[status] ?? "bg-line text-muted"}`}>
      {STATUS_TEXT[status] ?? status}
    </span>
  );
}

const SEV: Record<string, string> = {
  high: "border-bad text-bad bg-bad-soft",
  medium: "border-warn text-warn bg-warn-soft",
  low: "border-line text-muted bg-sheet",
};

export function FlagItem({ flag }: { flag: Flag }) {
  return (
    <li className={`rounded-md border-l-4 px-3 py-2 text-sm ${SEV[flag.severity]}`}>
      <div className="font-medium">
        {CHECK_LABEL[flag.check] ?? flag.check}
        <span className="ml-2 text-xs font-normal opacity-80">
          {flag.severity} · {flag.source === "llm" ? "LLM judge" : flag.source === "audio" ? "audio check" : "rule"}
        </span>
      </div>
      <div className="text-ink">{flag.message}</div>
      {flag.suggestion && <div className="mt-0.5 text-muted">{flag.suggestion}</div>}
    </li>
  );
}

export function LangName({ code, withNative = true }: { code: string; withNative?: boolean }) {
  return (
    <span>
      {LANG_NAME[code] ?? code}
      {withNative && NATIVE[code] && NATIVE[code] !== LANG_NAME[code] && (
        <span className="ml-1.5 text-muted" dir={code === "ar-SA" ? "rtl" : undefined}>
          {NATIVE[code]}
        </span>
      )}
    </span>
  );
}

export function ErrorNote({ children }: { children: React.ReactNode }) {
  return <p className="rounded-md border border-bad/30 bg-bad-soft px-4 py-3 text-sm text-bad">{children}</p>;
}

export function ProgressBar({ value, status }: { value: number; status: string }) {
  const color = status === "failed" ? "bg-bad" : status === "ready" ? "bg-ok" : "bg-brand";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-line/70" role="progressbar" aria-valuenow={Math.round(value * 100)}>
      <div className={`h-full ${color} transition-[width] duration-700`} style={{ width: `${Math.max(3, value * 100)}%` }} />
    </div>
  );
}

export const STAGE_TEXT: Record<string, string> = {
  queued: "Waiting in queue",
  localize: "Adapting dialogue",
  qa: "Checking for errors",
  synthesize: "Voicing characters",
  assemble: "Mixing the episode",
  done: "Done",
};

export function stageText(stage: string): string {
  if (stage.startsWith("retry")) return `Fixing flagged lines (${stage})`;
  return STAGE_TEXT[stage] ?? stage;
}
