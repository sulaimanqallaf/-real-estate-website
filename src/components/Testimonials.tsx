"use client";

import { Quote } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { testimonials } from "@/content/content";
import { t } from "@/lib/i18n";
import { ScrollReveal } from "./ScrollReveal";

// PLACEHOLDER CONTENT: these testimonials are illustrative only. Replace
// with real, verifiable investor quotes (with consent) before this site
// goes live — a fabricated testimonial on an investment site is both a
// marketing and an ethical liability.
export function Testimonials() {
  const { locale } = useLanguage();

  return (
    <section className="bg-navy px-5 py-20 text-cream sm:px-8 sm:py-28">
      <div className="mx-auto max-w-6xl">
        <ScrollReveal className="mx-auto max-w-2xl text-center">
          <h2 className="font-display-heading text-3xl font-bold sm:text-4xl">
            {t(testimonials.heading, locale)}
          </h2>
          <p className="mt-3 text-cream/70">{t(testimonials.subheading, locale)}</p>
        </ScrollReveal>

        <div className="mt-14 grid grid-cols-1 gap-6 md:grid-cols-3">
          {testimonials.items.map((item, i) => (
            <ScrollReveal key={i} delay={i * 0.1}>
              <div className="flex h-full flex-col rounded-2xl border border-cream/10 bg-navy-light/40 p-7">
                <Quote className="h-6 w-6 text-copper-end" strokeWidth={1.5} />
                <p className="mt-4 flex-1 text-sm leading-relaxed text-cream/85">
                  “{t(item.quote, locale)}”
                </p>
                <div className="mt-6 border-t border-cream/10 pt-4">
                  <p className="text-sm font-semibold">{t(item.name, locale)}</p>
                  <p className="text-xs text-cream/50">{t(item.since, locale)}</p>
                </div>
              </div>
            </ScrollReveal>
          ))}
        </div>
      </div>
    </section>
  );
}
