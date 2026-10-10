import type { IncidentMatch } from "../../api/types";
import { ru } from "../../i18n/ru";

export function ruleName(match: IncidentMatch): string {
  return ru.incidents.rules[match.rule_key] ?? (match.rule_title || match.rule_key);
}
