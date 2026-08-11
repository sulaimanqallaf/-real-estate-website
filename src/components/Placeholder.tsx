import { Building2, ImageIcon, User } from "lucide-react";

// Visual placeholders standing in for real photography/logos the client
// will supply. Kept intentional-looking (not "broken image") on purpose.

// Two recognizable landmark silhouettes anchor the skyline: Kuwait Towers
// (the client's home market) on the right, Burj Khalifa (the investment
// destination) on the left — visually the same "from Kuwait, in Dubai"
// story as the hero copy. Both are simplified geometric silhouettes, not
// photographic renderings — deliberate, matching every other graphic on
// the site (see the file-level comment above).
function LandmarkSkylineShapes() {
  return (
    <>
      {/* Low filler skyline, full width, sits behind both landmarks — a
          faint copper outline, same family as the two focal landmarks but
          quieter so it doesn't compete with them. */}
      <path
        d="M0,320 L0,270 L36,270 L36,240 L80,240 L80,290 L124,290 L124,210 L160,210 L160,255 L200,255 L200,300 L940,300 L940,260 L980,260 L980,215 L1020,215 L1020,280 L1060,280 L1060,235 L1104,235 L1104,300 L1200,300 L1200,320 Z"
        fill="none"
        stroke="url(#hero-skyline-copper)"
        strokeWidth="1.25"
        strokeOpacity="0.4"
      />

      {/* Kuwait Towers — main tower (two spheres), companion tower (one
          sphere), and the slender third tower with its aviation light. */}
      <g
        fill="none"
        stroke="url(#hero-skyline-copper)"
        strokeWidth="1.75"
        strokeLinejoin="round"
        style={{ filter: "url(#hero-skyline-glow)" }}
      >
        <line x1="201.5" y1="300" x2="201.5" y2="112" />
        <circle cx="201.5" cy="148" r="21" />
        <circle cx="201.5" cy="96" r="12.5" />
        <line x1="250" y1="300" x2="250" y2="168" />
        <circle cx="250" cy="152" r="15" />
        <line x1="282" y1="300" x2="282" y2="196" />
        <circle cx="282" cy="193" r="2.5" fill="#F0D68C" stroke="none" />
      </g>

      {/* Burj Khalifa — stepped, tapering tiers narrowing to a needle
          spire, the silhouette's tallest point. */}
      <path
        d="M816,300 L816,260 L823,260 L823,170 L830,170 L830,90 L838,90 L838,40 L847,40 L850,4 L853,40 L862,40 L862,90 L870,90 L870,170 L877,170 L877,260 L884,260 L884,300"
        fill="none"
        stroke="url(#hero-skyline-copper)"
        strokeWidth="1.75"
        strokeLinejoin="round"
        style={{ filter: "url(#hero-skyline-glow)" }}
      />
    </>
  );
}

export function SkylineBackdrop({ className = "" }: { className?: string }) {
  return (
    <div
      className={`absolute inset-0 bg-gradient-to-b from-navy-deep via-navy to-navy-light ${className}`}
      aria-hidden="true"
    >
      <div className="absolute inset-0 bg-gold-radial" />
      {/* Fixed height bands (not a fraction of the section, which is
          min-h-screen and can run much taller than the skyline itself) so
          the silhouettes stay a legible, consistent size instead of being
          stretched or cropped to fill an oversized area. */}
      <svg
        className="absolute inset-x-0 bottom-0 h-52 w-full sm:h-72 md:h-80"
        viewBox="0 0 1200 320"
        preserveAspectRatio="xMidYMax meet"
      >
        <defs>
          <linearGradient id="hero-skyline-copper" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#7A5A22" stopOpacity="0.7" />
            <stop offset="50%" stopColor="#F0D68C" stopOpacity="1" />
            <stop offset="100%" stopColor="#7A5A22" stopOpacity="0.7" />
          </linearGradient>
          <filter id="hero-skyline-glow" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="1.4" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <LandmarkSkylineShapes />
      </svg>
      {/* Fade only the lowest sliver into the section below — a full-height
          fade would wash the skyline back out. */}
      <div className="absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-navy-deep to-transparent" />
      <div className="grain-overlay" />
    </div>
  );
}

export function ProjectImagePlaceholder({ className = "" }: { className?: string }) {
  return (
    <div
      className={`relative flex items-center justify-center overflow-hidden bg-gradient-to-br from-navy-light via-navy to-navy-deep ${className}`}
    >
      <div className="absolute inset-0 bg-gold-radial" />
      <Building2 className="relative h-10 w-10 text-copper-end/50" strokeWidth={1.25} />
      <div className="grain-overlay" />
    </div>
  );
}

export function AvatarPlaceholder({ className = "" }: { className?: string }) {
  return (
    <div
      className={`flex items-center justify-center rounded-full bg-navy-light ring-1 ring-copper/30 transition-shadow duration-300 hover:shadow-gold hover:ring-copper/60 ${className}`}
    >
      <User className="h-8 w-8 text-cream/40" strokeWidth={1.25} />
    </div>
  );
}

export function KuwaitTowersPlaceholder({ className = "" }: { className?: string }) {
  return (
    <div
      className={`relative flex items-end justify-center overflow-hidden bg-gradient-to-b from-navy-deep via-navy to-navy-light ${className}`}
      aria-hidden="true"
    >
      <svg viewBox="0 0 200 260" className="h-full w-auto opacity-70">
        <rect x="60" y="120" width="10" height="140" fill="#16213D" />
        <circle cx="65" cy="105" r="26" fill="#16213D" stroke="#B8873A" strokeWidth="1.5" />
        <rect x="118" y="60" width="8" height="200" fill="#16213D" />
        <circle cx="122" cy="48" r="34" fill="#16213D" stroke="#F0D68C" strokeWidth="1.5" />
        <rect x="150" y="150" width="6" height="110" fill="#16213D" />
      </svg>
      <div className="absolute inset-0 bg-gradient-to-t from-navy-deep via-transparent to-transparent" />
      <div className="grain-overlay" />
    </div>
  );
}

export function LogoPlaceholder({
  label,
  className = "",
}: {
  label: string;
  className?: string;
}) {
  return (
    <div
      className={`flex h-16 w-32 shrink-0 flex-col items-center justify-center gap-1 rounded-md border border-cream-dark bg-white/70 text-navy/50 shadow-sm transition-shadow duration-300 hover:shadow-gold ${className}`}
    >
      <ImageIcon className="h-4 w-4" strokeWidth={1.5} />
      <span className="text-[10px] font-medium">{label}</span>
    </div>
  );
}
