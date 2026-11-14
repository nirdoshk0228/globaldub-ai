import Link from "next/link";

export default function NotFound() {
  return (
    <div className="pt-16">
      <h1 className="display text-2xl">This page doesn&apos;t exist</h1>
      <p className="mt-2 text-muted">The link may be old, or the job was removed.</p>
      <Link href="/demo" className="mt-6 inline-block text-brand underline">Go to the demo episodes</Link>
    </div>
  );
}
