"use client";

import { useLanguage } from "@/context/LanguageContext";
import { partners } from "@/content/content";
import { t } from "@/lib/i18n";
import { LogoPlaceholder } from "./Placeholder";
import { ScrollReveal } from "./ScrollReveal";

const placeholderKeys = Array.from({ length: partners.count }, (_, i) => i);

export function Partners() {
  const { locale } = useLanguage();
  const label = t(partners.placeholderLabel, locale);

  return (
    <section className="overflow-hidden bg-navy px-5 py-16 sm:px-8 sm:py-20">
      <div className="mx-auto max-w-6xl">
        <ScrollReveal className="flex items-center justify-center gap-3 text-center">
          <h2 className="font-display-heading text-xl font-semibold text-cream sm:text-2xl">
            {t(partners.heading, locale)}
          </h2>
          <span className="rounded-full bg-copper-gradient px-3 py-1 text-xs font-semibold text-navy-deep shadow-gold">
            {t(partners.pending, locale)}
          </span>
        </ScrollReveal>
      </div>

      <div className="relative mt-10 [mask-image:linear-gradient(to_right,transparent,black_10%,black_90%,transparent)]">
        <div className="flex w-max animate-marquee gap-6">
          {[...placeholderKeys, ...placeholderKeys].map((key, i) => (
            <LogoPlaceholder key={i} label={label} />
          ))}
        </div>
      </div>
    </section>
  );
}
