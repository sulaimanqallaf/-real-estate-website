"use client";

import { useState } from "react";
import { useLanguage } from "@/context/LanguageContext";
import { calculator, whatsappLink } from "@/content/content";
import { t } from "@/lib/i18n";
import { ScrollReveal } from "./ScrollReveal";
import { SectionRule } from "./SectionRule";
import { WhatsappCta } from "./WhatsappCta";

const MIN = 50000;
const MAX = 1000000;
const STEP = 5000;

export function Calculator() {
  const { locale } = useLanguage();
  const [amount, setAmount] = useState(200000);
  const [propertyId, setPropertyId] = useState(calculator.propertyTypes[0].id);

  const property =
    calculator.propertyTypes.find((p) => p.id === propertyId) ??
    calculator.propertyTypes[0];
  const estimate = Math.round(amount * property.rate);
  const message = calculator.ctaMessage(amount, property.label, estimate);

  return (
    <section id="calculator" className="bg-cream px-5 py-24 text-navy sm:px-8 sm:py-32">
      <div className="mx-auto max-w-4xl">
        <ScrollReveal className="mx-auto max-w-2xl text-center">
          <SectionRule className="mb-6" />
          <h2 className="font-display-heading text-3xl font-bold sm:text-4xl md:text-5xl">
            {t(calculator.heading, locale)}
          </h2>
          <p className="mt-4 text-navy/70">{t(calculator.subheading, locale)}</p>
        </ScrollReveal>

        <ScrollReveal delay={0.1}>
          <div className="mt-14 rounded-3xl border border-navy/10 bg-white/70 p-6 shadow-gold-lg sm:p-10">
            <div className="grid gap-10 md:grid-cols-2 md:gap-14">
              <div>
                <label className="flex items-center justify-between text-sm font-semibold text-navy">
                  <span>{t(calculator.amountLabel, locale)}</span>
                  <span className="font-display-heading text-lg text-copper-start">
                    {amount.toLocaleString("en-US")}
                  </span>
                </label>
                <input
                  type="range"
                  min={MIN}
                  max={MAX}
                  step={STEP}
                  value={amount}
                  onChange={(e) => setAmount(Number(e.target.value))}
                  className="mt-4 w-full"
                  aria-label={t(calculator.amountLabel, locale)}
                />
                <div className="mt-1.5 flex justify-between text-xs text-navy/50">
                  <span>{MIN.toLocaleString("en-US")}</span>
                  <span>{MAX.toLocaleString("en-US")}</span>
                </div>

                <p className="mt-8 text-sm font-semibold text-navy">
                  {t(calculator.propertyTypeLabel, locale)}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {calculator.propertyTypes.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => setPropertyId(p.id)}
                      aria-pressed={propertyId === p.id}
                      className={`rounded-full border px-4 py-2 text-sm font-medium transition-colors ${
                        propertyId === p.id
                          ? "border-copper bg-copper-gradient text-navy-deep"
                          : "border-navy/15 text-navy/70 hover:border-copper/50"
                      }`}
                    >
                      {t(p.label, locale)}
                    </button>
                  ))}
                </div>
              </div>

              <div className="relative flex flex-col justify-between overflow-hidden rounded-2xl bg-navy p-7 text-cream">
                <div className="pointer-events-none absolute inset-0 bg-gold-radial" />
                <div className="grain-overlay" />
                <div className="relative">
                  <p className="text-sm text-cream/70">
                    {t(calculator.resultLabel, locale)}
                  </p>
                  <p className="copper-text mt-2 font-display-heading text-4xl font-bold sm:text-5xl">
                    {estimate.toLocaleString("en-US")}{" "}
                    <span className="text-xl font-normal text-cream/70">
                      {locale === "ar" ? "د.ك" : "KWD"}
                    </span>
                  </p>
                  <p className="mt-3 text-xs leading-relaxed text-cream/50">
                    {t(calculator.disclaimer, locale)}
                  </p>
                </div>
                <WhatsappCta
                  href={whatsappLink(message, locale)}
                  className="mt-8 w-full justify-center"
                >
                  {t(calculator.ctaLabel, locale)}
                </WhatsappCta>
              </div>
            </div>
          </div>
        </ScrollReveal>
      </div>
    </section>
  );
}
