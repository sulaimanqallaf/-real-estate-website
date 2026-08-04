"use client";

import { useLanguage } from "@/context/LanguageContext";
import { about } from "@/content/content";
import { team } from "@/content/team";
import { t } from "@/lib/i18n";
import { AvatarPlaceholder } from "./Placeholder";
import { ScrollReveal } from "./ScrollReveal";

export function About() {
  const { locale } = useLanguage();

  return (
    <section id="about" className="bg-cream px-5 py-20 text-navy sm:px-8 sm:py-28">
      <div className="mx-auto max-w-6xl">
        <ScrollReveal className="mx-auto max-w-3xl text-center">
          <h2 className="font-display-heading text-3xl font-bold sm:text-4xl">
            {t(about.heading, locale)}
          </h2>
          <p className="mt-4 text-balance leading-relaxed text-navy/70">
            {t(about.intro, locale)}
          </p>
        </ScrollReveal>

        <ScrollReveal delay={0.1}>
          <h3 className="mt-16 text-center font-display-heading text-xl font-semibold text-navy/90">
            {t(about.teamHeading, locale)}
          </h3>
        </ScrollReveal>

        <div className="mt-8 grid grid-cols-2 gap-6 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-7">
          {team.map((member, i) => (
            <ScrollReveal key={member.id} delay={Math.min(i * 0.05, 0.4)}>
              <div className="flex flex-col items-center text-center">
                <AvatarPlaceholder className="h-20 w-20 sm:h-24 sm:w-24" />
                <p className="mt-3 text-sm font-semibold text-navy">
                  {t(member.name, locale)}
                </p>
                <p className="mt-0.5 text-xs text-navy/60">{t(member.title, locale)}</p>
              </div>
            </ScrollReveal>
          ))}
        </div>
      </div>
    </section>
  );
}
