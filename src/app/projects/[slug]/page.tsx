import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Footer } from "@/components/Footer";
import { Header } from "@/components/Header";
import { ProjectDetail } from "@/components/ProjectDetail";
import { getProjectBySlug, projectsData } from "@/content/projectsData";

export function generateStaticParams() {
  return projectsData.map((project) => ({ slug: project.slug }));
}

export function generateMetadata({ params }: { params: { slug: string } }): Metadata {
  const project = getProjectBySlug(params.slug);
  if (!project) return {};
  return {
    title: `${project.title.ar} | ${project.title.en} — Altiva`,
    description: `${project.description.ar} | ${project.description.en}`,
  };
}

export default function ProjectPage({ params }: { params: { slug: string } }) {
  const project = getProjectBySlug(params.slug);
  if (!project) notFound();

  return (
    <main>
      <Header />
      <ProjectDetail project={project} />
      <Footer />
    </main>
  );
}
