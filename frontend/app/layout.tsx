import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "GlobalDub AI - one Hindi episode, eight languages",
  description:
    "Agentic dubbing for Indian audio drama: culturally adapted dialogue, consistent character voices, and a QA agent that finds localization errors before a reviewer does.",
};

const FONTS =
  "https://fonts.googleapis.com/css2?family=Anek+Latin:wdth,wght@75..125,300..800&family=Anek+Devanagari:wdth,wght@75..125,300..800&family=Anek+Tamil:wdth,wght@75..125,300..800&family=Anek+Telugu:wdth,wght@75..125,300..800&family=Anek+Bangla:wdth,wght@75..125,300..800&family=Noto+Naskh+Arabic:wght@400..700&display=swap";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link rel="stylesheet" href={FONTS} />
      </head>
      <body className="min-h-screen">
        <header className="border-b border-line bg-paper/90 backdrop-blur sticky top-0 z-20">
          <nav className="mx-auto flex max-w-6xl items-center gap-5 overflow-x-auto whitespace-nowrap px-5 py-3 text-sm">
            <Link href="/" className="display text-lg text-ink no-underline">
              GlobalDub
            </Link>
            <Link href="/demo" className="text-muted hover:text-ink">
              Demo episodes
            </Link>
            <Link href="/new" className="text-muted hover:text-ink">
              Dub a script
            </Link>
            <Link href="/benchmark" className="text-muted hover:text-ink">
              QA benchmark
            </Link>
            <a
              href="https://github.com/nirdoshk0228/globaldub-ai"
              className="ml-auto hidden text-muted hover:text-ink sm:inline"
              target="_blank"
              rel="noreferrer"
            >
              Source code
            </a>
          </nav>
        </header>
        <main className="mx-auto max-w-6xl px-5 pb-24">{children}</main>
        <footer className="border-t border-line py-8 text-center text-xs text-muted">
          Built by Nirdosh Kumar. Demo audio is from original scripts written for this project; use only audio you have rights to.
        </footer>
      </body>
    </html>
  );
}
