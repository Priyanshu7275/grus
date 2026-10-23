import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-inter)", "Plus Jakarta Sans", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ["Instrument Serif", "ui-serif", "Georgia", "serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      colors: {
        // Clean white base + deep navy and royal blue (medical-site look).
        canvas: "#F6F7FB",
        surface: "#FFFFFF",
        ink: {
          900: "#121A47",
          700: "#343F6B",
          500: "#5A648C",
          400: "#7D86A8",
        },
        brand: {
          sky: "#8FB0FF",
          mid: "#3D6BE6",
          deep: "#2446B8", // buttons / text-on-white
          ink: "#13195C", // dark navy surfaces
        },
        leaf: {
          DEFAULT: "#3D6BE6",
          soft: "#EEF2FF",
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
        soft: "0 1px 2px rgba(19,25,92,0.04), 0 10px 30px rgba(19,25,92,0.06)",
        lift: "0 2px 6px rgba(19,25,92,0.06), 0 24px 56px rgba(19,25,92,0.12)",
        glow: "0 0 0 1px rgba(61,107,230,0.22), 0 14px 34px rgba(61,107,230,0.25)",
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
