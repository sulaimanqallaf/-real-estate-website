import type { Bilingual } from "@/lib/i18n";

export const siteInfo = {
  name: { ar: "ألتيفا العقارية", en: "Altiva Real Estate" } satisfies Bilingual,
  logoText: "ALTIVA",
  logoSubtext: { ar: "العقارية", en: "Real Estate" } satisfies Bilingual,
  whatsappNumber: "96541114212",
  phoneNumber: "+965-22200355",
  phoneNumberHref: "+96522200355",
  email: "sales@altivaproperties.com",
  address: {
    ar: "مجمع الصالحية – شارع محمد ثنيان الغانم، الكويت",
    en: "Salhia Complex – Mohammad Thunayyan Al Ghanem St, Kuwait",
  } satisfies Bilingual,
  instagram: "https://www.instagram.com/altiva_properties?igsh=MWI1a2pyYXRscWh3Yg==",
  facebook: "https://facebook.com",
};

export function whatsappLink(message?: Bilingual, locale: "ar" | "en" = "ar") {
  const base = `https://wa.me/${siteInfo.whatsappNumber}`;
  if (!message) return base;
  const text = encodeURIComponent(message[locale]);
  return `${base}?text=${text}`;
}

export const nav = {
  home: { ar: "الرئيسية", en: "Home" } satisfies Bilingual,
  about: { ar: "من نحن", en: "About Us" } satisfies Bilingual,
  projects: { ar: "المشاريع", en: "Projects" } satisfies Bilingual,
  contact: { ar: "تواصل معنا", en: "Contact" } satisfies Bilingual,
  toggleLanguage: { ar: "تبديل اللغة", en: "Toggle language" } satisfies Bilingual,
  toggleMenu: { ar: "تبديل القائمة", en: "Toggle menu" } satisfies Bilingual,
};

export const hero = {
  title: {
    ar: "استثمر في دبي من الكويت بكل سهولة",
    en: "Invest in Dubai from Kuwait, Effortlessly",
  } satisfies Bilingual,
  subtitle: {
    ar: "فرص عقارية مختارة بعوائد تبدأ من 8% سنويًا — بخبرة تمتد لأكثر من 23 عامًا في السوق العقاري",
    en: "Selected real estate opportunities with returns starting from 8% annually — backed by more than 23 years of real estate market expertise",
  } satisfies Bilingual,
  cta: {
    ar: "احصل على أفضل الفرص الآن",
    en: "Get the Best Opportunities Now",
  } satisfies Bilingual,
  ctaMessage: {
    ar: "مرحبًا، أرغب بمعرفة المزيد عن أفضل الفرص الاستثمارية المتاحة لدى ألتيفا في دبي.",
    en: "Hello, I'd like to learn more about the best investment opportunities available with Altiva in Dubai.",
  } satisfies Bilingual,
  scrollHint: { ar: "اكتشف المزيد", en: "Discover More" } satisfies Bilingual,
};

export const whyDubai = {
  heading: { ar: "لماذا دبي؟", en: "Why Dubai?" } satisfies Bilingual,
  subheading: {
    ar: "وجهة استثمارية عالمية تجمع بين الأمان والعائد",
    en: "A global investment destination that combines safety and return",
  } satisfies Bilingual,
  items: [
    {
      icon: "trending-up",
      title: { ar: "عوائد إيجارية مرتفعة", en: "High Rental Yields" } satisfies Bilingual,
      description: {
        ar: "من أعلى العوائد في العالم",
        en: "Among the highest returns in the world",
      } satisfies Bilingual,
    },
    {
      icon: "users",
      title: { ar: "طلب قوي ومتزايد", en: "Strong, Growing Demand" } satisfies Bilingual,
      description: {
        ar: "على الشراء العقاري والاستثمار",
        en: "For property purchase and investment",
      } satisfies Bilingual,
    },
    {
      icon: "shield-check",
      title: { ar: "سوق مستقر وآمن", en: "Stable & Secure Market" } satisfies Bilingual,
      description: {
        ar: "بيئة استثمارية موثوقة",
        en: "A trusted investment environment",
      } satisfies Bilingual,
    },
    {
      icon: "compass",
      title: { ar: "موقع استراتيجي", en: "Strategic Location" } satisfies Bilingual,
      description: {
        ar: "بوابة بين الشرق والغرب",
        en: "A gateway between East and West",
      } satisfies Bilingual,
    },
  ],
};

