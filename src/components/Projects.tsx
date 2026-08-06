"use client";

import Link from "next/link";
import { MapPin } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { projects, whatsappLink } from "@/content/content";
import { projectsData } from "@/content/projectsData";
import { t } from "@/lib/i18n";
import { withBasePath } from "@/lib/basePath";
import { ProjectImagePlaceholder } from "./Placeholder";
import { ScrollReveal } from "./ScrollReveal";
import { SectionRule } from "./SectionRule";

function formatPrice(amount: number, locale: "ar" | "en") {
  const formatted = amount.toLocaleString("en-US");
  return locale === "ar" ? `${formatted} درهم` : `AED ${formatted}`;
}

export function Projects() {
  const { locale } = useLanguage();

  return (
    <section id="projects" className="bg-navy px-5 py-24 text-cream sm:px-8 sm:py-32">
      <div className="mx-auto max-w-6xl">
        <ScrollReveal className="mx-auto max-w-2xl text-center">
          <SectionRule className="mb-6" />
          <h2 className="font-display-heading text-3xl font-bold sm:text-4xl md:text-5xl">
            {t(projects.heading, locale)}
          </h2>
          <p className="mt-4 text-cream/70">{t(projects.subheading, locale)}</p>
        </ScrollReveal>

        <div className="mt-16 grid grid-cols-1 gap-7 sm:grid-cols-2 lg:grid-cols-3">
          {projectsData.map((project, i) => (
            <ScrollReveal key={project.slug} delay={i * 0.1}>
              <Link
                href={withBasePath(`/projects/${project.slug}`)}
                className="group block overflow-hidden rounded-2xl border border-cream/10 bg-navy-light/40 transition-all duration-300 hover:-translate-y-1 hover:border-copper/40 hover:shadow-gold-lg"
              >
                <div className="relative">
                  <ProjectImagePlaceholder className="aspect-[4/3] w-full" />
                  <span className="absolute end-4 top-4 rounded-full bg-copper-gradient px-3 py-1 text-xs font-semibold text-navy-deep shadow-gold">
                    {t(projects.comingSoon, locale)}
                  </span>
                </div>
                <div className="p-6">
                  <h3 className="font-display-heading text-lg font-semibold">
                    {t(project.title, locale)}
                  </h3>
                  <p className="mt-1.5 flex items-center gap-1.5 text-sm text-cream/60">
                    <MapPin className="h-3.5 w-3.5 text-copper-end" />
                    {t(project.location, locale)}
                  </p>
                  {project.priceFrom && (
                    <p className="mt-3 text-sm font-semibold text-copper-end">
                      {t(projects.startingFrom, locale)}{" "}
                      {formatPrice(project.priceFrom.amount, locale)}
                    </p>
                  )}
                  <span className="mt-4 inline-flex items-center text-xs font-semibold text-cream/70 transition-colors group-hover:text-copper-end">
                    {t(projects.viewDetails, locale)}
                  </span>
                </div>
              </Link>
            </ScrollReveal>
          ))}
        </div>

        <div className="mt-12 text-center">
          <a
            href={whatsappLink(undefined, locale)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center rounded-full border border-copper/50 px-6 py-3 text-sm font-semibold text-cream transition-all duration-300 hover:border-copper hover:bg-copper/10 hover:shadow-gold"
          >
            {t(projects.viewAll, locale)}
          </a>
        </div>
      </div>
    </section>
  );
}
