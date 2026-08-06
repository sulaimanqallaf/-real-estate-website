import type { Bilingual } from "@/lib/i18n";

// PLACEHOLDER DATA — every field below (developer, price, payment plan,
// delivery date, features, brochure) is illustrative only. Replace with
// real, verified figures from the client before this goes live — same
// rule as team.ts and Testimonials.tsx: no invented developer names,
// prices, or delivery commitments on an investment site.
export type ProjectDetail = {
  slug: string;
  title: Bilingual;
  location: Bilingual;
  developer: Bilingual;
  priceFrom: { amount: number; currency: "AED" } | null;
  deliveryDate: Bilingual;
  paymentPlan: Bilingual[];
  features: Bilingual[];
  gallerySlots: number;
  brochureUrl: string | null;
  description: Bilingual;
};

export const projectsData: ProjectDetail[] = [
  {
    slug: "luxury-villas",
    title: { ar: "فلل فاخرة", en: "Luxury Villas" },
    location: { ar: "دبي", en: "Dubai" },
    developer: { ar: "المطور — يُحدد لاحقًا", en: "Developer — to be confirmed" },
    priceFrom: { amount: 3500000, currency: "AED" },
    deliveryDate: { ar: "يُحدد لاحقًا", en: "To be confirmed" },
    paymentPlan: [
      { ar: "10% عند الحجز", en: "10% on booking" },
      { ar: "40% أثناء الإنشاء", en: "40% during construction" },
      { ar: "50% عند التسليم", en: "50% on handover" },
    ],
    features: [
      { ar: "فلل مستقلة بمساحات واسعة", en: "Standalone villas with generous layouts" },
      { ar: "حدائق خاصة ومسابح", en: "Private gardens and pools" },
      { ar: "قرب من المرافق الأساسية", en: "Close to key amenities" },
      { ar: "تشطيبات فاخرة", en: "Premium finishes" },
    ],
    gallerySlots: 4,
    brochureUrl: null,
    description: {
      ar: "مجموعة فلل فاخرة ضمن مجتمع سكني هادئ، مصممة لعائلات تبحث عن مساحة وخصوصية مع قرب من أهم مناطق دبي.",
      en: "A collection of luxury villas within a quiet residential community, designed for families seeking space and privacy close to Dubai's key districts.",
    },
  },
  {
    slug: "luxury-tower",
    title: { ar: "برج سكني فاخر", en: "Luxury Residential Tower" },
    location: { ar: "دبي", en: "Dubai" },
    developer: { ar: "المطور — يُحدد لاحقًا", en: "Developer — to be confirmed" },
    priceFrom: { amount: 950000, currency: "AED" },
    deliveryDate: { ar: "يُحدد لاحقًا", en: "To be confirmed" },
    paymentPlan: [
      { ar: "20% عند الحجز", en: "20% on booking" },
      { ar: "30% أثناء الإنشاء", en: "30% during construction" },
      { ar: "50% عند التسليم", en: "50% on handover" },
    ],
    features: [
      { ar: "شقق استوديو، غرفة، وغرفتين", en: "Studio, 1BR, and 2BR units" },
      { ar: "مرافق مشتركة متكاملة", en: "Full suite of shared amenities" },
      { ar: "إطلالات على أفق دبي", en: "Views of the Dubai skyline" },
      { ar: "قرب من محطات المواصلات", en: "Close to transit links" },
    ],
    gallerySlots: 4,
    brochureUrl: null,
    description: {
      ar: "برج سكني عصري يقدّم وحدات مرنة تناسب المستثمر الباحث عن عائد إيجاري قوي والمقيم الباحث عن أسلوب حياة حضري متكامل.",
      en: "A modern residential tower offering flexible unit types suited to investors seeking strong rental yield and residents seeking a complete urban lifestyle.",
    },
  },
  {
    slug: "jais-retreats",
    title: { ar: "استراحات جبلية", en: "Jais Mountain Retreats" },
    location: { ar: "رأس الخيمة", en: "Ras Al Khaimah" },
    developer: { ar: "المطور — يُحدد لاحقًا", en: "Developer — to be confirmed" },
    priceFrom: { amount: 1200000, currency: "AED" },
    deliveryDate: { ar: "يُحدد لاحقًا", en: "To be confirmed" },
    paymentPlan: [
      { ar: "15% عند الحجز", en: "15% on booking" },
      { ar: "35% أثناء الإنشاء", en: "35% during construction" },
      { ar: "50% عند التسليم", en: "50% on handover" },
    ],
    features: [
      { ar: "إطلالات على جبل جيس", en: "Views of Jebel Jais" },
      { ar: "طراز استراحات جبلية هادئة", en: "Quiet mountain-retreat style" },
      { ar: "قرب من أنشطة المغامرات بجبل جيس", en: "Close to Jebel Jais adventure attractions" },
      { ar: "فرصة تأجير موسمي", en: "Seasonal rental potential" },
    ],
    gallerySlots: 4,
    brochureUrl: null,
    description: {
      ar: "استراحات جبلية بإطلالات مميزة على جبل جيس، فرصة استثمارية مختلفة تجمع بين الهدوء والقرب من أبرز وجهات المغامرة بالإمارات.",
      en: "Mountain retreats with striking Jebel Jais views — a distinctive investment opportunity combining tranquility with proximity to one of the UAE's top adventure destinations.",
    },
  },
];

export function getProjectBySlug(slug: string) {
  return projectsData.find((p) => p.slug === slug);
}
