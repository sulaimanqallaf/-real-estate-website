"use client";

import { useEffect, useState } from "react";
import { Menu, X } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { nav, siteInfo } from "@/content/content";
import { t } from "@/lib/i18n";

const links = [
  { href: "#home", label: nav.home },
  { href: "#about", label: nav.about },
  { href: "#projects", label: nav.projects },
  { href: "#contact", label: nav.contact },
];

export function Header() {
  const { locale, toggleLocale } = useLanguage();
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-colors duration-300 ${
        scrolled
          ? "bg-navy-deep/90 backdrop-blur-md shadow-md shadow-black/20"
          : "bg-transparent"
      }`}
    >
      <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4 sm:px-8">
        <a href="#home" className="flex items-baseline gap-2">
          <span className="font-display-heading text-xl font-bold tracking-wide text-cream sm:text-2xl">
            {siteInfo.logoText}
          </span>
          <span className="hidden text-xs font-medium text-copper-end sm:inline">
            {t(siteInfo.logoSubtext, locale)}
          </span>
        </a>

        <nav className="hidden items-center gap-8 md:flex">
          {links.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="text-sm font-medium text-cream/85 transition-colors hover:text-copper-end"
            >
              {t(link.label, locale)}
            </a>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={toggleLocale}
            className="rounded-full border border-copper/50 px-3.5 py-1.5 text-xs font-semibold text-cream transition-colors hover:bg-copper/10 sm:text-sm"
            aria-label="Toggle language"
          >
            {locale === "ar" ? "EN" : "عربي"}
          </button>
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            className="rounded-md p-1.5 text-cream md:hidden"
            aria-label="Toggle menu"
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
          </button>
        </div>
      </div>

      {menuOpen && (
        <nav className="flex flex-col gap-1 border-t border-cream/10 bg-navy-deep/95 px-5 py-4 md:hidden">
          {links.map((link) => (
            <a
              key={link.href}
              href={link.href}
              onClick={() => setMenuOpen(false)}
              className="rounded-md px-2 py-2.5 text-sm font-medium text-cream/90 hover:bg-cream/5"
            >
              {t(link.label, locale)}
            </a>
          ))}
        </nav>
      )}
    </header>
  );
}
