"use client";

import { useLanguage } from "@/context/LanguageContext";
import { finalCta, whatsappLink } from "@/content/content";
import { t } from "@/lib/i18n";
import { SkylineBackdrop } from "./Placeholder";
import { ScrollReveal } from "./ScrollReveal";
import { WhatsappCta } from "./WhatsappCta";

export function FinalCta() {
  const { locale } = useLanguage();

  return (
    <section className="relative overflow-hidden px-5 py-24 text-center text-cream sm:px-8 sm:py-32">
      <SkylineBackdrop />
      <div className="absolute inset-0 bg-navy-deep/50" aria-hidden="true" />

      <div className="relative mx-auto max-w-2xl">
        <ScrollReveal>
          <h2 className="font-display-heading text-balance text-3xl font-bold sm:text-4xl">
            {t(finalCta.heading, locale)}
          </h2>
          <p className="mt-4 text-balance text-cream/80">
            {t(finalCta.subheading, locale)}
          </p>
          <div className="mt-9 flex justify-center">
            <WhatsappCta href={whatsappLink(finalCta.ctaMessage, locale)}>
              {t(finalCta.cta, locale)}
            </WhatsappCta>
          </div>
        </ScrollReveal>
      </div>
    </section>
  );
}