export const projects = {
  heading: {
    ar: "فرص استثمارية مختارة",
    en: "Selected Investment Opportunities",
  } satisfies Bilingual,
  subheading: {
    ar: "مشاريع مدروسة بعناية في جميع مناطق الدولة وليدز",
    en: "Carefully studied projects across the country and Leeds",
  } satisfies Bilingual,
  comingSoon: { ar: "قريبًا", en: "Coming Soon" } satisfies Bilingual,
  viewAll: { ar: "عرض جميع الفرص", en: "View All Opportunities" } satisfies Bilingual,
  items: [
    {
      title: { ar: "فلل فاخرة", en: "Luxury Villas" } satisfies Bilingual,
      location: { ar: "دبي", en: "Dubai" } satisfies Bilingual,
    },
    {
      title: { ar: "برج سكني فاخر", en: "Luxury Residential Tower" } satisfies Bilingual,
      location: { ar: "دبي", en: "Dubai" } satisfies Bilingual,
    },
    {
      title: { ar: "استراحات جبلية", en: "Jais Mountain Retreats" } satisfies Bilingual,
      location: { ar: "رأس الخيمة", en: "Ras Al Khaimah" } satisfies Bilingual,
    },
  ],
};

export const about = {
  heading: { ar: "من نحن", en: "About Us" } satisfies Bilingual,
  intro: {
    ar: "نحن ألتيفا العقارية، نربط المستثمرين الكويتيين بأفضل الفرص العقارية في دبي. فريقنا يجمع بين الخبرة المحلية والمعرفة العميقة بسوق دبي العقاري، لنقدم لكم فرصًا مدروسة بعناية ومواكبة كاملة من أول استشارة حتى إتمام الصفقة.",
    en: "We are Altiva Real Estate, connecting Kuwaiti investors with the finest real estate opportunities in Dubai. Our team combines local expertise with deep knowledge of the Dubai property market, offering carefully studied opportunities and full support from the first consultation through to closing.",
  } satisfies Bilingual,
  teamHeading: { ar: "فريق العمل", en: "Our Team" } satisfies Bilingual,
};

export const services = {
  heading: { ar: "خدماتنا", en: "Our Services" } satisfies Bilingual,
  subheading: {
    ar: "حلول متكاملة من الاستشارة إلى إتمام الصفقة",
    en: "End-to-end solutions from consultation to closing",
  } satisfies Bilingual,
  items: [
    {
      icon: "target",
      title: { ar: "فرص استثمارية مختارة بعناية", en: "Carefully Selected Investment Opportunities" } satisfies Bilingual,
      description: {
        ar: "نغربل السوق لنقدم لكم فرصًا مدروسة تناسب أهدافكم المالية",
        en: "We screen the market to offer opportunities that fit your financial goals",
      } satisfies Bilingual,
    },
    {
      icon: "home",
      title: { ar: "بيع وشراء وتسويق العقار", en: "Property Sale, Purchase & Marketing" } satisfies Bilingual,
      description: {
        ar: "في جميع مناطق الدولة",
        en: "Across all areas of the country",
      } satisfies Bilingual,
    },
    {
      icon: "shield",
      title: { ar: "تأمين العقار", en: "Property Insurance" } satisfies Bilingual,
      description: {
        ar: "حماية استثماركم العقاري بثقة",
        en: "Protecting your property investment with confidence",
      } satisfies Bilingual,
    },
    {
      icon: "check-circle",
      title: { ar: "إنهاء جميع المعاملات", en: "Complete Transaction Handling" } satisfies Bilingual,
      description: {
        ar: "بسهولة وسرعة وأمان",
        en: "Easily, quickly, and securely",
      } satisfies Bilingual,
    },
    {
      icon: "building",
      title: { ar: "وصول مباشر لأكبر المطورين", en: "Direct Access to Leading Developers" } satisfies Bilingual,
      description: {
        ar: "شراكات مباشرة مع أكبر المطورين العقاريين",
        en: "Direct partnerships with major real estate developers",
      } satisfies Bilingual,
    },
  ],
};

