"use client";

import { useLanguage } from "@/context/LanguageContext";
import { services } from "@/content/content";
import { t } from "@/lib/i18n";
import { iconMap } from "@/lib/icons";
import { ScrollReveal } from "./ScrollReveal";
import { SectionRule } from "./SectionRule";

export function Services() {
  const { locale } = useLanguage();

  return (
    <section className="bg-navy px-5 py-24 text-cream sm:px-8 sm:py-32">
      <div className="mx-auto max-w-6xl">
        <ScrollReveal className="mx-auto max-w-2xl text-center">
          <SectionRule className="mb-6" />
          <h2 className="font-display-heading text-3xl font-bold sm:text-4xl md:text-5xl">
            {t(services.heading, locale)}
          </h2>
          <p className="mt-4 text-cream/70">{t(services.subheading, locale)}</p>
        </ScrollReveal>

        <div className="mt-16 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {services.items.map((item, i) => {
            const Icon = iconMap[item.icon];
            return (
              <ScrollReveal key={item.icon} delay={i * 0.07}>
                <div className="glass-card h-full rounded-2xl p-7 transition-all duration-300 hover:-translate-y-1 hover:shadow-gold-lg">
                  <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-copper-gradient shadow-gold">
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
