"use client";

import { useState, type FormEvent } from "react";
import { MessageCircle } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import {
  consultation,
  consultationMessage,
  whatsappLink,
  type ConsultationFormData,
  type FieldOption,
} from "@/content/content";
import { t, type Bilingual } from "@/lib/i18n";
import { ZOHO_WEB_TO_LEAD } from "@/lib/zoho";
import { ScrollReveal } from "./ScrollReveal";
import { SectionRule } from "./SectionRule";

const REQUIRED_FIELDS = [
  "fullName",
  "phone",
  "serviceType",
  "location",
  "contactMethod",
] as const satisfies readonly (keyof ConsultationFormData)[];

const EMPTY_DATA: ConsultationFormData = {
  fullName: "",
  phone: "",
  email: "",
  serviceType: "",
  location: "",
  budget: "",
  timeline: "",
  contactMethod: "",
  notes: "",
};

const ZOHO_IFRAME_NAME = "zoho-consultation-target";

function Tag({ required, locale }: { required: boolean; locale: "ar" | "en" }) {
  return (
    <span className={`text-xs font-normal ${required ? "text-copper-start" : "text-navy/40"}`}>
      {" "}
      ({t(required ? consultation.requiredTag : consultation.optionalTag, locale)})
    </span>
  );
}

export function ConsultationForm() {
  const { locale } = useLanguage();
  const [data, setData] = useState<ConsultationFormData>(EMPTY_DATA);
  const [attempted, setAttempted] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const errors = REQUIRED_FIELDS.filter((field) => data[field].trim() === "");

  const update = (field: keyof ConsultationFormData) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>
  ) => setData((prev) => ({ ...prev, [field]: e.target.value }));

  const handleSubmit = (e: FormEvent) => {
    setAttempted(true);
    if (errors.length > 0) {
      e.preventDefault();
      document
        .querySelector(`[data-field="${errors[0]}"]`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }

    if (!ZOHO_WEB_TO_LEAD.enabled) {
      e.preventDefault();
      const href = whatsappLink(consultationMessage(data), locale);
      window.open(href, "_blank", "noopener,noreferrer");
      return;
    }

    // Zoho enabled: let the native <form> POST proceed to
    // ZOHO_WEB_TO_LEAD.postUrl (targeted at the hidden iframe below), then
    // show the thank-you state.
    setSubmitted(true);
  };

  const inputClass =
    "mt-1.5 w-full rounded-xl border border-navy/15 bg-white px-4 py-2.5 text-sm text-navy outline-none transition-colors placeholder:text-navy/35 focus:border-copper";
  const errorClass = "border-red-400 focus:border-red-400";

  const fieldError = (field: keyof ConsultationFormData) =>
    attempted && (REQUIRED_FIELDS as readonly string[]).includes(field) && data[field].trim() === "";

  function TextField({
    field,
    label,
    type = "text",
    placeholder,
    required,
    dir,
    zohoName,
  }: {
    field: keyof ConsultationFormData;
    label: Bilingual;
    type?: string;
    placeholder: Bilingual;
    required: boolean;
    dir?: "ltr";
    zohoName?: string;
  }) {
    const invalid = fieldError(field);
    return (
      <div data-field={field}>
        <label className="text-sm font-semibold text-navy">
          {t(label, locale)}
          <Tag required={required} locale={locale} />
        </label>
        <input
          type={type}
          name={ZOHO_WEB_TO_LEAD.enabled ? zohoName : undefined}
          value={data[field]}
          onChange={update(field)}
          placeholder={t(placeholder, locale)}
          dir={dir}
          className={`${inputClass} ${invalid ? errorClass : ""}`}
          aria-required={required}
          aria-invalid={invalid}
        />
        {invalid && (
          <p className="mt-1 text-xs text-red-500">{t(consultation.requiredError, locale)}</p>
        )}
      </div>
    );
  }

  function SelectField({
    field,
    label,
    options,
    required,
  }: {
    field: keyof ConsultationFormData;
    label: Bilingual;
    options: FieldOption[];
    required: boolean;
  }) {
    const invalid = fieldError(field);
    return (
      <div data-field={field}>
        <label className="text-sm font-semibold text-navy">
          {t(label, locale)}
          <Tag required={required} locale={locale} />
        </label>
        <select
          value={data[field]}
          onChange={update(field)}
          className={`${inputClass} ${invalid ? errorClass : ""}`}
          aria-required={required}
          aria-invalid={invalid}
        >
          <option value="" disabled>
            {locale === "ar" ? "اختر..." : "Select..."}
          </option>
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {t(o.label, locale)}
            </option>
          ))}
        </select>
        {invalid && (
          <p className="mt-1 text-xs text-red-500">{t(consultation.requiredError, locale)}</p>
        )}
      </div>
    );
  }

  return (
    <section className="bg-cream px-5 pb-24 pt-36 text-navy sm:px-8 sm:pb-32 sm:pt-44">
      <div className="mx-auto max-w-3xl">
        <ScrollReveal className="mx-auto max-w-2xl text-center">
          <SectionRule className="mb-6" />
          <h1 className="font-display-heading text-3xl font-bold sm:text-4xl md:text-5xl">
            {t(consultation.pageTitle, locale)}
          </h1>
          <p className="mt-4 text-navy/70">{t(consultation.pageIntro, locale)}</p>
        </ScrollReveal>

        {submitted ? (
          <ScrollReveal delay={0.1}>
            <div className="mt-14 rounded-3xl border border-navy/10 bg-white/70 p-10 text-center shadow-gold-lg">
              <p className="font-display-heading text-lg font-semibold text-navy">
                {locale === "ar" ? "تم استلام طلبك، شكرًا لك!" : "Your request was received, thank you!"}
              </p>
              <p className="mt-2 text-sm text-navy/60">
                {locale === "ar"
                  ? "فريق المبيعات بيتواصل معك بأقرب وقت."
                  : "Our sales team will reach out to you shortly."}
              </p>
            </div>
          </ScrollReveal>
        ) : (
        <ScrollReveal delay={0.1}>
          <form
            action={ZOHO_WEB_TO_LEAD.enabled ? ZOHO_WEB_TO_LEAD.postUrl : undefined}
            method={ZOHO_WEB_TO_LEAD.enabled ? "POST" : undefined}
            target={ZOHO_WEB_TO_LEAD.enabled ? ZOHO_IFRAME_NAME : undefined}
            onSubmit={handleSubmit}
            noValidate
            className="mt-14 rounded-3xl border border-navy/10 bg-white/70 p-6 shadow-gold-lg sm:p-10"
          >
            {ZOHO_WEB_TO_LEAD.enabled && (
              <>
                <input type="hidden" name="xnQsjsdp" value={ZOHO_WEB_TO_LEAD.hiddenFields.xnQsjsdp} />
                <input type="hidden" name="xmIwtLD" value={ZOHO_WEB_TO_LEAD.hiddenFields.xmIwtLD} />
                <input
                  type="hidden"
                  name="actionType"
                  value={ZOHO_WEB_TO_LEAD.hiddenFields.actionType}
                />
                {ZOHO_WEB_TO_LEAD.returnUrl && (
                  <input type="hidden" name="returnUrl" value={ZOHO_WEB_TO_LEAD.returnUrl} />
                )}
                <input
                  type="hidden"
                  name={ZOHO_WEB_TO_LEAD.fieldNames.leadSource}
                  value={ZOHO_WEB_TO_LEAD.leadSource}
                />
                <input
                  type="hidden"
                  name={ZOHO_WEB_TO_LEAD.fieldNames.company}
                  value="Individual — Website Lead"
                />
                <input
                  type="hidden"
                  name={ZOHO_WEB_TO_LEAD.fieldNames.lastName}
                  value={data.fullName}
                />
                <input
                  type="hidden"
                  name={ZOHO_WEB_TO_LEAD.fieldNames.description}
                  value={consultationMessage(data).en}
                />
              </>
            )}

            <div className="grid gap-6 sm:grid-cols-2">
              <TextField
                field="fullName"
                label={consultation.fullName.label}
                placeholder={consultation.fullName.placeholder}
                required
              />
              <TextField
                field="phone"
                label={consultation.phone.label}
                placeholder={consultation.phone.placeholder}
                type="tel"
                dir="ltr"
                zohoName={ZOHO_WEB_TO_LEAD.fieldNames.mobile}
                required
              />
              <TextField
                field="email"
                label={consultation.email.label}
                placeholder={consultation.email.placeholder}
                zohoName={ZOHO_WEB_TO_LEAD.fieldNames.email}
                type="email"
                dir="ltr"
                required={false}
              />
              <SelectField
                field="serviceType"
                label={consultation.serviceType.label}
                options={consultation.serviceType.options}
                required
              />
              <SelectField
                field="location"
                label={consultation.location.label}
                options={consultation.location.options}
                required
              />
              <SelectField
                field="budget"
                label={consultation.budget.label}
                options={consultation.budget.options}
                required={false}
              />
              <SelectField
                field="timeline"
                label={consultation.timeline.label}
                options={consultation.timeline.options}
                required={false}
              />
              <SelectField
                field="contactMethod"
                label={consultation.contactMethod.label}
                options={consultation.contactMethod.options}
                required
              />
            </div>

            <div className="mt-6" data-field="notes">
              <label className="text-sm font-semibold text-navy">
                {t(consultation.notes.label, locale)}
                <Tag required={false} locale={locale} />
              </label>
              <textarea
                value={data.notes}
                onChange={update("notes")}
                placeholder={t(consultation.notes.placeholder, locale)}
                rows={4}
                className={`${inputClass} resize-none`}
              />
            </div>

            <div className="mt-9 flex flex-col items-center gap-3">
              <button
                type="submit"
                className="btn-shine inline-flex w-full items-center justify-center gap-2.5 rounded-full bg-copper-gradient bg-[length:200%_100%] px-6 py-3.5 text-sm font-semibold text-navy-deep shadow-gold transition-all duration-200 hover:scale-[1.02] hover:shadow-gold-lg sm:w-auto sm:text-base"
              >
                <MessageCircle className="h-5 w-5" strokeWidth={2} />
                <span>{t(consultation.submit, locale)}</span>
              </button>
              <p className="text-center text-xs text-navy/50">
                {t(consultation.submitNote, locale)}
              </p>
            </div>
          </form>
        </ScrollReveal>
        )}
        {ZOHO_WEB_TO_LEAD.enabled && (
          <iframe name={ZOHO_IFRAME_NAME} title="Zoho consultation submission" className="hidden" />
        )}
      </div>
    </section>
  );
}
