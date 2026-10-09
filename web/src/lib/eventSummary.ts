import type { EventSummary } from "../api/types";

type Described = Pick<EventSummary, "channel" | "action" | "subject">;

const text = (value: unknown): string => (typeof value === "string" ? value : "");

/** Одна строка о том, с чем произошло событие. Форма `subject` зависит от канала. */
export function describeEvent({ channel, action, subject }: Described): string {
  const dst = text(subject.dst_path);

  if (channel === "file") {
    if (action === "copy" && dst) return `${text(subject.src_path) || "?"} → ${dst}`;
    if (action === "rename" && dst) return `${text(subject.old_path) || "?"} → ${dst}`;
    return dst || "—";
  }

  if (channel === "usb") {
    const volume = subject.volume;
    const label =
      volume && typeof volume === "object" ? text((volume as Record<string, unknown>).label) : "";
    const parts = [text(subject.drive_letter), label].filter(Boolean);
    return parts.length > 0 ? parts.join(" · ") : "—";
  }

  if (channel === "network") {
    const source = text(subject.src_path);
    if (action === "upload" && source) {
      const service = text(subject.service_name) || text(subject.service);
      return service ? `${source} → ${service}` : source;
    }
    return "—";
  }

  if (channel === "agent") {
    const component = text(subject.component);
    const detail = text(subject.detail);
    if (component && detail) return `${component}: ${detail}`;
    return component || detail || "—";
  }

  return "—";
}
