export type Locale = "ar" | "en";

export type Bilingual = {
  ar: string;
  en: string;
};

export function t(field: Bilingual, locale: Locale): string {
  return field[locale];
}
