import type { Bilingual } from "@/lib/i18n";
import { consultation, type FieldOption } from "./content";

// Content for the project-inquiry lead form embedded on each project detail
// page. Kept separate from content.ts's `consultation` object because this
// form is shorter (name/phone/budget/project) and is the one wired up for
// Zoho CRM Web-to-Lead — see src/lib/zoho.ts for the connection config.
export const leadForm = {
  requiredTag: consultation.requiredTag,
  optionalTag: consultation.optionalTag,
  requiredError: consultation.requiredError,
  submit: { ar: "إرسال بياناتي", en: "Submit My Details" } satisfies Bilingual,
  submitNote: {
    ar: "بالضغط على الزر، فريق المبيعات بيتواصل معك بأقرب وقت.",
    en: "Tapping the button, our sales team will reach out to you shortly.",
  } satisfies Bilingual,

  fullName: {
    label: { ar: "الاسم الكامل", en: "Full Name" } satisfies Bilingual,
    placeholder: { ar: "اسمك الكامل", en: "Your full name" } satisfies Bilingual,
  },
  phone: {
    label: {
      ar: "رقم الهاتف مع مفتاح الدولة",
      en: "Phone Number (with country code)",
    } satisfies Bilingual,
    placeholder: { ar: "+965 XXXXXXXX", en: "+965 XXXXXXXX" } satisfies Bilingual,
  },
  budget: {
    label: consultation.budget.label,
    options: consultation.budget.options,
  },
  projectInterest: {
    label: { ar: "المشروع المهتم فيه", en: "Project of Interest" } satisfies Bilingual,
  },
};

export type LeadFormData = {
  fullName: string;
  phone: string;
  budget: string;
  projectSlug: string;
};

function findLabel(options: FieldOption[], id: string, locale: "ar" | "en") {
  return options.find((o) => o.id === id)?.label[locale] ?? "";
}

function buildMessage(
  data: LeadFormData,
  projectTitle: Bilingual | undefined,
  locale: "ar" | "en"
) {
  const lines =
    locale === "ar"
      ? [
          "مرحبًا، أنا مهتم بأحد مشاريعكم العقارية.",
          `الاسم: ${data.fullName}`,
          `الهاتف: ${data.phone}`,
          projectTitle && `المشروع: ${projectTitle.ar}`,
          data.budget && `الميزانية: ${findLabel(leadForm.budget.options, data.budget, locale)}`,
        ]
      : [
          "Hello, I'm interested in one of your real estate projects.",
          `Name: ${data.fullName}`,
          `Phone: ${data.phone}`,
          projectTitle && `Project: ${projectTitle.en}`,
          data.budget && `Budget: ${findLabel(leadForm.budget.options, data.budget, locale)}`,
        ];

  return lines.filter(Boolean).join("\n");
}

export function leadMessage(data: LeadFormData, projectTitle: Bilingual | undefined): Bilingual {
  return {
    ar: buildMessage(data, projectTitle, "ar"),
    en: buildMessage(data, projectTitle, "en"),
  };
}
