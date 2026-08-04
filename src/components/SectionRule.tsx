export function SectionRule({ className = "" }: { className?: string }) {
  return (
    <div
      className={`mx-auto h-[3px] w-14 rounded-full bg-copper-gradient bg-[length:200%_100%] ${className}`}
      aria-hidden="true"
    />
  );
}
