"use client";

import { useEffect, useState } from "react";
import { Menu, X } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { consultation, nav, siteInfo } from "@/content/content";
import { t } from "@/lib/i18n";
import { withBasePath } from "@/lib/basePath";

// Home-page-relative so these still work when clicked from a different
// route (e.g. /consultation) — a bare "#home" would just look for that id
// on whatever page it's clicked from and silently do nothing.
const home = withBasePath("/");
const links = [
  { href: `${home}#home`, label: nav.home },
  { href: `${home}#about`, label: nav.about },
  { href: `${home}#projects`, label: nav.projects },
  { href: `${home}#contact`, label: nav.contact },
];
// Next's static export writes this route to consultation.html (no
// trailing-slash directory form), so link to it without one — GitHub
// Pages does resolve extensionless "/consultation" to that file, but
// matching the real filename exactly avoids relying on that.
const consultationHref = withBasePath("/consultation");

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
          ? "border-b border-copper/10 bg-navy-deep/90 shadow-md shadow-black/20 backdrop-blur-md"
          : "border-b border-transparent bg-transparent"
      }`}
    >
      <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4 sm:px-8">
        <a href={home + "#home"} className="flex items-center gap-2.5">
          {/* eslint-disable-next-line @next/next/no-img-element -- static
              export + unoptimized:true means next/image buys nothing here,
              and it was silently dropping the GitHub Pages base path */}
          <img
            src={withBasePath("/brand/altiva-icon.png")}
            alt=""
            width={31}
            height={40}
            className="h-8 w-auto"
          />
          <span className="flex items-baseline gap-2">
            <span className="copper-text font-display-heading text-xl font-bold tracking-wide sm:text-2xl">
              {siteInfo.logoText}
            </span>
            <span className="hidden text-xs font-medium text-cream/60 sm:inline">
              {t(siteInfo.logoSubtext, locale)}
            </span>
          </span>
        </a>

        <nav className="hidden items-center gap-8 md:flex">
          {links.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="group relative py-1 text-sm font-medium text-cream/85 transition-colors hover:text-copper-end"
            >
              {t(link.label, locale)}
              <span className="absolute inset-x-0 -bottom-0.5 h-px origin-center scale-x-0 bg-copper-gradient transition-transform duration-300 group-hover:scale-x-100" />
            </a>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <a
            href={consultationHref}
            className="btn-shine hidden rounded-full bg-copper-gradient bg-[length:200%_100%] px-4 py-1.5 text-xs font-semibold text-navy-deep shadow-gold transition-transform duration-200 hover:scale-[1.03] sm:inline-block sm:text-sm"
          >
            {t(consultation.navLabel, locale)}
          </a>
          <button
            type="button"
            onClick={toggleLocale}
            className="rounded-full border border-copper/50 px-3.5 py-1.5 text-xs font-semibold text-cream transition-colors hover:bg-copper/10 sm:text-sm"
            aria-label={t(nav.toggleLanguage, locale)}
          >
            {locale === "ar" ? "EN" : "عربي"}
          </button>
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            className="rounded-md p-1.5 text-cream md:hidden"
            aria-label={t(nav.toggleMenu, locale)}
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
          <a
            href={consultationHref}
            onClick={() => setMenuOpen(false)}
            className="mt-1 rounded-md bg-copper-gradient px-2 py-2.5 text-center text-sm font-semibold text-navy-deep"
          >
            {t(consultation.navLabel, locale)}
          </a>
        </nav>
      )}
    </header>
  );
}
