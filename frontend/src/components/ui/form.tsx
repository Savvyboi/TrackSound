import { Check, ChevronDown, Search } from "lucide-react";
import {
  forwardRef,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "../../lib/cn";

export const inputBase =
  "h-8 w-full min-w-0 rounded-md border border-line-strong bg-surface-2 px-2.5 text-sm text-fg placeholder:text-fg-faint transition-colors outline-none hover:border-fg-faint/50 focus:border-accent-line focus:ring-2 focus:ring-accent/15 disabled:opacity-50 tnum";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { suffix?: ReactNode; prefixIcon?: ReactNode }>(
  function Input({ className, suffix, prefixIcon, ...rest }, ref) {
    if (!suffix && !prefixIcon) return <input ref={ref} className={cn(inputBase, className)} {...rest} />;
    return (
      <div className={cn("relative flex items-center", className)}>
        {prefixIcon && <span className="pointer-events-none absolute left-2.5 text-fg-faint [&_svg]:size-3.5">{prefixIcon}</span>}
        <input ref={ref} className={cn(inputBase, !!prefixIcon && "pl-8", !!suffix && "pr-10")} {...rest} />
        {suffix && <span className="pointer-events-none absolute right-2.5 text-xs text-fg-faint">{suffix}</span>}
      </div>
    );
  },
);

/** Number input that keeps its own text while editing and commits on blur / Enter. */
export function NumberInput({
  value,
  onCommit,
  placeholder,
  step,
  min,
  max,
  suffix,
  className,
  disabled,
  allowEmpty = true,
  digits,
  id,
  ariaLabel,
}: {
  value: number | null | undefined;
  onCommit: (v: number | null) => void;
  placeholder?: string;
  step?: number;
  min?: number;
  max?: number;
  suffix?: ReactNode;
  className?: string;
  disabled?: boolean;
  allowEmpty?: boolean;
  digits?: number;
  id?: string;
  ariaLabel?: string;
}) {
  const fmt = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? "" : digits != null ? String(+v.toFixed(digits)) : String(v));
  const [text, setText] = useState(fmt(value));
  const [focus, setFocus] = useState(false);
  useEffect(() => {
    if (!focus) setText(fmt(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, focus]);
  const parsed = text.trim() === "" ? null : Number(text.replace(",", "."));
  const invalid =
    (parsed === null && !allowEmpty) ||
    (parsed !== null && (!Number.isFinite(parsed) || (min != null && parsed < min) || (max != null && parsed > max)));
  const commit = () => {
    if (invalid) {
      setText(fmt(value));
      return;
    }
    if (parsed !== (value ?? null)) onCommit(parsed);
  };
  return (
    <Input
      id={id}
      aria-label={ariaLabel}
      inputMode="decimal"
      type="text"
      value={text}
      placeholder={placeholder}
      step={step}
      disabled={disabled}
      suffix={suffix}
      className={cn(className, invalid && "[&_input]:border-danger/60 [&_input]:focus:border-danger/60")}
      onFocus={() => setFocus(true)}
      onBlur={() => {
        setFocus(false);
        commit();
      }}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") {
          setText(fmt(value));
          (e.target as HTMLInputElement).blur();
        }
        if ((e.key === "ArrowUp" || e.key === "ArrowDown") && step) {
          e.preventDefault();
          const base = parsed ?? value ?? 0;
          let v = base + (e.key === "ArrowUp" ? step : -step) * (e.shiftKey ? 10 : 1);
          if (min != null) v = Math.max(min, v);
          if (max != null) v = Math.min(max, v);
          v = +v.toFixed(6);
          setText(String(v));
        }
      }}
    />
  );
}

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...rest },
  ref,
) {
  return (
    <div className={cn("relative", className)}>
      <select ref={ref} className={cn(inputBase, "cursor-pointer appearance-none pr-7")} {...rest}>
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-2 size-3.5 -translate-y-1/2 text-fg-faint" />
    </div>
  );
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...rest },
  ref,
) {
  return <textarea ref={ref} className={cn(inputBase, "h-auto min-h-16 resize-y py-1.5 leading-relaxed", className)} {...rest} />;
});

