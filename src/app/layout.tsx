import type { Metadata, Viewport } from "next";
import { arabicBody, arabicDisplay, displaySerif, inter } from "@/lib/fonts";
import { LanguageProvider } from "@/context/LanguageContext";
import { withBasePath } from "@/lib/basePath";
import "./globals.css";

const siteUrl = "https://sulaimanqallaf.github.io/-real-estate-website";
const title = "Altiva Real Estate | ألتيفا العقارية";
const description =
  "Altiva Real Estate connects Kuwaiti investors with the finest real estate opportunities in Dubai. | ألتيفا العقارية تربط المستثمرين الكويتيين بأفضل الفرص العقارية في دبي.";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title,
  description,
  keywords: [
    "Altiva Real Estate",
    "Dubai real estate investment",
    "Kuwait investors Dubai",
    "عقارات دبي",
    "استثمار عقاري دبي",
  ],
  icons: {
    icon: withBasePath("/favicon.png"),
    apple: withBasePath("/apple-icon.png"),
  },
  openGraph: {
    title,
    description,
    url: siteUrl,
    siteName: "Altiva Real Estate",
    locale: "ar_KW",
    alternateLocale: "en_US",
    type: "website",
    images: [{ url: `${siteUrl}/og-image.png`, width: 1200, height: 630 }],
  },
  twitter: {
    card: "summary_large_image",
    title,
    description,
    images: [`${siteUrl}/og-image.png`],
  },
};

export const viewport: Viewport = {
  themeColor: "#0B1220",
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
