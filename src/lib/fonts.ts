import { Cairo, El_Messiri, Inter, Playfair_Display } from "next/font/google";

export const arabicBody = Cairo({
  subsets: ["arabic"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-arabic-body",
  display: "swap",
});

export const arabicDisplay = El_Messiri({
  subsets: ["arabic"],
  weight: ["500", "600", "700"],
  variable: "--font-arabic-display",
  display: "swap",
});

export const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const displaySerif = Playfair_Display({
  subsets: ["latin"],
  weight: ["600", "700"],
  variable: "--font-display",
  display: "swap",
});
