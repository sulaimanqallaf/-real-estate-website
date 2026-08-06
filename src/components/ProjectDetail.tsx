"use client";

import { ArrowRight, ArrowLeft, Building2, Calendar, MapPin, Wallet } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { projectDetail } from "@/content/content";
import type { ProjectDetail as ProjectDetailType } from "@/content/projectsData";
import { t } from "@/lib/i18n";
import { withBasePath } from "@/lib/basePath";
import { ProjectInquiryForm } from "./ProjectInquiryForm";
import { ProjectImagePlaceholder } from "./Placeholder";
import { ScrollReveal } from "./ScrollReveal";
import { SectionRule } from "./SectionRule";

function formatPrice(amount: number, locale: "ar" | "en") {
  const formatted = amount.toLocaleString("en-US");
  return locale === "ar" ? `${formatted} درهم` : `AED ${formatted}`;
}

export function ProjectDetail({ project }: { project: ProjectDetailType }) {
  const { locale } = useLanguage();
  const BackArrow = locale === "ar" ? ArrowRight : ArrowLeft;

  return (
    <section className="bg-cream px-5 pb-24 pt-36 text-navy sm:px-8 sm:pb-32 sm:pt-44">
      <div className="mx-auto max-w-5xl">
        <a
          href={`${withBasePath("/")}#projects`}
          className="inline-flex items-center gap-2 text-sm font-semibold text-navy/60 transition-colors hover:text-copper-end"
        >
          <BackArrow className="h-4 w-4" />
          {t(projectDetail.backToProjects, locale)}
        </a>

        <ScrollReveal className="mt-6">
          <SectionRule className="mb-6" />
          <h1 className="font-display-heading text-3xl font-bold sm:text-4xl md:text-5xl">
            {t(project.title, locale)}
          </h1>
          <p className="mt-3 flex items-center gap-1.5 text-navy/60">
            <MapPin className="h-4 w-4 text-copper-end" />
            {t(project.location, locale)}
          </p>
          <p className="mt-6 max-w-2xl text-navy/70">{t(project.description, locale)}</p>
        </ScrollReveal>

        <ScrollReveal delay={0.1} className="mt-10">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Array.from({ length: project.gallerySlots }).map((_, i) => (
              <ProjectImagePlaceholder key={i} className="aspect-[4/3] rounded-2xl" />
            ))}
          </div>
          <p className="mt-3 text-center text-xs text-navy/45">
            {t(projectDetail.photosComingSoon, locale)}
          </p>
        </ScrollReveal>

        <div className="mt-12 grid gap-10 lg:grid-cols-[1.1fr_0.9fr]">
          <ScrollReveal delay={0.15}>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-2xl border border-navy/10 bg-white/70 p-5">
                <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-navy/50">
                  <Building2 className="h-4 w-4 text-copper-end" />
                  {t(projectDetail.developer, locale)}
                </p>
                <p className="mt-1.5 text-sm font-semibold text-navy">
                  {t(project.developer, locale)}
                </p>
              </div>
              <div className="rounded-2xl border border-navy/10 bg-white/70 p-5">
                <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-navy/50">
                  <Wallet className="h-4 w-4 text-copper-end" />
                  {t(projectDetail.priceFrom, locale)}
                </p>
                <p className="mt-1.5 text-sm font-semibold text-navy">
                  {project.priceFrom
                    ? formatPrice(project.priceFrom.amount, locale)
                    : t(projectDetail.brochureComingSoon, locale)}
                </p>
                {project.priceFrom && (
                  <p className="mt-1 text-xs text-navy/45">
                    {t(projectDetail.priceEstimateNote, locale)}
                  </p>
                )}
              </div>
              <div className="rounded-2xl border border-navy/10 bg-white/70 p-5 sm:col-span-2">
                <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-navy/50">
                  <Calendar className="h-4 w-4 text-copper-end" />
                  {t(projectDetail.deliveryDate, locale)}
                </p>
                <p className="mt-1.5 text-sm font-semibold text-navy">
                  {t(project.deliveryDate, locale)}
                </p>
              </div>
            </div>

            <div className="mt-8">
              <h2 className="font-display-heading text-lg font-semibold text-navy">
                {t(projectDetail.paymentPlan, locale)}
              </h2>
              <ul className="mt-3 space-y-2">
                {project.paymentPlan.map((step, i) => (
                  <li
                    key={i}
                    className="flex items-center gap-3 rounded-xl border border-navy/10 bg-white/70 px-4 py-3 text-sm text-navy/80"
                  >
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-copper-gradient text-xs font-bold text-navy-deep">
                      {i + 1}
                    </span>
                    {t(step, locale)}
                  </li>
                ))}
              </ul>
            </div>

            <div className="mt-8">
              <h2 className="font-display-heading text-lg font-semibold text-navy">
                {t(projectDetail.features, locale)}
              </h2>
              <ul className="mt-3 grid gap-2 sm:grid-cols-2">
                {project.features.map((feature, i) => (
                  <li
                    key={i}
                    className="rounded-xl border border-navy/10 bg-white/70 px-4 py-3 text-sm text-navy/80"
                  >
                    {t(feature, locale)}
                  </li>
                ))}
              </ul>
            </div>

            <div className="mt-8 rounded-2xl border border-navy/10 bg-white/70 p-5">
              <h2 className="font-display-heading text-sm font-semibold text-navy">
                {t(projectDetail.brochure, locale)}
              </h2>
              {project.brochureUrl ? (
                <a
                  href={project.brochureUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2 inline-block text-sm font-semibold text-copper-end hover:underline"
                >
                  {t(projectDetail.downloadBrochure, locale)}
                </a>
              ) : (
                <p className="mt-2 text-sm text-navy/50">
                  {t(projectDetail.brochureComingSoon, locale)}
                </p>
              )}
            </div>
          </ScrollReveal>

          <ScrollReveal delay={0.2}>
            <div className="lg:sticky lg:top-28">
              <h2 className="font-display-heading text-xl font-semibold text-navy">
                {t(projectDetail.interestedHeading, locale)}
              </h2>
              <p className="mt-1.5 text-sm text-navy/60">
                {t(projectDetail.interestedSubheading, locale)}
              </p>
              <div className="mt-5">
                <ProjectInquiryForm defaultProjectSlug={project.slug} />
              </div>
            </div>
          </ScrollReveal>
        </div>
      </div>
    </section>
  );
}
