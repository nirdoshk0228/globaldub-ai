"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, BACKGROUND_TEXT, editToken, fileUrl, fmtTime, Job, LANG_NAME, money, NATIVE, TrackDetail } from "@/lib/api";
import { ErrorNote, LangName, ProgressBar, StatusPill, stageText, usePoll } from "@/components/ui";

function Player({ job }: { job: Job }) {
  const ready = job.tracks.filter((t) => t.status === "ready" && t.dub_url);
  const [lang, setLang] = useState<string>(ready[0]?.lang ?? "original");
  const [detail, setDetail] = useState<TrackDetail | null>(null);
  const [now, setNow] = useState(0);
  const audioRef = useRef<HTMLAudioElement>(null);

  const trackForText = lang === "original" ? ready[0]?.lang : lang;
  useEffect(() => {
    if (!trackForText) return;
    api<TrackDetail>(`/api/jobs/${job.id}/tracks/${trackForText}`).then(setDetail).catch(() => {});
  }, [job.id, trackForText]);

  const src = lang === "original" ? fileUrl(job.source_audio_url) : fileUrl(ready.find((t) => t.lang === lang)?.dub_url);
  const current = useMemo(() => detail?.lines.find((l) => now >= l.start - 0.1 && now <= l.end + 0.4), [detail, now]);

  function switchTo(next: string) {
    const t = audioRef.current?.currentTime ?? 0;
    const wasPlaying = audioRef.current && !audioRef.current.paused;
    setLang(next);
    requestAnimationFrame(() => {
      if (audioRef.current) {
        audioRef.current.currentTime = t;  // keep position so you can A/B the same moment
        if (wasPlaying) audioRef.current.play().catch(() => {});
      }
    });
  }

  if (!ready.length && !job.source_audio_url) return null;
  const options = [...(job.source_audio_url ? ["original"] : []), ...ready.map((t) => t.lang)];

  return (
    <section className="mt-10 rounded-lg border border-line bg-sheet p-5 md:p-7">
      <div role="tablist" aria-label="Listen in" className="flex flex-wrap gap-2">
        {options.map((o) => (
          <button
            key={o}
            role="tab"
            aria-selected={lang === o}
            onClick={() => switchTo(o)}
            className={`rounded-full border px-3.5 py-1 text-sm ${lang === o ? "border-ink bg-ink text-white" : "border-line hover:border-ink/40"}`}
          >
            {o === "original" ? "Original Hindi" : NATIVE[o] ?? o}
          </button>
        ))}
      </div>

      <div className="mt-6 min-h-[7.5rem]" aria-live="polite">
        {current ? (
          <>
            <p className="text-sm text-muted">{current.speaker}</p>
            {lang === "original" ? (
              <p lang="hi" className="display text-xl">{current.source_text}</p>
            ) : (
              <>
                <p className="display text-xl" dir={detail?.direction}>{current.target_text}</p>
                <p lang="hi" className="mt-2 text-muted">{current.source_text}</p>
              </>
            )}
          </>
        ) : (
          <p className="text-muted">Press play. Switching language keeps your place in the scene.</p>
        )}
      </div>

      {src && (
        <audio ref={audioRef} key={src} src={src} controls preload="metadata" className="mt-4 w-full"
               onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)} />
      )}
      {lang !== "original" && (
        <div className="mt-3 flex gap-4 text-sm">
          <a className="text-brand underline" href={src} download>Download dub (MP3)</a>
          {ready.find((t) => t.lang === lang)?.srt_url && (
            <a className="text-brand underline" href={fileUrl(ready.find((t) => t.lang === lang)?.srt_url)} download>Subtitles (SRT)</a>
          )}
        </div>
      )}
    </section>
  );
}

export default function JobPage() {
  const { id } = useParams<{ id: string }>();
  const { data: job, error } = usePoll<Job>(
    () => api<Job>(`/api/jobs/${id}`),
    (d) => !d || d.status === "queued" || d.status === "processing",
    2000,
    [id],
  );
  const [canEdit, setCanEdit] = useState(false);
  useEffect(() => setCanEdit(!!editToken(id)), [id]);

  if (error && !job) return <div className="pt-12"><ErrorNote>{error}</ErrorNote></div>;
  if (!job) return <p className="pt-12 text-muted">Loading...</p>;
  const busy = job.status === "queued" || job.status === "processing";

  return (
    <div className="pt-12">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="display text-2xl">{job.title}</h1>
        <StatusPill status={job.status} />
      </div>
      <p className="mt-2 text-sm text-muted">
        {job.lines} lines, {fmtTime(job.runtime_seconds)} from {LANG_NAME[job.source_lang] ?? job.source_lang}.
        {job.cost_usd > 0 && <> Processing cost so far {money(job.cost_usd)}.</>}
      </p>
      {!busy && <p className="mt-1 text-sm text-muted">{BACKGROUND_TEXT[job.background] ?? job.background}</p>}
      {job.synopsis && <p className="mt-3 measure text-muted">{job.synopsis}</p>}
      {job.error && <div className="mt-4"><ErrorNote>{job.error}</ErrorNote></div>}

      {!busy && <Player job={job} />}

      <section className="mt-12">
        <h2 className="display text-xl">{busy ? "Pipeline progress" : "Languages"}</h2>
        <div className="mt-4 divide-y divide-line border-y border-line">
          {job.tracks.map((t) => (
            <div key={t.lang} className="grid grid-cols-1 items-center gap-3 py-4 md:grid-cols-[14rem_1fr_auto]">
              <div>
                <LangName code={t.lang} />
                <div className="mt-1"><StatusPill status={t.status} /></div>
              </div>
              <div>
                {t.status === "ready" ? (
                  <p className="text-sm">
                    <span className="text-ok">{t.auto_ok} passed QA</span>
                    <span className="mx-2 text-line">|</span>
                    <span className={t.flagged ? "text-warn" : "text-muted"}>{t.flagged} need review</span>
                    <span className="mx-2 text-line">|</span>
                    <span className="text-muted">{t.approved} approved or edited</span>
                    {t.kept_original > 0 && (
                      <>
                        <span className="mx-2 text-line">|</span>
                        <span className="text-muted">{t.kept_original} sung kept original</span>
                      </>
                    )}
                    <span className="mx-2 text-line">|</span>
                    <span className="text-muted">{money(t.cost_per_minute_usd)} per finished minute</span>
                  </p>
                ) : t.status === "failed" ? (
                  <p className="text-sm text-bad">{t.error}</p>
                ) : (
                  <>
                    <p className="mb-2 text-sm text-muted">{stageText(t.stage)}</p>
                    <ProgressBar value={t.progress} status={t.status} />
                  </>
                )}
              </div>
              {t.status === "ready" && (
                <Link href={`/jobs/${job.id}/review/${t.lang}`} className="rounded-md border border-ink/20 px-4 py-2 text-sm font-medium hover:border-ink">
                  {canEdit ? "Review flagged lines" : "Open in Review Studio"}
                </Link>
              )}
            </div>
          ))}
        </div>
        {!busy && (
          <Link href={`/jobs/${job.id}/report`} className="mt-5 inline-block text-brand underline">
            Cost and quality report
          </Link>
        )}
      </section>
    </div>
  );
}
