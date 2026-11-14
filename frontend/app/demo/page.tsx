"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, fmtTime, Job, myJobs } from "@/lib/api";
import { ErrorNote, LangName, StatusPill } from "@/components/ui";

function JobRow({ j }: { j: Job }) {
  return (
    <li className="border-b border-line py-5">
      <div className="flex flex-wrap items-baseline gap-3">
        <Link href={`/jobs/${j.id}`} className="text-lg font-semibold text-ink hover:text-brand">
          {j.title}
        </Link>
        <StatusPill status={j.status} />
        <span className="text-sm text-muted">
          {j.lines} lines, {fmtTime(j.runtime_seconds)}
        </span>
      </div>
      {j.synopsis && <p className="mt-1 measure text-sm text-muted">{j.synopsis}</p>}
      <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {j.target_langs.map((l) => (
          <LangName key={l} code={l} />
        ))}
      </p>
    </li>
  );
}

export default function Demo() {
  const [gallery, setGallery] = useState<Job[] | null>(null);
  const [mine, setMine] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Job[]>("/api/gallery").then(setGallery).catch((e) => setError(e.message));
    const ids = myJobs();
    if (ids.length) api<Job[]>(`/api/jobs?ids=${ids.join(",")}`).then(setMine).catch(() => {});
  }, []);

  return (
    <div className="pt-12">
      <h1 className="display text-2xl">Demo episodes</h1>
      <p className="mt-2 measure text-muted">
        Finished episodes you can play in every language, with the QA flags and the reviewer&apos;s view intact.
      </p>
      {error && <div className="mt-6"><ErrorNote>{error}</ErrorNote></div>}
      {gallery && gallery.length === 0 && (
        <p className="mt-8 text-muted">
          No demo episodes are published yet. <Link href="/new" className="text-brand underline">Dub the sample script</Link> to see the pipeline run.
        </p>
      )}
      {gallery === null && !error && <p className="mt-8 text-muted">Loading episodes...</p>}
      <ul className="mt-6">{gallery?.map((j) => <JobRow key={j.id} j={j} />)}</ul>

      {mine.length > 0 && (
        <>
          <h2 className="display mt-14 text-xl">Your jobs</h2>
          <p className="mt-1 text-sm text-muted">Jobs started from this browser. Only this browser can edit them.</p>
          <ul className="mt-2">{mine.map((j) => <JobRow key={j.id} j={j} />)}</ul>
        </>
      )}
    </div>
  );
}
