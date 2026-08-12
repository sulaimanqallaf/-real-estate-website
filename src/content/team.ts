import type { Bilingual } from "@/lib/i18n";

// Real team members as supplied by the client. More will be appended here
// as additional photos/titles are provided — avatar is null until a real
// photo exists for that member.
export type TeamMember = {
  id: string;
  name: Bilingual;
  title: Bilingual;
  avatar: string | null;
};

export const team: TeamMember[] = [
  {
    id: "abdallah-abulibdeh",
    name: { ar: "عبدالله أبو لبدة", en: "Abdallah Abulibdeh" },
    title: { ar: "مدير الفرع", en: "Branch Manager" },
    avatar: "/team/abdallah-abulibdeh.jpg",
  },
  {
    id: "yasmin-shahabi",
    name: { ar: "ياسمين شهابي", en: "Yasmin Shahabi" },
    title: { ar: "مديرة مكتب الرئيس التنفيذي", en: "CEO Office Manager" },
    avatar: "/team/yasmin-shahabi.jpg",
  },
  {
    id: "sarah-alotaibi",
    name: { ar: "سارة العتيبي", en: "Sarah Alotaibi" },
    title: { ar: "استشارية مبيعات", en: "Sales Consultant" },
    avatar: "/team/sarah-alotaibi.jpg",
  },
  {
    id: "fatma-hasan",
    name: { ar: "فاطمة حسن", en: "Fatma Hasan" },
    title: { ar: "استشارية مبيعات", en: "Sales Consultant" },
    avatar: "/team/fatma-hasan.jpg",
  },
];