export const stats = {
  items: [
    {
      icon: "calendar",
      value: 23,
      suffix: "+",
      label: {
        ar: "سنة خبرة في الاستثمار والتطوير العقاري",
        en: "Years of experience in real estate investment & development",
      } satisfies Bilingual,
    },
    {
      icon: "handshake",
      value: 100,
      suffix: "%",
      label: {
        ar: "شراكات موثوقة مع أكبر المطورين في دبي",
        en: "Trusted partnerships with Dubai's leading developers",
      } satisfies Bilingual,
    },
    {
      icon: "layers",
      value: 360,
      suffix: "°",
      label: {
        ar: "إدارة متكاملة في إدارة الأملاك",
        en: "Integrated end-to-end property management",
      } satisfies Bilingual,
    },
    {
      icon: "trending-up",
      value: 8,
      suffix: "%+",
      label: {
        ar: "عوائد مجزية — استثمارات مدروسة تحقق أفضل العوائد",
        en: "Rewarding returns — well-studied investments delivering top performance",
      } satisfies Bilingual,
    },
  ],
};

export const partners = {
  heading: {
    ar: "شركاؤنا من كبرى شركات التطوير في دبي",
    en: "Our Partners Among Dubai's Leading Developers",
  } satisfies Bilingual,
  placeholderLabel: { ar: "شعار المطور", en: "Logo Placeholder" } satisfies Bilingual,
  count: 6,
};

export const calculator = {
  heading: {
    ar: "احسب عائدك الاستثماري",
    en: "Calculate Your Investment Return",
  } satisfies Bilingual,
  subheading: {
    ar: "أدخل مبلغ استثمارك واختر نوع العقار لتقدير العائد السنوي المتوقع",
    en: "Enter your investment amount and choose a property type to estimate the expected annual return",
  } satisfies Bilingual,
  amountLabel: { ar: "مبلغ الاستثمار (د.ك)", en: "Investment Amount (KWD)" } satisfies Bilingual,
  propertyTypeLabel: { ar: "نوع العقار", en: "Property Type" } satisfies Bilingual,
  propertyTypes: [
    {
      id: "villa",
      label: { ar: "فيلا", en: "Villa" } satisfies Bilingual,
      rate: 0.08,
    },
    {
      id: "tower",
      label: { ar: "برج سكني", en: "Residential Tower" } satisfies Bilingual,
      rate: 0.09,
    },
    {
      id: "retreat",
      label: { ar: "استراحة", en: "Retreat" } satisfies Bilingual,
      rate: 0.1,
    },
  ],
  resultLabel: {
    ar: "العائد السنوي المتوقع",
    en: "Estimated Annual Return",
  } satisfies Bilingual,
  disclaimer: {
    ar: "هذا الرقم تقديري وليس ضمانًا، ولا يُعد استشارة مالية ملزمة.",
    en: "This figure is an estimate, not a guarantee, and does not constitute binding financial advice.",
  } satisfies Bilingual,
  ctaLabel: {
    ar: "ناقش هذا الرقم مع مستشارنا عبر واتساب",
    en: "Discuss This Number With Our Advisor on WhatsApp",
  } satisfies Bilingual,
  ctaMessage: (amount: number, propertyLabel: Bilingual, estimate: number) => ({
    ar: `مرحبًا، أرغب بمناقشة عائد استثماري تقديري. مبلغ الاستثمار: ${amount.toLocaleString(
      "en-US"
    )} د.ك، نوع العقار: ${propertyLabel.ar}، العائد السنوي التقديري: ${estimate.toLocaleString(
      "en-US"
    )} د.ك تقريبًا.`,
    en: `Hello, I'd like to discuss an estimated investment return. Investment amount: KWD ${amount.toLocaleString(
      "en-US"
    )}, property type: ${propertyLabel.en}, estimated annual return: approx. KWD ${estimate.toLocaleString(
      "en-US"
    )}.`,
  } satisfies Bilingual),
};

