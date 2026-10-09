/**
 * Имена для схем контракта.
 *
 * Сами типы не пишутся руками: schema.d.ts порождается из
 * api/gateway-v1.yaml командой `npm run types`. Расхождение консоли
 * с сервером обнаруживает сборка, а не оператор.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type SessionUser = Schemas["SessionUser"];
export type SessionSummary = Schemas["SessionSummary"];

export type AgentSummary = Schemas["AgentSummary"];
export type AgentDetail = Schemas["AgentDetail"];
export type AgentPage = Schemas["AgentPage"];
export type CertificateSummary = Schemas["CertificateSummary"];

export type GroupSummary = Schemas["GroupSummary"];
export type EnrollmentTokenSummary = Schemas["EnrollmentTokenSummary"];
export type EnrollmentTokenResponse = Schemas["EnrollmentTokenResponse"];

export type CommandResponse = Schemas["CommandResponse"];
export type FleetCommand = Schemas["FleetCommand"];
export type CommandType = Schemas["CommandType"];
export type CommandPage = Schemas["CommandPage"];

export type ConfigResponse = Schemas["ConfigResponse"];
export type EffectiveConfigResponse = Schemas["EffectiveConfigResponse"];
export type AgentConfigDocument = Schemas["AgentConfigDocument"];

export type UserSummary = Schemas["UserSummary"];
export type IssuedPasswordResponse = Schemas["IssuedPasswordResponse"];
export type ApiKeyResponse = Schemas["ApiKeyResponse"];

export type AuditEntry = Schemas["AuditEntry"];
export type AuditPage = Schemas["AuditPage"];
export type AuditIntegrity = Schemas["AuditIntegrity"];

export type Overview = Schemas["Overview"];

export type EventSummary = Schemas["EventSummary"];
export type EventPage = Schemas["EventPage"];

export type IncidentSummary = Schemas["IncidentSummary"];
export type IncidentPage = Schemas["IncidentPage"];
export type IncidentDetail = Schemas["IncidentDetail"];
export type IncidentMatch = Schemas["IncidentMatch"];
export type IncidentEventRef = Schemas["IncidentEventRef"];
export type IncidentStatusUpdate = Schemas["IncidentStatusUpdate"];
export type VerdictSummary = Schemas["VerdictSummary"];

export type AgentStatus = "pending" | "active" | "offline" | "quarantined" | "revoked";

export type RuleSummary = Schemas["RuleSummary"];
export type RuleCreateRequest = Schemas["RuleCreateRequest"];
export type RuleUpdateRequest = Schemas["RuleUpdateRequest"];
export type TermItem = Schemas["TermItem"];
export type TermPage = Schemas["TermPage"];
export type RuleVersionItem = Schemas["RuleVersionItem"];
export type RuleTestRequest = Schemas["RuleTestRequest"];
export type RuleTestResponse = Schemas["RuleTestResponse"];
