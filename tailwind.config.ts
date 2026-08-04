import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        navy: {
          DEFAULT: "#0A0F1E",
          light: "#16213D",
          deep: "#05070D",
        },
        cream: {
          DEFAULT: "#F7F4EE",
          dark: "#EDE8DD",
        },
        copper: {
          deep: "#7A5A22",
          start: "#B8873A",
          end: "#F0D68C",
          bright: "#FBEFD1",
          DEFAULT: "#C9A15A",
        },
        olive: {
          DEFAULT: "#3D4A3A",
          light: "#4E5D49",
        },
      },
      fontFamily: {
        "arabic-display": ["var(--font-arabic-display)", "serif"],
        "arabic-body": ["var(--font-arabic-body)", "sans-serif"],
        "display": ["var(--font-display)", "serif"],
        "sans": ["var(--font-inter)", "sans-serif"],
      },
      backgroundImage: {
        // Brushed-metal sweep (dark bronze edges, bright core) rather than
        // a flat two-stop fill — reads as poured metal, not a flat tint.
        "copper-gradient":
          "linear-gradient(135deg, #7A5A22 0%, #B8873A 22%, #F0D68C 50%, #B8873A 78%, #7A5A22 100%)",
        "gold-radial":
          "radial-gradient(60% 60% at 50% 40%, rgba(240,214,140,0.16) 0%, rgba(240,214,140,0) 70%)",
        "grain":
          "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.05'/%3E%3C/svg%3E\")",
      },
      boxShadow: {
        gold: "0 8px 30px -8px rgba(201,161,90,0.35)",
        "gold-lg": "0 20px 60px -12px rgba(201,161,90,0.3)",
      },
      letterSpacing: {
        eyebrow: "0.2em",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shine: {
          "0%": { transform: "translateX(-130%) skewX(-12deg)" },
          "100%": { transform: "translateX(130%) skewX(-12deg)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.6s ease-out forwards",
        shine: "shine 1.1s ease-in-out",
      },
    },
  },
  plugins: [],
};
export default config;
