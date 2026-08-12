"use client";

import { useLanguage } from "@/context/LanguageContext";
import { about } from "@/content/content";
import { team } from "@/content/team";
import { t } from "@/lib/i18n";
import { withBasePath } from "@/lib/basePath";
import { AvatarPlaceholder } from "./Placeholder";
import { ScrollReveal } from "./ScrollReveal";
import { SectionRule } from "./SectionRule";

export function About() {
  const { locale } = useLanguage();

  return (
    <section id="about" className="bg-cream px-5 py-24 text-navy sm:px-8 sm:py-32">
      <div className="mx-auto max-w-6xl">
        <ScrollReveal className="mx-auto max-w-3xl text-center">
          <SectionRule className="mb-6" />
          <h2 className="font-display-heading text-3xl font-bold sm:text-4xl md:text-5xl">
            {t(about.heading, locale)}
          </h2>
          <p className="mt-5 text-balance leading-relaxed text-navy/70">
            {t(about.intro, locale)}
          </p>
        </ScrollReveal>

        <ScrollReveal delay={0.1}>
          <div className="mt-20 flex items-center justify-center gap-3">
            <h3 className="text-center font-display-heading text-xl font-semibold text-navy/90">
              {t(about.teamHeading, locale)}
            </h3>
          </div>
        </ScrollReveal>

        <div className="mt-8 grid grid-cols-2 gap-6 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-7">
          {team.map((member, i) => (
            <ScrollReveal key={member.id} delay={Math.min(i * 0.05, 0.4)}>
              <div className="flex flex-col items-center text-center">
                {member.avatar ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={withBasePath(member.avatar)}
                    alt={t(member.name, locale)}
                    className="h-20 w-20 rounded-full object-cover ring-1 ring-copper/30 transition-shadow duration-300 hover:shadow-gold hover:ring-copper/60 sm:h-24 sm:w-24"
                  />
                ) : (
                  <AvatarPlaceholder className="h-20 w-20 sm:h-24 sm:w-24" />
                )}
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
