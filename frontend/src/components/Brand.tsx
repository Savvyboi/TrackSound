import { Link } from "react-router-dom";
import { cn } from "../lib/cn";

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("size-7", className)} aria-hidden>
      <defs>
        <linearGradient id="ts-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--accent-strong)" />
          <stop offset="1" stopColor="var(--accent)" />
        </linearGradient>
      </defs>
      <rect x="0.5" y="0.5" width="31" height="31" rx="8" fill="var(--surface-3)" stroke="var(--line-strong)" />
      <path
        d="M5 19c3 0 3.5-9 6.5-9s3 12 6 12 3-14 6-14c1.6 0 2.4 2.5 3.5 5"
        fill="none"
        stroke="url(#ts-g)"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Brand({ compact }: { compact?: boolean }) {
  return (
    <Link to="/" className="group flex items-center gap-2.5 rounded-md outline-none" aria-label="TrackSound home">
      <LogoMark className={compact ? "size-6" : "size-7"} />
      {!compact && (
        <span className="text-[15px] font-semibold tracking-tight text-fg">
          Track<span className="text-accent">Sound</span>
        </span>
      )}
    </Link>
  );
}
