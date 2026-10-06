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

const SEVERITY_TONES: Record<string, Tone> = {
  info: "neutral",
  low: "info",
  medium: "warn",
  high: "danger",
  critical: "danger",
};

export function SeverityBadge({ severity }: { severity: string }) {
  return (
    <Badge tone={SEVERITY_TONES[severity] ?? "neutral"}>
      {ru.events.severities[severity] ?? severity}
    </Badge>
  );
}
