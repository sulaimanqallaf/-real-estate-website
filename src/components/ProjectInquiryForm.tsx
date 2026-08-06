"use client";

import { useState, type FormEvent } from "react";
import { MessageCircle } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { whatsappLink } from "@/content/content";
import { leadForm, leadMessage, type LeadFormData } from "@/content/leadForm";
import { getProjectBySlug, projectsData } from "@/content/projectsData";
import { t, type Bilingual } from "@/lib/i18n";
import { ZOHO_WEB_TO_LEAD } from "@/lib/zoho";

const REQUIRED_FIELDS = ["fullName", "phone"] as const satisfies readonly (keyof LeadFormData)[];

const ZOHO_IFRAME_NAME = "zoho-lead-target";

function Tag({ required, locale }: { required: boolean; locale: "ar" | "en" }) {
  return (
    <span className={`text-xs font-normal ${required ? "text-copper-start" : "text-navy/40"}`}>
      {" "}
      ({t(required ? leadForm.requiredTag : leadForm.optionalTag, locale)})
    </span>
  );
}

export function ProjectInquiryForm({ defaultProjectSlug }: { defaultProjectSlug: string }) {
  const { locale } = useLanguage();
  const [data, setData] = useState<LeadFormData>({
    fullName: "",
    phone: "",
    budget: "",
    projectSlug: defaultProjectSlug,
  });
  const [attempted, setAttempted] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const errors = REQUIRED_FIELDS.filter((field) => data[field].trim() === "");

  const update = (
    field: keyof LeadFormData
  ) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setData((prev) => ({ ...prev, [field]: e.target.value }));

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
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
      const project = getProjectBySlug(data.projectSlug);
      const href = whatsappLink(leadMessage(data, project?.title), locale);
      window.open(href, "_blank", "noopener,noreferrer");
      return;
    }

    // Zoho enabled: let the native <form> POST proceed to ZOHO_WEB_TO_LEAD.postUrl
    // (targeted at the hidden iframe below), then show the thank-you state.
    setSubmitted(true);
  };

  const inputClass =
    "mt-1.5 w-full rounded-xl border border-navy/15 bg-white px-4 py-2.5 text-sm text-navy outline-none transition-colors placeholder:text-navy/35 focus:border-copper";
  const errorClass = "border-red-400 focus:border-red-400";

  const fieldError = (field: keyof LeadFormData) =>
    attempted && (REQUIRED_FIELDS as readonly string[]).includes(field) && data[field].trim() === "";

  function TextField({
    field,
    label,
    type = "text",
    placeholder,
    required,
    dir,
  }: {
    field: keyof LeadFormData;
    label: Bilingual;
    type?: string;
    placeholder: Bilingual;
    required: boolean;
    dir?: "ltr";
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
          name={field === "phone" ? ZOHO_WEB_TO_LEAD.fieldNames.mobile : undefined}
          value={data[field]}
          onChange={update(field)}
          placeholder={t(placeholder, locale)}
          dir={dir}
          className={`${inputClass} ${invalid ? errorClass : ""}`}
          aria-required={required}
          aria-invalid={invalid}
        />
        {invalid && (
          <p className="mt-1 text-xs text-red-500">{t(leadForm.requiredError, locale)}</p>
        )}
      </div>
    );
  }

  if (submitted) {
    return (
      <div className="rounded-3xl border border-navy/10 bg-white/70 p-10 text-center shadow-gold-lg">
        <p className="font-display-heading text-lg font-semibold text-navy">
          {locale === "ar" ? "تم استلام بياناتك، شكرًا لك!" : "Your details were received, thank you!"}
        </p>
        <p className="mt-2 text-sm text-navy/60">
          {locale === "ar"
            ? "فريق المبيعات بيتواصل معك بأقرب وقت."
            : "Our sales team will reach out to you shortly."}
        </p>
      </div>
    );
  }

  return (
    <>
      <form
        action={ZOHO_WEB_TO_LEAD.enabled ? ZOHO_WEB_TO_LEAD.postUrl : undefined}
        method={ZOHO_WEB_TO_LEAD.enabled ? "POST" : undefined}
        target={ZOHO_WEB_TO_LEAD.enabled ? ZOHO_IFRAME_NAME : undefined}
        onSubmit={handleSubmit}
        noValidate
        className="rounded-3xl border border-navy/10 bg-white/70 p-6 shadow-gold-lg sm:p-8"
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
              value={`Project: ${data.projectSlug} | Budget: ${data.budget}`}
            />
          </>
        )}

        <div className="grid gap-6 sm:grid-cols-2">
          <TextField
            field="fullName"
            label={leadForm.fullName.label}
            placeholder={leadForm.fullName.placeholder}
            required
          />
          <TextField
            field="phone"
            label={leadForm.phone.label}
            placeholder={leadForm.phone.placeholder}
            type="tel"
            dir="ltr"
            required
          />
          <div data-field="projectSlug">
            <label className="text-sm font-semibold text-navy">
              {t(leadForm.projectInterest.label, locale)}
              <Tag required={false} locale={locale} />
            </label>
            <select
              value={data.projectSlug}
              onChange={update("projectSlug")}
              className={inputClass}
            >
              {projectsData.map((p) => (
                <option key={p.slug} value={p.slug}>
                  {t(p.title, locale)}
                </option>
              ))}
            </select>
          </div>
          <div data-field="budget">
            <label className="text-sm font-semibold text-navy">
              {t(leadForm.budget.label, locale)}
              <Tag required={false} locale={locale} />
            </label>
            <select value={data.budget} onChange={update("budget")} className={inputClass}>
              <option value="" disabled>
                {locale === "ar" ? "اختر..." : "Select..."}
              </option>
              {leadForm.budget.options.map((o) => (
                <option key={o.id} value={o.id}>
                  {t(o.label, locale)}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="mt-8 flex flex-col items-center gap-3">
          <button
            type="submit"
            className="btn-shine inline-flex w-full items-center justify-center gap-2.5 rounded-full bg-copper-gradient bg-[length:200%_100%] px-6 py-3.5 text-sm font-semibold text-navy-deep shadow-gold transition-all duration-200 hover:scale-[1.02] hover:shadow-gold-lg sm:w-auto sm:text-base"
          >
            <MessageCircle className="h-5 w-5" strokeWidth={2} />
            <span>{t(leadForm.submit, locale)}</span>
          </button>
          <p className="text-center text-xs text-navy/50">{t(leadForm.submitNote, locale)}</p>
        </div>
      </form>
      {ZOHO_WEB_TO_LEAD.enabled && (
        <iframe name={ZOHO_IFRAME_NAME} title="Zoho lead submission" className="hidden" />
      )}
    </>
  );
}