export function Field({
  label,
  hint,
  children,
  className,
  htmlFor,
  aside,
}: {
  label: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
  htmlFor?: string;
  aside?: ReactNode;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={htmlFor} className="text-xs font-medium text-fg-muted">
          {label}
        </label>
        {aside}
      </div>
      {children}
      {hint && <div className="text-2xs leading-snug text-fg-faint">{hint}</div>}
    </div>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
  disabled,
  className,
  ariaLabel,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: ReactNode;
  disabled?: boolean;
  className?: string;
  ariaLabel?: string;
}) {
  return (
    <label className={cn("inline-flex cursor-pointer items-center gap-2 text-xs text-fg select-none", disabled && "pointer-events-none opacity-50", className)}>
      <span
        role="checkbox"
        aria-checked={checked}
        aria-label={ariaLabel}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === " " || e.key === "Enter") {
            e.preventDefault();
            onChange(!checked);
          }
        }}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onChange(!checked);
        }}
        className={cn(
          "flex size-[15px] shrink-0 items-center justify-center rounded-[4px] border transition-colors",
          checked ? "border-accent bg-accent text-accent-fg" : "border-line-strong bg-surface-2 hover:border-fg-faint",
        )}
      >
        {checked && <Check className="size-3" strokeWidth={3} />}
      </span>
      {label && (
        <span
          onClick={(e) => {
            e.preventDefault();
            onChange(!checked);
          }}
        >
          {label}
        </span>
      )}
    </label>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
  className,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: ReactNode;
  disabled?: boolean;
  className?: string;
  hint?: ReactNode;
}) {
  return (
    <label className={cn("flex cursor-pointer items-start justify-between gap-3 select-none", disabled && "pointer-events-none opacity-50", className)}>
      {label && (
        <span className="min-w-0">
          <span className="block text-xs text-fg">{label}</span>
          {hint && <span className="mt-0.5 block text-2xs leading-snug text-fg-faint">{hint}</span>}
        </span>
      )}
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative mt-px inline-flex h-4 w-7 shrink-0 items-center rounded-full border transition-colors",
          checked ? "border-accent bg-accent" : "border-line-strong bg-surface-4",
        )}
      >
        <span
          className={cn(
            "absolute size-3 rounded-full shadow-sm transition-transform",
            checked ? "translate-x-[13px] bg-accent-fg" : "translate-x-[1px] bg-fg-muted",
          )}
        />
      </button>
    </label>
  );
}

export function Slider({
  value,
  min,
  max,
  step,
  onChange,
  className,
  ariaLabel,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
  className?: string;
  ariaLabel?: string;
}) {
  const fill = ((value - min) / (max - min)) * 100;
  return (
    <input
      type="range"
      aria-label={ariaLabel}
      className={cn("ts-range", className)}
      style={{ ["--fill" as string]: `${fill}%` }}
      min={min}
      max={max}
      step={step}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
    />
  );
}

