"use client";

import { useLanguage } from "@/context/LanguageContext";
import { stats } from "@/content/content";
import { t } from "@/lib/i18n";
import { iconMap } from "@/lib/icons";
import { AnimatedCounter } from "./AnimatedCounter";
import { ScrollReveal } from "./ScrollReveal";
import { SkylineToChart } from "./SkylineToChart";

export function Stats() {
  const { locale } = useLanguage();

  return (
    <section className="bg-cream px-5 pb-20 pt-4 text-navy sm:px-8 sm:pb-28">
      <SkylineToChart className="mb-10 opacity-80" />
      <div className="mx-auto max-w-6xl">
        <div className="grid grid-cols-1 gap-8 sm:grid-cols-2 lg:grid-cols-4">
          {stats.items.map((item, i) => {
            const Icon = iconMap[item.icon];
            return (
              <ScrollReveal key={item.icon} delay={i * 0.08}>
                <div className="flex flex-col items-center text-center">
                  <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-olive/10">
                    <Icon className="h-6 w-6 text-olive" strokeWidth={1.75} />
                  </div>
                  <p className="mt-4 font-display-heading text-4xl font-bold text-navy sm:text-5xl">
                    <AnimatedCounter value={item.value} suffix={item.suffix} />
                  </p>
                  <p className="mt-2 max-w-[220px] text-sm leading-relaxed text-navy/70">
                    {t(item.label, locale)}
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
