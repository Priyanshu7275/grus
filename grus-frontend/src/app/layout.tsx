import type { Metadata } from "next";
import "./globals.css";
import { ActivePatientProvider } from "@/lib/activePatientContext";
import { SourceDrawerProvider } from "@/components/SourceDrawer";
import { AppShell } from "@/components/AppShell";

// Note: intentionally using the system font stack (see --font-inter in
// globals.css) instead of next/font/google — that loader fetches from
// Google Fonts at build time, which fails on networks/CI runners without
// outbound access to fonts.googleapis.com. The system stack (San Francisco /
// Segoe UI / Roboto depending on OS) reads visually very close to Inter and
// keeps builds fully offline-safe.

export const metadata: Metadata = {
  title: "GRUS — Emergency Decision Support",
  description: "Decision support, not diagnosis.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:ital,wght@0,400;0,500;0,600;0,700;0,800&family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="app-bg min-h-screen font-sans text-ink-900 antialiased">
        <ActivePatientProvider>
          <SourceDrawerProvider>
            <AppShell>{children}</AppShell>
          </SourceDrawerProvider>
        </ActivePatientProvider>
      </body>
    </html>
  );
}
