import type { Metadata } from "next";
import Link from "next/link";
import HealthBanner from "@/components/HealthBanner";
import "./globals.css";

export const metadata: Metadata = {
  title: "TenderHawk",
  description: "AI web agents that find the government tenders a small business can actually win, and tell it why.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col font-sans">
        <header className="bg-ink text-paper">
          <div className="mx-auto max-w-6xl px-4 py-3 flex items-center gap-6">
            <Link href="/" className="text-xl font-bold tracking-tight">
              🦅 TenderHawk
            </Link>
            <nav className="flex gap-4 text-sm text-paper/80">
              <Link href="/" className="hover:text-paper">New profile</Link>
            </nav>
          </div>
        </header>
        <HealthBanner />
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{children}</main>
        <footer className="mx-auto w-full max-w-6xl px-4 py-6 text-sm text-ink-soft">
          Read-only: TenderHawk never submits bids and never bypasses captchas.
        </footer>
      </body>
    </html>
  );
}
