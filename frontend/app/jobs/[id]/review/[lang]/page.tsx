"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, CHECK_LABEL, editToken, fileUrl, fmtTime, LANG_NAME, TrackDetail, TrackLine } from "@/lib/api";
import { ErrorNote, FlagItem, StatusPill, usePoll } from "@/components/ui";

type Filter = "flagged" | "all" | "done";

const DOT: Record<string, string> = {
  flagged: "bg-warn",
  auto_ok: "bg-ok",
  approved: "bg-ok",
  edited: "bg-brand",
  pending: "bg-line",
  kept: "bg-muted",
};

function Timeline({ lines, runtime, selected, onPick }: { lines: TrackLine[]; runtime: number; selected?: string; onPick: (id: string) => void }) {
  return (
    <div className="relative h-9 w-full rounded-md bg-sheet ring-1 ring-line" aria-label="Episode timeline">
      {lines.map((l) => (
        <button
          key={l.seg_id}
          title={`${fmtTime(l.start)} ${l.speaker}: ${l.status}`}
          aria-label={`Line at ${fmtTime(l.start)}, ${l.speaker}, ${l.status}`}
          onClick={() => onPick(l.seg_id)}
          className={`absolute top-1.5 bottom-1.5 rounded-sm ${DOT[l.status] ?? "bg-line"} ${selected === l.seg_id ? "ring-2 ring-ink ring-offset-1" : "opacity-80 hover:opacity-100"}`}
          style={{ left: `${(l.start / runtime) * 100}%`, width: `max(4px, ${((l.end - l.start) / runtime) * 100}%)` }}
        />
      ))}
    </div>
  );
}

function Clip({ url, label }: { url?: string; label: string }) {
  if (!url) return <span className="text-sm text-muted">{label}: not available</span>;
  return (
    <div>
      <p className="mb-1 text-xs text-muted">{label}</p>
      <audio src={url} controls preload="none" className="h-9 w-full" />
    </div>
  );
}

