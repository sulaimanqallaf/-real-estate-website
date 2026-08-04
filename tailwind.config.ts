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
          DEFAULT: "#0B1220",
          light: "#141F35",
          deep: "#070B14",
        },
        cream: {
          DEFAULT: "#F7F4EE",
          dark: "#EDE8DD",
        },
        copper: {
          start: "#C9A15A",
          end: "#E8C77E",
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
        "copper-gradient": "linear-gradient(135deg, #C9A15A 0%, #E8C77E 100%)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.6s ease-out forwards",
      },
    },
  },
  plugins: [],
};
export default config;
