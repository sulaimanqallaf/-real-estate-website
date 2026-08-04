import { About } from "@/components/About";
import { Calculator } from "@/components/Calculator";
import { FinalCta } from "@/components/FinalCta";
import { Footer } from "@/components/Footer";
import { Header } from "@/components/Header";
import { Hero } from "@/components/Hero";
import { Partners } from "@/components/Partners";
import { Projects } from "@/components/Projects";
import { Services } from "@/components/Services";
import { SkylineDivider } from "@/components/SkylineDivider";
import { Stats } from "@/components/Stats";
import { Testimonials } from "@/components/Testimonials";
import { WhyDubai } from "@/components/WhyDubai";

export default function Home() {
  return (
    <main>
      <Header />
      <Hero />
      <SkylineDivider className="bg-navy" />
      <WhyDubai />
      <Projects />
      <SkylineDivider className="bg-navy" />
      <About />
      <Services />
      <Stats />
      <Partners />
      <SkylineDivider className="bg-navy" />
      <Calculator />
      <Testimonials />
      <SkylineDivider className="bg-navy" />
      <FinalCta />
      <Footer />
    </main>
  );
}
