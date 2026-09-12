"use client";

import { useEffect, useState, ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { LogOut, LogIn } from "lucide-react";
import { GrusLogo } from "./GrusLogo";
import { isLoggedIn, logout } from "@/lib/auth";
import { ChatWidget } from "./ChatWidget";
import { cn } from "@/lib/utils";

const PUBLIC_PATHS = ["/", "/login"];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.includes(pathname);
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    if (isPublic) {
      setReady(true);
      return;
    }
    const ok = isLoggedIn();
    setAuthed(ok);
    if (!ok) {
      router.replace("/login");
    } else {
      setReady(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, isPublic]);

  // Public pages (landing, login) render full-bleed, no navbar chrome —
  // those pages build their own header/hero.
  if (isPublic) return <>{children}</>;

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="font-mono text-xs text-ink-400">loading…</div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col">
      <Navbar authed={authed} />
      <main className="flex-1">{children}</main>
      {authed && <ChatWidget />}
    </div>
  );
}

function Navbar({ authed }: { authed: boolean }) {
  const pathname = usePathname();
  const router = useRouter();

  return (
    <header className="sticky top-0 z-40 w-full">
      <div className="glass border-b border-ink-900/5">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6">
          <Link href="/patients" aria-label="GRUS home">
            <GrusLogo />
          </Link>

          <nav className="hidden items-center gap-1 sm:flex">
            <NavLink href="/patients" active={pathname?.startsWith("/patients") ?? false}>
              Patients
            </NavLink>
            <NavLink href="/governance" active={pathname?.startsWith("/governance") ?? false}>
              Governance
            </NavLink>
          </nav>

          <div className="flex items-center gap-3">
            {authed ? (
              <button
                onClick={() => {
                  logout();
                  router.push("/login");
                }}
                className="inline-flex items-center gap-1.5 rounded-full border border-ink-900/10 px-4 py-2 text-sm font-medium text-ink-700 transition hover:bg-ink-900/5"
              >
                <LogOut className="h-4 w-4" />
                Sign out
              </button>
            ) : (
              <Link
                href="/login"
                className="inline-flex items-center gap-1.5 rounded-full brand-gradient px-5 py-2 text-sm font-semibold text-white shadow-soft transition hover:shadow-glow"
              >
                <LogIn className="h-4 w-4" />
                Log in
              </Link>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}

function NavLink({ href, active, children }: { href: string; active: boolean; children: ReactNode }) {
  return (
    <Link
      href={href}
      className={cn(
        "rounded-full px-3.5 py-1.5 text-sm font-medium transition",
        active ? "bg-ink-900/5 text-ink-900" : "text-ink-500 hover:text-ink-900"
      )}
    >
      {children}
    </Link>
  );
}
