import type { Metadata } from "next";
import { arabicBody, arabicDisplay, displaySerif, inter } from "@/lib/fonts";
import { LanguageProvider } from "@/context/LanguageContext";
import "./globals.css";

export const metadata: Metadata = {
  title: "Altiva Real Estate | ألتيفا العقارية",
  description:
    "Altiva Real Estate connects Kuwaiti investors with the finest real estate opportunities in Dubai. | ألتيفا العقارية تربط المستثمرين الكويتيين بأفضل الفرص العقارية في دبي.",
};

const themeInitScript = `
(function() {
  try {
    var stored = window.localStorage.getItem('altiva-locale');
    var locale = stored === 'en' ? 'en' : 'ar';
    document.documentElement.lang = locale;
    document.documentElement.dir = locale === 'ar' ? 'rtl' : 'ltr';
  } catch (e) {}
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ar" dir="rtl">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body
        className={`${arabicBody.variable} ${arabicDisplay.variable} ${inter.variable} ${displaySerif.variable} bg-navy text-cream antialiased`}
      >
        <LanguageProvider>{children}</LanguageProvider>
      </body>
    </html>
  );
}