export const testimonials = {
  heading: {
    ar: "آراء مستثمرين",
    en: "Investor Stories",
  } satisfies Bilingual,
  subheading: {
    ar: "قصص نجاح من مستثمرين وثقوا بألتيفا",
    en: "Success stories from investors who trusted Altiva",
  } satisfies Bilingual,
  items: [
    {
      name: { ar: "الاسم الكامل", en: "Full Name" } satisfies Bilingual,
      since: { ar: "مستثمر منذ 2022", en: "Investor since 2022" } satisfies Bilingual,
      quote: {
        ar: "تجربة استثمارية موثوقة ومتابعة احترافية من الاستشارة الأولى حتى إتمام الصفقة.",
        en: "A trusted investment experience with professional follow-up from the first consultation through to closing.",
      } satisfies Bilingual,
    },
    {
      name: { ar: "الاسم الكامل", en: "Full Name" } satisfies Bilingual,
      since: { ar: "مستثمر منذ 2021", en: "Investor since 2021" } satisfies Bilingual,
      quote: {
        ar: "فريق يفهم احتياجات المستثمر الكويتي ويقدم فرصًا مدروسة بشفافية كاملة.",
        en: "A team that understands the Kuwaiti investor's needs and offers well-studied opportunities with full transparency.",
      } satisfies Bilingual,
    },
    {
      name: { ar: "الاسم الكامل", en: "Full Name" } satisfies Bilingual,
      since: { ar: "مستثمر منذ 2023", en: "Investor since 2023" } satisfies Bilingual,
      quote: {
        ar: "سهّلوا عليّ كل خطوات الاستثمار في دبي من الكويت دون أي تعقيد.",
        en: "They made every step of investing in Dubai from Kuwait simple and hassle-free.",
      } satisfies Bilingual,
    },
  ],
};

export const finalCta = {
  heading: {
    ar: "هل تبحث عن فرصة استثمارية حقيقية في دبي؟",
    en: "Looking for a Real Investment Opportunity in Dubai?",
  } satisfies Bilingual,
  subheading: {
    ar: "فريقنا جاهز لمساعدتك في اختيار أفضل الفرص لتحقيق أهدافك المالية",
    en: "Our team is ready to help you choose the best opportunities to reach your financial goals",
  } satisfies Bilingual,
  cta: {
    ar: "احجز استشارتك المجانية الآن",
    en: "Book Your Free Consultation Now",
  } satisfies Bilingual,
  ctaMessage: {
    ar: "مرحبًا، أرغب بحجز استشارة مجانية لمناقشة فرص الاستثمار العقاري في دبي.",
    en: "Hello, I'd like to book a free consultation to discuss real estate investment opportunities in Dubai.",
  } satisfies Bilingual,
};

export const footer = {
  contactHeading: { ar: "تواصل معنا", en: "Contact Us" } satisfies Bilingual,
  profileAr: { ar: "الملف التعريفي (عربي)", en: "Company Profile (Arabic)" } satisfies Bilingual,
  profileEn: { ar: "الملف التعريفي (إنجليزي)", en: "Company Profile (English)" } satisfies Bilingual,
  profileNotReady: {
    ar: "سيتم إضافته قريبًا",
    en: "Coming soon",
  } satisfies Bilingual,
  rights: {
    ar: "جميع الحقوق محفوظة",
    en: "All rights reserved",
  } satisfies Bilingual,
};
