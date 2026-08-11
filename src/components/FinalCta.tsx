"use client";

import { useLanguage } from "@/context/LanguageContext";
import { finalCta } from "@/content/content";
import { t } from "@/lib/i18n";
import { withBasePath } from "@/lib/basePath";
import { SkylineBackdrop } from "./Placeholder";
import { ScrollReveal } from "./ScrollReveal";
import { SectionRule } from "./SectionRule";

// Not withBasePath()'d through next/link (only Projects.tsx/ProjectDetail.tsx
// use that) — a plain <a>, so it needs the manual prefix like Header.tsx's
// equivalent consultation link.
const consultationHref = withBasePath("/consultation");

export function FinalCta() {
  const { locale } = useLanguage();

  return (
    <section className="relative overflow-hidden px-5 py-28 text-center text-cream sm:px-8 sm:py-36">
      <SkylineBackdrop />
      <div className="absolute inset-0 bg-navy-deep/50" aria-hidden="true" />

      <div className="relative mx-auto max-w-2xl">
        <ScrollReveal>
          <SectionRule className="mb-6" />
          <h2 className="font-display-heading text-balance text-3xl font-bold sm:text-4xl md:text-5xl">
            {t(finalCta.heading, locale)}
          </h2>
          <p className="mt-4 text-balance text-cream/80">
            {t(finalCta.subheading, locale)}
          </p>
          <div className="mt-9 flex justify-center">
            <a
              href={consultationHref}
              className="btn-shine inline-flex items-center gap-2.5 rounded-full bg-copper-gradient bg-[length:200%_100%] px-6 py-3.5 text-sm font-semibold text-navy-deep shadow-gold transition-all duration-200 hover:scale-[1.02] hover:shadow-gold-lg focus-visible:scale-[1.02] sm:text-base"
            >
              {t(finalCta.cta, locale)}
            </a>
          </div>
        </ScrollReveal>
      </div>
    </section>
  );
}
