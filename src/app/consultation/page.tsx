import type { Metadata } from "next";
import { ConsultationForm } from "@/components/ConsultationForm";
import { Footer } from "@/components/Footer";
import { Header } from "@/components/Header";

export const metadata: Metadata = {
  title: "طلب استشارة عقارية | Request a Real Estate Consultation — Altiva",
  description:
    "عبّي بياناتك وفريق ألتيفا بيتواصل معك لمناقشة فرصتك العقارية. | Share your details and the Altiva team will reach out to discuss your real estate needs.",
};

export default function ConsultationPage() {
  return (
    <main>
      <Header />
      <ConsultationForm />
      <Footer />
    </main>
  );
}
