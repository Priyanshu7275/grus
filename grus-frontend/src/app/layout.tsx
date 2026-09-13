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
