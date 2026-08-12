import type { Bilingual } from "@/lib/i18n";

// luxury-villas/luxury-tower/jais-retreats below are still PLACEHOLDER DATA
// (developer, price, payment plan, delivery date, features, brochure are
// illustrative) pending real figures from the client. Projects with a real
// `images` array (e.g. ventana-residences) are real, client-supplied data —
// no invented developer names, prices, or delivery commitments either way.
export type ProjectDetail = {
  slug: string;
  title: Bilingual;
  location: Bilingual;
  developer: Bilingual;
  priceFrom: { amount: number; currency: "AED" } | null;
  // Placeholder projects' priceFrom is an illustrative figure (shows an
  // "Estimated" tag); real projects set this false once the client has
  // confirmed a starting price.
  priceIsEstimate: boolean;
  deliveryDate: Bilingual;
  paymentPlan: Bilingual[];
  features: Bilingual[];
  gallerySlots: number;
  // Real gallery photos, basePath-relative (e.g. "/projects/ventana/x.jpg").
  // When present, these replace the gallerySlots placeholder grid.
  images?: string[];
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
    priceIsEstimate: true,
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
    priceIsEstimate: true,
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
    priceIsEstimate: true,
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
  {
    slug: "ventana-residences",
    title: { ar: "فنتانا ريزيدنس", en: "Ventana Residences" },
    location: { ar: "وارسان 4، دبي", en: "Warsan 4, Dubai" },
    developer: { ar: "زيدور للتطوير العقاري", en: "Zedor Developments" },
    priceFrom: { amount: 550000, currency: "AED" },
    priceIsEstimate: false,
    deliveryDate: { ar: "يُحدد لاحقًا", en: "To be confirmed" },
    paymentPlan: [
      { ar: "20% عند الحجز", en: "20% on booking" },
      { ar: "30% أثناء الإنشاء", en: "30% during construction" },
      { ar: "50% عند التسليم", en: "50% on handover" },
    ],
    features: [
      { ar: "استوديوهات وشقق غرفة وغرفتين", en: "Studio, 1BR, and 2BR apartments" },
      { ar: "مسبح وتراس على السطح", en: "Rooftop pool and terrace" },
      { ar: "صالة رياضية مجهزة بالكامل", en: "Fully equipped gym" },
      { ar: "لوبي استقبال فاخر", en: "Luxury reception lobby" },
      { ar: "قرب من المرافق الأساسية في وارسان", en: "Close to key amenities in Warsan" },
    ],
    gallerySlots: 5,
    images: [
      "/projects/ventana/exterior-night.jpg",
      "/projects/ventana/aerial.jpg",
      "/projects/ventana/lobby.jpg",
      "/projects/ventana/studio-interior.jpg",
      "/projects/ventana/pool-terrace.jpg",
    ],
    brochureUrl: "/brochures/ventana-residences-brochure.pdf",
    description: {
      ar: "فنتانا ريزيدنس مشروع سكني من زيدور للتطوير العقاري في وارسان 4، دبي، يقدّم استوديوهات وشقق غرفة وغرفتين بمرافق مشتركة متكاملة، بأسعار تبدأ من 550,000 درهم.",
      en: "Ventana Residences is a residential development by Zedor Developments in Warsan 4, Dubai, offering studio, 1BR, and 2BR apartments with a full suite of shared amenities, starting from AED 550,000.",
    },
  },
  {
    slug: "empire-jebel-ali",
    title: { ar: "امباير داون تاون جبل علي", en: "Empire Down Town Jebel Ali" },
    location: { ar: "جبل علي، دبي", en: "Jebel Ali, Dubai" },
    developer: { ar: "امباير للتطوير العقاري", en: "Empire Developments" },
    priceFrom: { amount: 799777, currency: "AED" },
    priceIsEstimate: false,
    deliveryDate: { ar: "الربع الثالث 2029", en: "Q3 2029" },
    paymentPlan: [
      { ar: "20% عند الحجز", en: "20% on booking" },
      { ar: "1% شهريًا لمدة 80 شهرًا", en: "1% monthly for 80 months" },
      { ar: "44% عند التسليم", en: "44% on handover" },
    ],
    features: [
      { ar: "دقيقة واحدة مشيًا إلى محطة المترو", en: "1-minute walk to the metro station" },
      { ar: "نظام المنزل الذكي", en: "Smart home system" },
      { ar: "مطبخ مجهز بالكامل", en: "Fully equipped kitchen" },
      { ar: "مسابح وصالات رياضية وسبا وساونا", en: "Swimming pools, gyms, spa and sauna" },
      { ar: "محلات ومعارض تجارية في الطابق الأرضي", en: "Ground-floor retail and showroom spaces" },
    ],
    gallerySlots: 3,
    images: [
      "/projects/empire-jebel-ali/exterior-day.jpg",
      "/projects/empire-jebel-ali/exterior-street.jpg",
      "/projects/empire-jebel-ali/exterior-aerial.jpg",
    ],
    brochureUrl: "/brochures/empire-jebel-ali-floor-plans.pdf",
    description: {
      ar: "امباير داون تاون جبل علي مشروع سكني وتجاري من امباير للتطوير العقاري في جبل علي، دبي، على بعد دقيقة مشيًا من محطة المترو، ويضم 116 وحدة سكنية (استوديو، غرفة، وغرفتين) ضمن مبنى من أرضي + 4 طوابق مواقف + 9 طوابق، بأسعار تبدأ من 799,777 درهم وتسليم متوقع في الربع الثالث 2029.",
      en: "Empire Down Town Jebel Ali is a residential and retail development by Empire Developments in Jebel Ali, Dubai, a 1-minute walk from the metro station. The building offers 116 units (studio, 1BR, and 2BR) across G+4 podium+9 floors, starting from AED 799,777, with handover expected Q3 2029.",
    },
  },
  {
    slug: "ayami-residence",
    title: { ar: "أيامي ريزيدنس", en: "Ayami Residence" },
    location: { ar: "ورسان الأولى، دبي", en: "Warsan First, Dubai" },
    developer: { ar: "آيات للتطوير العقاري", en: "AYAT Development" },
    priceFrom: { amount: 864000, currency: "AED" },
    priceIsEstimate: false,
    deliveryDate: { ar: "الربع الرابع 2028", en: "Q4 2028" },
    paymentPlan: [
      { ar: "5% مقدم عند الحجز", en: "5% down payment on booking" },
      { ar: "15% بعد شهر واحد", en: "15% after one month" },
      { ar: "5% في 2027", en: "5% in 2027" },
      { ar: "5% في 2028", en: "5% in 2028" },
      { ar: "50% عند التسليم", en: "50% on handover" },
      { ar: "20% بعد الاستلام لمدة سنة", en: "20% post-handover over one year" },
    ],
    features: [
      { ar: "قرب محطة مترو الخط الأزرق", en: "Near the Metro Blue Line station" },
      { ar: "مسبحان وحمام سباحة على السطح للكبار", en: "Two swimming pools and a rooftop adult pool" },
      { ar: "صالتان رياضيتان تخدمان المبنى بالكامل", en: "Two gyms serving the full building" },
      { ar: "ملعب بادل وسينما مفتوحة وستوديو بودكاست", en: "Paddle court, open cinema, and podcast studio" },
      { ar: "قرب من كريك هاربور ومطار دبي ووسط المدينة", en: "Close to Dubai Creek Harbour, DXB Airport, and Downtown Dubai" },
    ],
    gallerySlots: 5,
    images: [
      "/projects/ayami-residence/exterior-dusk.jpg",
      "/projects/ayami-residence/balcony-view.jpg",
      "/projects/ayami-residence/lobby.jpg",
      "/projects/ayami-residence/rooftop-pool.jpg",
      "/projects/ayami-residence/living-room.jpg",
    ],
    brochureUrl: "/brochures/ayami-residence-brochure.pdf",
    description: {
      ar: "أيامي ريزيدنس مشروع سكني من آيات للتطوير العقاري في ورسان الأولى، دبي، عند تقاطع شارع الشيخ محمد بن زايد مع شارع رأس الخور والعوير، يقدّم استوديوهات وشقق غرفة وغرفتين بمرافق حياة متكاملة، بأسعار تبدأ من 864,000 درهم للغرفة وصالة (1.1 مليون درهم لغرفتين وصالة)، وتسليم متوقع في الربع الرابع 2028.",
      en: "Ayami Residence is a residential development by AYAT Development in Warsan First, Dubai, at the intersection of Sheikh Mohammed Bin Zayed Road with Ras Al Khor Road and Al Awir Road. It offers studio, 1BR, and 2BR apartments with a full lifestyle amenity offering, starting from AED 864,000 for a 1BR (AED 1.1M for a 2BR), with handover expected Q4 2028.",
    },
  },
];

export function getProjectBySlug(slug: string) {
  return projectsData.find((p) => p.slug === slug);
}
