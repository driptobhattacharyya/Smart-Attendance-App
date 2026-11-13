import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { cx } from "./cx";
import { IconButton } from "./Button";

type Variant = "center" | "side" | "drawer";

const POSITION: Record<Variant, string> = {
  center:
    "dialog-center m-auto w-[calc(100%-1.5rem)] max-h-[calc(100dvh-2rem)] rounded-xl border border-line",
  side: "dialog-side ml-auto mr-0 my-0 h-dvh max-h-dvh w-full rounded-none border-l border-line sm:rounded-l-xl",
  drawer: "dialog-drawer mr-auto ml-0 my-0 h-dvh max-h-dvh w-72 max-w-[85vw] rounded-none border-r border-line",
};
const SIZE = { sm: "max-w-md", md: "max-w-xl", lg: "max-w-3xl" };

/**
 * Accessible modal built on the native <dialog>: focus trap, Esc and inert background come
 * from the browser. Content mounts only while open, so heavy children (maps) are free when closed.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  variant = "center",
  size = "md",
  bare = false,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  variant?: Variant;
  size?: keyof typeof SIZE;
  /** Skip the header/padding (used by the nav drawer). */
  bare?: boolean;
}) {
  if (!open) return null;
  return (
    <Modal
      onClose={onClose}
      title={title}
      description={description}
      footer={footer}
      variant={variant}
      size={size}
      bare={bare}
    >
      {children}
    </Modal>
  );
}

function Modal({
  onClose,
  title,
  description,
  children,
  footer,
  variant,
  size,
  bare,
}: {
  onClose: () => void;
  title?: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  variant: Variant;
  size: keyof typeof SIZE;
  bare: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const el = ref.current;
    if (el && !el.open) el.showModal();
  }, []);

  return (
    <dialog
      ref={ref}
      aria-labelledby={title ? titleId : undefined}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onMouseDown={(e) => {
        if (e.target === ref.current) onClose();
      }}
      className={cx(
        "bg-surface p-0 text-fg shadow-pop",
        POSITION[variant],
        variant === "center" && SIZE[size],
        variant === "side" && "max-w-xl",
      )}
    >
      <div className={cx("flex flex-col", variant === "center" ? "max-h-[calc(100dvh-2rem)]" : "h-full")}>
        {!bare && (
          <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
            <div className="min-w-0">
              <h2 id={titleId} className="text-base font-semibold text-balance">
                {title}
              </h2>
              {description && <p className="mt-0.5 text-[13px] text-muted">{description}</p>}
            </div>
            <IconButton label="Close" onClick={onClose} className="-mt-1 -mr-2">
              <X className="size-4" />
            </IconButton>
          </header>
        )}
        <div className={cx("scroll-thin min-h-0 flex-1 overflow-y-auto", !bare && "px-5 py-4")}>
          {children}
        </div>
        {footer && (
          <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-3">
            {footer}
          </footer>
        )}
      </div>
    </dialog>
  );
}
