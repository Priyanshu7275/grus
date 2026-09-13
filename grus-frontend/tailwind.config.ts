import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-inter)", "Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      colors: {
        // White base + the sky-blue sampled straight from the mascot video's
        // background gradient (#78BFD8 .. #85CFDC).
        canvas: "#F4FAFC",
        surface: "#FFFFFF",
        ink: {
          900: "#0F1E2B",
          700: "#33475A",
          500: "#45566A", // darkened from #64748B — was reading as too light/grey
          400: "#64748B", // darkened from #94A3B8 — shifted down to the old 500 value
        },
        brand: {
          sky: "#7EC8DE", // sampled from mascot background
          mid: "#3AA9C4",
          deep: "#146C8C", // for buttons / text-on-white contrast
        },
        // Clinical severity — kept red/amber/green so they never get
        // confused with the blue brand chrome around them.
        sev: {
          critical: "#F43F5E",
          criticalBg: "#FFF1F3",
          warning: "#F59E0B",
          warningBg: "#FFFBEB",
          info: "#0EA5E9",
          infoBg: "#EAF7FC",
          unknown: "#64748B",
          unknownBg: "#EEF2F4",
          ok: "#10B981",
          okBg: "#ECFDF5",
        },
      },
      borderRadius: {
        "2xl": "1rem",
        "3xl": "1.5rem",
      },
      boxShadow: {
        soft: "0 1px 2px rgba(15,40,60,0.04), 0 8px 24px rgba(15,40,60,0.06)",
        lift: "0 2px 4px rgba(15,40,60,0.05), 0 18px 40px rgba(15,40,60,0.10)",
        glow: "0 0 0 1px rgba(58,169,196,0.20), 0 12px 32px rgba(58,169,196,0.20)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: { "100%": { transform: "translateX(100%)" } },
      },
      animation: {
        "fade-up": "fade-up 0.5s ease-out both",
      },
    },
  },
  plugins: [],
};

export default config;
