import { ru } from "../i18n/ru";

export function Spinner({ label = ru.common.loading }: { label?: string }) {
  return (
    <div className="spinner" role="status" aria-live="polite">
      <span className="spinner__dot" />
      <span className="spinner__label">{label}</span>
    </div>
  );
}
