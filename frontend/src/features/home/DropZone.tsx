import { FileAudio, Upload } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { MEDIA_ACCEPT } from "./uploads";

/** Stops the browser from opening a file that's dropped outside a drop zone. */
export function useSwallowWindowDrops() {
  useEffect(() => {
    const prevent = (e: DragEvent) => {
      if (e.dataTransfer?.types?.includes("Files")) e.preventDefault();
    };
    window.addEventListener("dragover", prevent);
    window.addEventListener("drop", prevent);
    return () => {
      window.removeEventListener("dragover", prevent);
      window.removeEventListener("drop", prevent);
    };
  }, []);
}

export function DropZone({
  onFile,
  disabled,
  children,
  className,
  title = "Drop a recording to start a new case",
  compact,
}: {
  onFile: (f: File) => void;
  disabled?: boolean;
  children?: ReactNode;
  className?: string;
  title?: string;
  compact?: boolean;
}) {
  const [over, setOver] = useState(false);
  const depth = useRef(0);
  const input = useRef<HTMLInputElement>(null);
  useSwallowWindowDrops();

  return (
    <div
      onDragEnter={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        depth.current++;
        setOver(true);
      }}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = disabled ? "none" : "copy";
      }}
      onDragLeave={() => {
        depth.current = Math.max(0, depth.current - 1);
        if (depth.current === 0) setOver(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        depth.current = 0;
        setOver(false);
        const f = e.dataTransfer.files?.[0];
        if (f && !disabled) onFile(f);
      }}
      className={cn(
        "group relative flex flex-col items-center justify-center rounded-2xl border border-dashed text-center transition-all duration-150",
        compact ? "gap-2 px-6 py-8" : "gap-3 px-8 py-11",
        over ? "border-accent bg-accent-soft shadow-[0_0_0_4px_var(--accent-soft)]" : "border-line-strong bg-surface/60 hover:border-fg-faint/60",
        disabled && "pointer-events-none opacity-60",
        className,
      )}
    >
      <div
        className={cn(
          "flex items-center justify-center rounded-2xl border transition-colors",
          compact ? "size-11" : "size-14",
          over ? "border-accent-line bg-accent/15 text-accent" : "border-line-strong bg-surface-2 text-fg-muted",
        )}
      >
        {over ? <Upload className="size-6" /> : <FileAudio className="size-6" />}
      </div>
      <div>
        <div className={cn("font-semibold text-fg", compact ? "text-base" : "text-lg")}>{over ? "Release to upload" : title}</div>
        <div className="mt-1 text-sm text-fg-muted">
          or{" "}
          <button className="font-medium text-accent underline-offset-2 hover:underline" onClick={() => input.current?.click()}>
            browse for a file
          </button>
        </div>
      </div>
      <div className="text-xs text-fg-faint">Audio or video: WAV, FLAC, MP3, M4A, MP4, MOV, MKV… Files never leave this computer.</div>
      {children}
      <input
        ref={input}
        type="file"
        accept={MEDIA_ACCEPT}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
    </div>
  );
}
