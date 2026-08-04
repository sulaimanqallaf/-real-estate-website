"use client";

import { FileText, Mail, MapPin, Phone } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { footer, siteInfo, whatsappLink } from "@/content/content";
import { t } from "@/lib/i18n";
import { KuwaitTowersPlaceholder } from "./Placeholder";

function WhatsappGlyph({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
      <path d="M12.04 2c-5.52 0-10 4.48-10 10 0 1.77.46 3.43 1.27 4.87L2 22l5.28-1.28A9.96 9.96 0 0 0 12.04 22c5.52 0 10-4.48 10-10s-4.48-10-10-10Zm5.87 14.24c-.25.7-1.24 1.29-2.02 1.46-.55.12-1.26.21-3.66-.78-3.07-1.27-5.05-4.37-5.2-4.57-.15-.2-1.24-1.65-1.24-3.15 0-1.5.79-2.24 1.07-2.54.28-.3.62-.38.82-.38.2 0 .41 0 .59.01.19.01.44-.07.69.53.25.6.86 2.08.94 2.23.08.15.13.33.03.53-.1.2-.15.33-.3.5-.15.17-.31.38-.44.51-.15.15-.3.31-.13.61.17.3.75 1.24 1.62 2.01 1.11.99 2.04 1.3 2.34 1.45.3.15.48.13.65-.08.18-.2.75-.87.95-1.17.2-.3.39-.25.65-.15.27.1 1.71.81 2 .96.3.15.49.22.56.35.08.13.08.75-.17 1.44Z" />
    </svg>
  );
}

function InstagramGlyph({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      aria-hidden="true"
    >
      <rect x="3" y="3" width="18" height="18" rx="5" />
      <circle cx="12" cy="12" r="4.2" />
      <circle cx="17.3" cy="6.7" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}

function FacebookGlyph({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
      <path d="M13.5 22v-8.5h2.85l.43-3.31H13.5V8.05c0-.96.27-1.61 1.64-1.61h1.75V3.48A23.6 23.6 0 0 0 14.4 3.3c-2.5 0-4.22 1.53-4.22 4.34v2.42H7.32v3.31h2.86V22h3.32Z" />
    </svg>
  );
}

export function Footer() {
  const { locale } = useLanguage();

  return (
    <footer id="contact" className="bg-navy-deep text-cream">
      <div className="mx-auto grid max-w-6xl grid-cols-1 gap-12 px-5 py-16 sm:px-8 lg:grid-cols-[1.2fr_0.8fr]">
        <div>
          <div className="flex items-baseline gap-2">
            <span className="font-display-heading text-xl font-bold tracking-wide">
              {siteInfo.logoText}
            </span>
            <span className="text-xs font-medium text-copper-end">
              {t(siteInfo.logoSubtext, locale)}
            </span>
          </div>
          <h3 className="mt-6 font-display-heading text-lg font-semibold">
            {t(footer.contactHeading, locale)}
          </h3>

          <ul className="mt-5 space-y-3.5 text-sm text-cream/80">
            <li className="flex items-start gap-3">
              <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-copper-end" />
              <span>{t(siteInfo.address, locale)}</span>
            </li>
            <li>
              <a
                href={whatsappLink(undefined, locale)}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-3 transition-colors hover:text-copper-end"
              >
                <WhatsappGlyph className="h-4 w-4 shrink-0 text-copper-end" />
                <span dir="ltr">+965-41114212</span>
              </a>
            </li>
            <li>
              <a
                href={`tel:${siteInfo.phoneNumberHref}`}
                className="flex items-center gap-3 transition-colors hover:text-copper-end"
              >
                <Phone className="h-4 w-4 shrink-0 text-copper-end" />
                <span dir="ltr">{siteInfo.phoneNumber}</span>
              </a>
            </li>
            <li>
              <a
                href={`mailto:${siteInfo.email}`}
                className="flex items-center gap-3 transition-colors hover:text-copper-end"
              >
                <Mail className="h-4 w-4 shrink-0 text-copper-end" />
                <span>{siteInfo.email}</span>
              </a>
            </li>
          </ul>

          <div className="mt-7 flex flex-wrap gap-4">
            {/* PDF profile links aren't live yet — rendered inert (not a
                real "#" anchor) so they don't fake a click action. */}
            <span
              className="flex cursor-not-allowed items-center gap-2 text-sm text-cream/40"
              title={t(footer.profileNotReady, locale)}
              aria-disabled="true"
            >
              <FileText className="h-4 w-4" />
              {t(footer.profileAr, locale)}
            </span>
            <span
              className="flex cursor-not-allowed items-center gap-2 text-sm text-cream/40"
              title={t(footer.profileNotReady, locale)}
              aria-disabled="true"
            >
              <FileText className="h-4 w-4" />
              {t(footer.profileEn, locale)}
            </span>
          </div>

          <div className="mt-7 flex gap-3">
            <a
              href={siteInfo.instagram}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Instagram"
              className="flex h-9 w-9 items-center justify-center rounded-full border border-cream/15 transition-colors hover:border-copper/60 hover:text-copper-end"
            >
              <InstagramGlyph className="h-4 w-4" />
            </a>
            <a
              href={siteInfo.facebook}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Facebook"
              className="flex h-9 w-9 items-center justify-center rounded-full border border-cream/15 transition-colors hover:border-copper/60 hover:text-copper-end"
            >
              <FacebookGlyph className="h-4 w-4" />
            </a>
          </div>
        </div>

        <KuwaitTowersPlaceholder className="hidden min-h-[280px] rounded-2xl lg:block" />
      </div>

      <div className="border-t border-cream/10 px-5 py-6 text-center text-xs text-cream/50 sm:px-8">
        © {new Date().getFullYear()} {t(siteInfo.name, locale)} — {t(footer.rights, locale)}
      </div>
    </footer>
  );
}