export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  className,
  size = "sm",
  ariaLabel,
}: {
  value: T;
  options: { value: T; label: ReactNode; tip?: string; disabled?: boolean }[];
  onChange: (v: T) => void;
  className?: string;
  size?: "xs" | "sm";
  ariaLabel?: string;
}) {
  return (
    <div role="radiogroup" aria-label={ariaLabel} className={cn("inline-flex rounded-md border border-line-strong bg-surface-2 p-0.5", className)}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          data-tip={o.tip}
          disabled={o.disabled}
          onClick={() => onChange(o.value)}
          className={cn(
            "flex-1 rounded-[4px] font-medium whitespace-nowrap transition-colors disabled:opacity-40 tnum",
            size === "xs" ? "h-5 px-1.5 text-2xs" : "h-6 px-2.5 text-xs",
            o.value === value ? "bg-surface-4 text-fg shadow-[0_1px_2px_rgb(0_0_0/0.25)]" : "text-fg-muted hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Searchable single-select (used for the time zone list). */
export function ComboBox({
  value,
  options,
  onChange,
  placeholder,
  className,
  renderOption,
  searchText,
  ariaLabel,
}: {
  value: string | null;
  options: string[];
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
  renderOption?: (o: string) => ReactNode;
  /** text an option is searched by (default: the option itself, with spaces as underscores) */
  searchText?: (o: string) => string;
  ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const btn = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const id = useId();

  const filtered = useMemo(() => {
    if (searchText) {
      const s = q.trim().toLowerCase();
      if (!s) return options;
      return options.filter((o) => searchText(o).toLowerCase().includes(s));
    }
    const s = q.trim().toLowerCase().replace(/\s+/g, "_");
    if (!s) return options;
    return options.filter((o) => o.toLowerCase().includes(s));
  }, [q, options, searchText]);

  useEffect(() => {
    if (!open) return;
    setRect(btn.current?.getBoundingClientRect() ?? null);
    const close = (e: MouseEvent) => {
      if (listRef.current?.contains(e.target as Node) || btn.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);

  useEffect(() => {
    if (open) {
      const i = value ? Math.max(0, filtered.indexOf(value)) : 0;
      setHi(i);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${hi}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [hi, open]);

  const pick = (o: string) => {
    onChange(o);
    setOpen(false);
    setQ("");
  };

  return (
    <>
      <button
        ref={btn}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={id}
        aria-label={ariaLabel}
        onClick={() => setOpen((v) => !v)}
        className={cn(inputBase, "flex items-center justify-between gap-2 text-left", className)}
      >
        <span className={cn("truncate", !value && "text-fg-faint")}>{value ? (renderOption ? renderOption(value) : value) : placeholder}</span>
        <ChevronDown className="size-3.5 shrink-0 text-fg-faint" />
      </button>
      {open &&
        rect &&
        createPortal(
          <div
            ref={listRef}
            id={id}
            className="fixed z-[90] flex max-h-[320px] flex-col overflow-hidden rounded-lg border border-line-strong bg-surface shadow-pop animate-pop-in"
            style={{
              left: rect.left,
              width: Math.max(rect.width, 260),
              ...(rect.bottom + 330 > window.innerHeight ? { bottom: window.innerHeight - rect.top + 4 } : { top: rect.bottom + 4 }),
            }}
          >
            <div className="border-b border-line p-1.5">
              <Input
                autoFocus
                prefixIcon={<Search />}
                placeholder="Search…"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setHi(0);
                }}
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setHi((h) => Math.min(filtered.length - 1, h + 1));
                  } else if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setHi((h) => Math.max(0, h - 1));
                  } else if (e.key === "Enter") {
                    e.preventDefault();
                    if (filtered[hi]) pick(filtered[hi]);
                  } else if (e.key === "Escape") {
                    setOpen(false);
                  }
                }}
              />
            </div>
            <div role="listbox" className="min-h-0 flex-1 overflow-y-auto p-1">
              {filtered.length === 0 && <div className="px-2 py-3 text-center text-xs text-fg-faint">No matches</div>}
              {filtered.slice(0, 400).map((o, i) => (
                <div
                  key={o}
                  data-idx={i}
                  role="option"
                  aria-selected={o === value}
                  onMouseEnter={() => setHi(i)}
                  onClick={() => pick(o)}
                  className={cn(
                    "flex cursor-pointer items-center justify-between gap-2 rounded-[5px] px-2 py-1 text-xs",
                    i === hi ? "bg-surface-3 text-fg" : "text-fg-muted",
                  )}
                >
                  <span className="truncate">{renderOption ? renderOption(o) : o}</span>
                  {o === value && <Check className="size-3.5 shrink-0 text-accent" />}
                </div>
              ))}
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
