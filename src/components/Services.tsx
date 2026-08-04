"use client";

import { useLanguage } from "@/context/LanguageContext";
import { services } from "@/content/content";
import { t } from "@/lib/i18n";
import { iconMap } from "@/lib/icons";
import { ScrollReveal } from "./ScrollReveal";

export function Services() {
  const { locale } = useLanguage();

  return (
    <section className="bg-navy px-5 py-20 text-cream sm:px-8 sm:py-28">
      <div className="mx-auto max-w-6xl">
        <ScrollReveal className="mx-auto max-w-2xl text-center">
          <h2 className="font-display-heading text-3xl font-bold sm:text-4xl">
            {t(services.heading, locale)}
          </h2>
          <p className="mt-3 text-cream/70">{t(services.subheading, locale)}</p>
        </ScrollReveal>

        <div className="mt-14 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {services.items.map((item, i) => {
            const Icon = iconMap[item.icon];
            return (
              <ScrollReveal key={item.icon} delay={i * 0.07}>
                <div className="h-full rounded-2xl border border-cream/10 bg-navy-light/40 p-7">
                  <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-copper-gradient">
                    <Icon className="h-6 w-6 text-navy-deep" strokeWidth={1.75} />
                  </div>
                  <h3 className="mt-5 font-display-heading text-lg font-semibold">
                    {t(item.title, locale)}
                  </h3>
                  <p className="mt-2 text-sm leading-relaxed text-cream/70">
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
