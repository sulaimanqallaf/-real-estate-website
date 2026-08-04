import type { Bilingual } from "@/lib/i18n";

// PLACEHOLDER DATA — replace name/title/avatar fields with real team
// member information and photos before this site goes live.
export type TeamMember = {
  id: string;
  name: Bilingual;
  title: Bilingual;
  avatar: string | null;
};

export const team: TeamMember[] = [
  {
    id: "member-1",
    name: { ar: "الاسم الكامل", en: "Full Name" },
    title: { ar: "المسمى الوظيفي", en: "Job Title" },
    avatar: null,
  },
  {
    id: "member-2",
    name: { ar: "الاسم الكامل", en: "Full Name" },
    title: { ar: "المسمى الوظيفي", en: "Job Title" },
    avatar: null,
  },
  {
    id: "member-3",
    name: { ar: "الاسم الكامل", en: "Full Name" },
    title: { ar: "المسمى الوظيفي", en: "Job Title" },
    avatar: null,
  },
  {
    id: "member-4",
    name: { ar: "الاسم الكامل", en: "Full Name" },
    title: { ar: "المسمى الوظيفي", en: "Job Title" },
    avatar: null,
  },
  {
    id: "member-5",
    name: { ar: "الاسم الكامل", en: "Full Name" },
    title: { ar: "المسمى الوظيفي", en: "Job Title" },
    avatar: null,
  },
  {
    id: "member-6",
    name: { ar: "الاسم الكامل", en: "Full Name" },
    title: { ar: "المسمى الوظيفي", en: "Job Title" },
    avatar: null,
  },
  {
    id: "member-7",
    name: { ar: "الاسم الكامل", en: "Full Name" },
    title: { ar: "المسمى الوظيفي", en: "Job Title" },
    avatar: null,
  },
];