export default function Review() {
  const { id, lang } = useParams<{ id: string; lang: string }>();
  const [token, setToken] = useState<string | null>(null);
  useEffect(() => setToken(editToken(id)), [id]);

  const { data: track, error, refresh, setData } = usePoll<TrackDetail>(
    () => api<TrackDetail>(`/api/jobs/${id}/tracks/${lang}`),
    (d) => !!d && d.lines.some((l) => l.rendering),
    1500,
    [id, lang],
  );

  const [filter, setFilter] = useState<Filter>("flagged");
  const [selected, setSelected] = useState<string | undefined>();
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const openedAt = useRef<number>(Date.now());

  const visible = useMemo(() => {
    if (!track) return [];
    return track.lines.filter((l) =>
      filter === "all" ? true : filter === "flagged" ? l.status === "flagged" : l.status === "approved" || l.status === "edited",
    );
  }, [track, filter]);

  const line = track?.lines.find((l) => l.seg_id === selected);

  // pick the first line to review once data arrives
  useEffect(() => {
    if (track && !selected) {
      const first = track.lines.find((l) => l.status === "flagged") ?? track.lines[0];
      if (first) setSelected(first.seg_id);
      if (!track.lines.some((l) => l.status === "flagged")) setFilter("all");
    }
  }, [track, selected]);

  useEffect(() => {
    if (line) {
      setDraft(line.target_text);
      setActionError(null);
      openedAt.current = Date.now();
    }
    // reset the draft only when moving to another line or a new version arrives
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [line?.seg_id, line?.version]);

  const go = useCallback(
    (delta: number) => {
      if (!visible.length) return;
      const i = visible.findIndex((l) => l.seg_id === selected);
      const next = visible[Math.min(visible.length - 1, Math.max(0, (i === -1 ? 0 : i) + delta))];
      setSelected(next.seg_id);
    },
    [visible, selected],
  );

  const act = useCallback(
    async (action: "approve" | "edit" | "reopen") => {
      if (!line || !token) return;
      setBusy(true);
      setActionError(null);
      try {
        const res = await api<{ line: { status: TrackLine["status"]; version: number; target_text: string } }>(
          `/api/jobs/${id}/tracks/${lang}/lines/${line.seg_id}`,
          {
            method: "PATCH",
            headers: { "content-type": "application/json", "x-edit-token": token },
            body: JSON.stringify({
              action,
              target_text: action === "edit" ? draft : undefined,
              seconds_spent: Math.round((Date.now() - openedAt.current) / 100) / 10,
            }),
          },
        );
        setData((d) =>
          d && {
            ...d,
            lines: d.lines.map((l) =>
              l.seg_id === line.seg_id ? { ...l, ...res.line, rendering: action === "edit" && res.line.version !== l.tts_version } : l,
            ),
          },
        );
        refresh();
        if (action !== "reopen" && filter === "flagged") go(1);
      } catch (e) {
        setActionError(e instanceof ApiError ? e.message : "That didn't save. Try again.");
      } finally {
        setBusy(false);
      }
    },
    [line, token, id, lang, draft, refresh, setData, filter, go],
  );

  // keyboard: j/k move, a approve (when not typing)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const typing = (e.target as HTMLElement)?.tagName === "TEXTAREA" || (e.target as HTMLElement)?.tagName === "INPUT";
      if (typing) return;
      if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); go(1); }
      if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); go(-1); }
      if (e.key === "a" && token && line && line.status !== "approved") act("approve");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, act, token, line]);

  if (error && !track) return <div className="pt-12"><ErrorNote>{error}</ErrorNote></div>;
  if (!track) return <p className="pt-12 text-muted">Loading the track...</p>;

  const runtime = Math.max(1, ...track.lines.map((l) => l.end));
  const s = track.summary;
  const changed = line && draft.trim() !== line.target_text.trim();
  const counts = {
    flagged: track.lines.filter((l) => l.status === "flagged").length,
    done: track.lines.filter((l) => l.status === "approved" || l.status === "edited").length,
    all: track.lines.length,
  };

  return (
    <div className="pt-8">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <Link href={`/jobs/${id}`} className="text-sm text-muted hover:text-ink">{track.title}</Link>
        <h1 className="display text-xl">Review Studio: {LANG_NAME[lang] ?? track.language}</h1>
        <span className="text-sm text-muted">
          {s.auto_ok} passed QA, {counts.flagged} need review, {counts.done} done
        </span>
      </div>
      {!token && (
        <p className="mt-3 rounded-md bg-brand-soft px-4 py-2 text-sm text-brand">
          Read-only view. Lines can be edited from the browser that started this job.
        </p>
      )}

      <div className="mt-5">
        <Timeline lines={track.lines} runtime={runtime} selected={selected} onPick={(sid) => { setSelected(sid); setFilter("all"); }} />
        <div className="mt-2 flex flex-wrap gap-4 text-xs text-muted">
          <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-warn" />needs review</span>
          <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-ok" />passed or approved</span>
          <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-brand" />edited by reviewer</span>
          {track.lines.some((l) => l.status === "kept") && (
            <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-muted" />sung, kept original</span>
          )}
          <span className="ml-auto hidden md:inline">Keys: J / K to move, A to approve</span>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 md:grid-cols-[20rem_1fr]">
        <aside className="min-w-0">
          <div role="tablist" className="flex gap-1 rounded-md bg-sheet p-1 ring-1 ring-line">
            {(["flagged", "all", "done"] as Filter[]).map((f) => (
              <button key={f} role="tab" aria-selected={filter === f} onClick={() => setFilter(f)}
                      className={`flex-1 rounded px-2 py-1.5 text-sm ${filter === f ? "bg-ink text-white" : "text-muted hover:text-ink"}`}>
                {f === "flagged" ? "To review" : f === "all" ? "All" : "Done"} ({counts[f]})
              </button>
            ))}
          </div>
          <ul className="mt-3 max-h-[60vh] overflow-y-auto rounded-md ring-1 ring-line">
            {visible.length === 0 && <li className="p-4 text-sm text-muted">Nothing here. {filter === "flagged" && "Every line has been reviewed."}</li>}
            {visible.map((l) => (
              <li key={l.seg_id}>
                <button onClick={() => setSelected(l.seg_id)}
                        className={`block w-full border-b border-line px-3 py-2.5 text-left ${selected === l.seg_id ? "bg-brand-soft" : "bg-sheet hover:bg-paper"}`}>
                  <span className="flex items-center gap-2 text-xs text-muted">
                    <i className={`inline-block h-2 w-2 rounded-full ${DOT[l.status]}`} />
                    {fmtTime(l.start)} {l.speaker}
                    {l.rendering && <span className="text-brand">re-voicing</span>}
                  </span>
                  <span className="mt-0.5 block truncate text-sm" dir={track.direction}>{l.target_text || "(empty)"}</span>
                  {l.status === "flagged" && (
                    <span className="mt-0.5 block truncate text-xs text-warn">
                      {[...new Set(l.flags.filter((f) => f.severity !== "low").map((f) => CHECK_LABEL[f.check] ?? f.check))].join(", ")}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </aside>

        {line ? (
          <section className="min-w-0 rounded-lg border border-line bg-sheet p-5 md:p-6">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-semibold">{line.speaker}</h2>
              <span className="text-sm text-muted">
                {fmtTime(line.start)} to {fmtTime(line.end)}
                {line.source_emotion !== "unknown" && <>, {line.source_emotion} in the original</>}
                {line.audio_hint && <>, said {line.audio_hint}</>}
              </span>
              <StatusPill status={line.status} />
            </div>

            <div className="mt-5 grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <p className="text-xs text-muted">Original</p>
                <p lang="hi" className="mt-1 text-xl leading-snug">{line.source_text}</p>
                <div className="mt-3"><Clip url={fileUrl(line.source_clip_url)} label="Hear the original line" /></div>
              </div>
              <div>
                <label htmlFor="draft" className="text-xs text-muted">{LANG_NAME[lang] ?? track.language} dub</label>
                <textarea id="draft" dir={track.direction} value={draft} onChange={(e) => setDraft(e.target.value)} readOnly={!token}
                          rows={3} className="mt-1 block w-full resize-y rounded-md border border-line bg-paper px-3 py-2 text-xl leading-snug" />
                {line.back_translation && !lang.startsWith("en") && (
                  <p className="mt-2 text-sm text-muted">Means: {line.back_translation}</p>
                )}
                <div className="mt-3">
                  {line.rendering ? (
                    <p className="text-sm text-brand">Re-voicing this line...</p>
                  ) : (
                    <Clip key={line.tts_url ?? ""} url={fileUrl(line.tts_url)} label={`Hear the dub${line.stretch > 1 ? ` (sped up ${line.stretch}x to fit)` : ""}`} />
                  )}
                </div>
              </div>
            </div>

            {line.status === "kept" && (
              <p className="mt-5 rounded-md bg-paper px-4 py-3 text-sm text-ink ring-1 ring-line">
                This line is sung, so it stays in the original voice: a text-to-speech voice can read lyrics but
                cannot sing them, and dubbed shows usually keep songs or re-record them with a singer. To dub it as
                speech anyway, type the line above and save.
              </p>
            )}
            {line.adaptation_note && line.status !== "kept" && (
              <p className="mt-5 border-l-2 border-brand pl-3 text-sm text-muted">Adaptation: {line.adaptation_note}</p>
            )}
            <p className="mt-2 text-sm text-muted">
              Voiced as {line.delivery.emotion}, intensity {Math.round(line.delivery.intensity * 100)}%, {line.delivery.pace} pace
              {line.attempts > 1 && <>. Rewritten {line.attempts - 1} time{line.attempts > 2 ? "s" : ""} by the agent after QA</>}.
            </p>

            {line.flags.length > 0 && (
              <ul className="mt-5 space-y-2">
                {line.flags.map((f, i) => <FlagItem key={i} flag={f} />)}
              </ul>
            )}

            {actionError && <div className="mt-4"><ErrorNote>{actionError}</ErrorNote></div>}
            {token && (
              <div className="mt-6 flex flex-wrap gap-3">
                {changed ? (
                  <button disabled={busy || !draft.trim()} onClick={() => act("edit")} className="rounded-md bg-brand px-5 py-2.5 font-medium text-white hover:bg-ink">
                    Save and re-voice
                  </button>
                ) : (
                  line.status !== "approved" && (
                    <button disabled={busy} onClick={() => act("approve")} className="rounded-md bg-ok px-5 py-2.5 font-medium text-white hover:bg-ink">
                      Approve line
                    </button>
                  )
                )}
                {changed && (
                  <button onClick={() => setDraft(line.target_text)} className="rounded-md border border-line px-4 py-2.5 text-sm">
                    Discard changes
                  </button>
                )}
                {(line.status === "approved" || line.status === "edited") && !changed && (
                  <button disabled={busy} onClick={() => act("reopen")} className="rounded-md border border-line px-4 py-2.5 text-sm">
                    Reopen
                  </button>
                )}
                <button onClick={() => go(1)} className="ml-auto rounded-md border border-line px-4 py-2.5 text-sm">Next line</button>
              </div>
            )}
          </section>
        ) : (
          <p className="text-muted">Pick a line to review.</p>
        )}
      </div>

      {track.dub_url && (
        <div className="mt-10 rounded-lg border border-line bg-sheet p-5">
          <p className="text-sm text-muted">Full episode with your edits</p>
          <audio key={track.dub_url} src={fileUrl(track.dub_url)} controls preload="none" className="mt-2 w-full" />
        </div>
      )}
    </div>
  );
}
