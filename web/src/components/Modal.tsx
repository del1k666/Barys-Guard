import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { ru } from "../i18n/ru";
import styles from "./Modal.module.css";

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  variant?: "dialog" | "drawer";
}

const FOCUSABLE =
  'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

export function Modal({ open, title, onClose, children, footer, variant = "dialog" }: ModalProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  // Родитель обычно передаёт onClose стрелкой; если бы эффект зависел от
  // неё, фокус сбрасывался бы на каждый рендер родителя.
  const closeRef = useRef(onClose);

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;

    const dialog = dialogRef.current;
    const previous = document.activeElement as HTMLElement | null;
    const items = () => Array.from(dialog?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);

    const initial = items()[0];
    if (initial) initial.focus();
    else dialog?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const list = items();
      const first = list[0];
      const last = list[list.length - 1];
      if (!first || !last) {
        event.preventDefault();
        return;
      }

      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === dialog)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previous?.focus();
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      className={`${styles.overlay} ${variant === "drawer" ? styles.drawerOverlay : ""}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeRef.current();
      }}
    >
      <div
        ref={dialogRef}
        className={`${styles.dialog} ${variant === "drawer" ? styles.drawer : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        {variant === "drawer" ? (
          // На узком экране панель занимает всё окно и нажать мимо неё нельзя.
          <div className={styles.header}>
            <h2 id={titleId} className={styles.title}>
              {title}
            </h2>
            <button
              type="button"
              className={styles.close}
              aria-label={ru.common.closePanel}
              title={ru.common.closePanel}
              onClick={() => closeRef.current()}
            >
              <span aria-hidden="true">×</span>
            </button>
          </div>
        ) : (
          <h2 id={titleId} className={styles.title}>
            {title}
          </h2>
        )}
        <div className={styles.body}>{children}</div>
        {footer ? <div className={styles.footer}>{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
