import { Building2, ImageIcon, User } from "lucide-react";

// Visual placeholders standing in for real photography/logos the client
// will supply. Kept intentional-looking (not "broken image") on purpose.

export function SkylineBackdrop({ className = "" }: { className?: string }) {
  return (
    <div
      className={`absolute inset-0 bg-gradient-to-b from-navy-deep via-navy to-navy-light ${className}`}
      aria-hidden="true"
    >
      <svg
        className="absolute bottom-0 left-0 h-1/2 w-full opacity-40"
        viewBox="0 0 1200 200"
        preserveAspectRatio="none"
      >
        <path
          d="M0,200 L0,150 L40,150 L40,110 L90,110 L90,170 L140,170 L140,60 L160,60 L160,20 L180,20 L180,60 L200,60 L200,170 L260,170 L260,130 L320,130 L320,190 L380,190 L380,80 L420,80 L420,150 L470,150 L470,40 L490,40 L490,10 L510,10 L510,40 L530,40 L530,150 L590,150 L590,100 L650,100 L650,170 L710,170 L710,60 L750,60 L750,130 L810,130 L810,180 L870,180 L870,90 L920,90 L920,160 L980,160 L980,120 L1040,120 L1040,190 L1200,190 L1200,200 Z"
          fill="#0B1220"
          fillOpacity="0.6"
        />
      </svg>
      <div className="absolute inset-0 bg-gradient-to-t from-navy-deep via-transparent to-transparent" />
    </div>
  );
}

export function ProjectImagePlaceholder({ className = "" }: { className?: string }) {
  return (
    <div
      className={`flex items-center justify-center bg-gradient-to-br from-navy-light via-navy to-navy-deep ${className}`}
    >
      <Building2 className="h-10 w-10 text-copper/40" strokeWidth={1.25} />
    </div>
  );
}

export function AvatarPlaceholder({ className = "" }: { className?: string }) {
  return (
    <div
      className={`flex items-center justify-center rounded-full bg-navy-light ring-1 ring-copper/30 ${className}`}
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
        <rect x="60" y="120" width="10" height="140" fill="#141F35" />
        <circle cx="65" cy="105" r="26" fill="#141F35" stroke="#C9A15A" strokeWidth="1.5" />
        <rect x="118" y="60" width="8" height="200" fill="#141F35" />
        <circle cx="122" cy="48" r="34" fill="#141F35" stroke="#E8C77E" strokeWidth="1.5" />
        <rect x="150" y="150" width="6" height="110" fill="#141F35" />
      </svg>
      <div className="absolute inset-0 bg-gradient-to-t from-navy-deep via-transparent to-transparent" />
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
      className={`flex h-16 w-32 shrink-0 flex-col items-center justify-center gap-1 rounded-md border border-cream-dark bg-white/60 text-navy/50 ${className}`}
    >
      <ImageIcon className="h-4 w-4" strokeWidth={1.5} />
      <span className="text-[10px] font-medium">{label}</span>
    </div>
  );
}
