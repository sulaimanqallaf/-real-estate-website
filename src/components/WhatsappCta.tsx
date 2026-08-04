import { MessageCircle } from "lucide-react";
import type { ReactNode } from "react";

export function WhatsappCta({
  href,
  children,
  variant = "solid",
  className = "",
}: {
  href: string;
  children: ReactNode;
  variant?: "solid" | "outline";
  className?: string;
}) {
  const base =
    "inline-flex items-center gap-2.5 rounded-full px-6 py-3.5 text-sm font-semibold transition-all duration-200 hover:scale-[1.02] hover:shadow-gold-lg focus-visible:scale-[1.02] sm:text-base";
  const styles =
    variant === "solid"
      ? "btn-shine bg-copper-gradient bg-[length:200%_100%] text-navy-deep shadow-gold"
      : "border border-copper/60 text-cream hover:border-copper hover:bg-copper/10";

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={`${base} ${styles} ${className}`}
    >
      <MessageCircle className="h-5 w-5" strokeWidth={2} />
      <span>{children}</span>
    </a>
  );
}
