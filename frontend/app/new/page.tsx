"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { API, api, ApiError, Language, rememberJob } from "@/lib/api";
import { ErrorNote } from "@/components/ui";

interface Limits {
  max_upload_mb: number;
  max_audio_seconds: number;
  max_langs_per_job: number;
  jobs_per_day: number;
  jobs_used_today: number;
  budget_left_today: boolean;
}

export default function NewJob() {
  const router = useRouter();
  const [langs, setLangs] = useState<Language[]>([]);
  const [limits, setLimits] = useState<Limits | null>(null);
  const [picked, setPicked] = useState<string[]>(["en-US", "ta-IN"]);
  const [file, setFile] = useState<File | null>(null);
  const [bed, setBed] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Language[]>("/api/languages").then(setLangs).catch((e) => setError(e.message));
    api<Limits>("/api/limits").then(setLimits).catch(() => {});
  }, []);

  const max = limits?.max_langs_per_job ?? 3;
  const toggle = (code: string) =>
    setPicked((p) => (p.includes(code) ? p.filter((x) => x !== code) : p.length >= max ? p : [...p, code]));

  async function useSample() {
    const res = await fetch("/samples/virasat_ep01.json");
    const blob = await res.blob();
    setFile(new File([blob], "virasat_ep01.json", { type: "application/json" }));
    const bedRes = await fetch("/samples/virasat_bed.mp3");
    setBed(new File([await bedRes.blob()], "virasat_bed.mp3", { type: "audio/mpeg" }));
    setTitle("Virasat - Episode 1: The Missing Papers");
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!file) return setError("Choose a script or an audio file first.");
    if (!picked.length) return setError("Pick at least one language.");
    setBusy(true);
    const fd = new FormData();
    fd.append("file", file);
    if (bed) fd.append("bed", bed);
    fd.append("target_langs", picked.join(","));
    if (title) fd.append("title", title);
    try {
      const out = await api<{ id: string; edit_token: string }>("/api/jobs", { method: "POST", body: fd });
      rememberJob(out.id, out.edit_token);
      router.push(`/jobs/${out.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed.");
      setBusy(false);
    }
  }

  const groups: [string, Language[]][] = [
    ["Indian languages", langs.filter((l) => l.market === "india")],
    ["International", langs.filter((l) => l.market !== "india")],
  ];
  const outOfQuota = limits && (limits.jobs_used_today >= limits.jobs_per_day || !limits.budget_left_today);

  return (
    <div className="pt-12">
      <h1 className="display text-2xl">Dub a script or episode</h1>
      <p className="mt-2 measure text-muted">
        Upload a Hindi script or audio file and pick up to {max} languages. A two-minute episode takes a few minutes; you
        can watch each language move through the pipeline.
      </p>

      <form onSubmit={submit} className="mt-10 grid gap-10 md:grid-cols-[1fr_1fr]">
        <fieldset className="space-y-5">
          <legend className="font-semibold">1. What to dub</legend>
          <label className="block">
            <span className="text-sm text-muted">Script (.json, .srt, .txt) or audio (.mp3, .wav, .m4a)</span>
            <input
              type="file"
              accept=".json,.srt,.txt,.mp3,.wav,.m4a,.aac,.ogg,.flac"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="mt-2 block w-full rounded-md border border-line bg-sheet px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-brand-soft file:px-3 file:py-1 file:text-brand"
            />
          </label>
          <p className="text-sm text-muted">
            {file ? <>Selected: <span className="text-ink">{file.name}</span>. </> : null}
            No file handy?{" "}
            <button type="button" onClick={useSample} className="text-brand underline">
              Use the sample episode
            </button>{" "}
            (an original 27-line family drama, with an original background score).
          </p>
          <label className="block">
            <span className="text-sm text-muted">Background music and effects (optional)</span>
            <input
              type="file"
              accept=".mp3,.wav,.m4a,.aac,.ogg,.flac"
              onChange={(e) => setBed(e.target.files?.[0] ?? null)}
              className="mt-2 block w-full rounded-md border border-line bg-sheet px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-brand-soft file:px-3 file:py-1 file:text-brand"
            />
            <span className="mt-1.5 block text-sm text-muted">
              {bed ? <>Selected: <span className="text-ink">{bed.name}</span>. </> : null}
              The dub is mixed over this track, and the music dips while someone speaks. Without it, background
              is separated from an uploaded audio mix where possible; a script without it is dubbed as dialogue only.
            </span>
          </label>
          <label className="block">
            <span className="text-sm text-muted">Title (optional)</span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              className="mt-2 block w-full rounded-md border border-line bg-sheet px-3 py-2"
            />
          </label>
          <details className="text-sm text-muted">
            <summary className="cursor-pointer text-ink">Script formats</summary>
            <p className="mt-2">Plain text, one line each: <code>Meera (sad): मैंने तीन साल इस परिवार की सेवा की है।</code></p>
            <p className="mt-1">SRT with the speaker before a colon, or the JSON format of the <a className="text-brand underline" href="/samples/virasat_ep01.json">sample script</a>, which can also carry characters and a glossary.</p>
            {limits && <p className="mt-1">Demo limits: {limits.max_upload_mb} MB, {limits.max_audio_seconds}s of audio, {limits.jobs_per_day} jobs a day.</p>}
          </details>
        </fieldset>

        <fieldset>
          <legend className="font-semibold">2. Languages <span className="font-normal text-muted">({picked.length}/{max})</span></legend>
          {groups.map(([name, list]) => (
            <div key={name} className="mt-4">
              <p className="text-sm text-muted">{name}</p>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {list.map((l) => {
                  const on = picked.includes(l.code);
                  return (
                    <label
                      key={l.code}
                      className={`flex cursor-pointer items-baseline justify-between rounded-md border px-3 py-2 ${on ? "border-brand bg-brand-soft" : "border-line bg-sheet hover:border-ink/40"}`}
                    >
                      <span>
                        <input type="checkbox" className="sr-only" checked={on} onChange={() => toggle(l.code)} />
                        <span className="text-lg" dir={l.direction}>{l.native_name}</span>
                      </span>
                      <span className="text-xs text-muted">{l.name}</span>
                    </label>
                  );
                })}
              </div>
            </div>
          ))}
        </fieldset>

        <div className="md:col-span-2">
          {error && <div className="mb-4"><ErrorNote>{error}</ErrorNote></div>}
          {outOfQuota && <div className="mb-4"><ErrorNote>The public demo limit for today is reached. The demo episodes are still available.</ErrorNote></div>}
          <button disabled={busy || !!outOfQuota} className="rounded-md bg-brand px-6 py-3 font-medium text-white hover:bg-ink">
            {busy ? "Uploading..." : `Start dubbing into ${picked.length} language${picked.length === 1 ? "" : "s"}`}
          </button>
          <p className="mt-3 text-xs text-muted">Server: {API}</p>
        </div>
      </form>
    </div>
  );
}
