"use client";

import { useLanguage } from "@/context/LanguageContext";
import { whyDubai } from "@/content/content";
import { t } from "@/lib/i18n";
import { iconMap } from "@/lib/icons";
import { ScrollReveal } from "./ScrollReveal";

export function WhyDubai() {
  const { locale } = useLanguage();

  return (
    <section className="bg-cream px-5 py-20 text-navy sm:px-8 sm:py-28">
      <div className="mx-auto max-w-6xl">
        <ScrollReveal className="mx-auto max-w-2xl text-center">
          <h2 className="font-display-heading text-3xl font-bold sm:text-4xl">
            {t(whyDubai.heading, locale)}
          </h2>
          <p className="mt-3 text-navy/70">{t(whyDubai.subheading, locale)}</p>
        </ScrollReveal>

        <div className="mt-14 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {whyDubai.items.map((item, i) => {
            const Icon = iconMap[item.icon];
            return (
              <ScrollReveal key={item.icon} delay={i * 0.08}>
                <div className="h-full rounded-2xl border border-navy/8 bg-white/60 p-7 shadow-sm transition-shadow hover:shadow-md">
                  <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-olive/10">
                    <Icon className="h-6 w-6 text-olive" strokeWidth={1.75} />
                  </div>
                  <h3 className="mt-5 font-display-heading text-lg font-semibold">
                    {t(item.title, locale)}
                  </h3>
                  <p className="mt-2 text-sm leading-relaxed text-navy/70">
                    {t(item.description, locale)}
                  </p>
                </div>
              </ScrollReveal>
            );
          })}
        </div>
      </div>
    </section>
  );
}
