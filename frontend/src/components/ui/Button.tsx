import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Spinner } from "./misc";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "outline" | "subtle";
type Size = "xs" | "sm" | "md" | "lg";

const VARIANT: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:bg-accent-strong font-semibold shadow-[inset_0_1px_0_rgb(255_255_255/0.2)]",
  secondary: "bg-surface-3 text-fg border border-line-strong hover:bg-surface-4 hover:border-fg-faint/40",
  outline: "border border-line-strong text-fg hover:bg-surface-3",
  ghost: "text-fg-muted hover:bg-surface-3 hover:text-fg",
  subtle: "bg-surface-2 text-fg-muted border border-line hover:text-fg hover:bg-surface-3",
  danger: "bg-danger/12 text-danger border border-danger/30 hover:bg-danger/20",
};

const SIZE: Record<Size, string> = {
  xs: "h-6 px-2 text-xs gap-1 rounded-[5px]",
  sm: "h-7 px-2.5 text-xs gap-1.5 rounded-md",
  md: "h-8 px-3 text-sm gap-1.5 rounded-md",
  lg: "h-10 px-4 text-base gap-2 rounded-lg",
};

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  iconRight?: ReactNode;
  loading?: boolean;
  active?: boolean;
  tip?: string;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon, iconRight, loading, active, tip, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      data-tip={tip}
      disabled={disabled || loading}
      className={cn(
        "inline-flex shrink-0 items-center justify-center whitespace-nowrap transition-[background,color,border,opacity] duration-100 select-none disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-3.5 [&_svg]:shrink-0",
        VARIANT[variant],
        SIZE[size],
        active && "!border-accent-line !bg-accent-soft !text-accent",
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner className="size-3.5" /> : icon}
      {children}
      {iconRight}
    </button>
  );
});

export type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  size?: "xs" | "sm" | "md";
  active?: boolean;
  variant?: "ghost" | "subtle" | "secondary";
  tipSide?: "top" | "bottom" | "left" | "right";
  showTip?: boolean;
};

const ICON_SIZE = { xs: "size-6 [&_svg]:size-3.5", sm: "size-7 [&_svg]:size-4", md: "size-8 [&_svg]:size-4" };

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, size = "sm", active, variant = "ghost", className, children, tipSide, showTip = true, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      aria-pressed={active}
      data-tip={showTip ? label : undefined}
      data-tip-side={tipSide}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-md transition-colors duration-100 disabled:pointer-events-none disabled:opacity-40",
        ICON_SIZE[size],
        variant === "ghost" && "text-fg-muted hover:bg-surface-3 hover:text-fg",
        variant === "subtle" && "border border-line bg-surface-2 text-fg-muted hover:bg-surface-3 hover:text-fg",
        variant === "secondary" && "border border-line-strong bg-surface-3 text-fg hover:bg-surface-4",
        active && "!bg-accent-soft !text-accent ring-1 ring-accent-line ring-inset",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
});
