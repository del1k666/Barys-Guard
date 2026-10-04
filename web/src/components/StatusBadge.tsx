import { ru } from "../i18n/ru";
import { Badge, type Tone } from "./Badge";

const AGENT_TONES: Record<string, Tone> = {
  active: "ok",
  offline: "warn",
  pending: "info",
  quarantined: "danger",
  revoked: "neutral",
};

const COMMAND_TONES: Record<string, Tone> = {
  queued: "info",
  sent: "info",
  running: "info",
  done: "ok",
  failed: "danger",
  expired: "neutral",
};

// Сервер может прислать статус новее консоли: показываем его как есть
// и нейтральным цветом, а не падаем.
export function AgentStatusBadge({ status }: { status: string }) {
  return <Badge tone={AGENT_TONES[status] ?? "neutral"}>{ru.status.agent[status] ?? status}</Badge>;
}

export function CommandStatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={COMMAND_TONES[status] ?? "neutral"}>{ru.status.command[status] ?? status}</Badge>
  );
}
