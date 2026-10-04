import { ApiError } from "../api/client";
import { ru } from "../i18n/ru";

/** Одна фраза для любого сбоя, которую можно показать оператору. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return ru.errors.forbidden;
    if (error.status === 404) return ru.errors.notFound;
    return error.message;
  }

  // fetch при обрыве сети бросает именно TypeError.
  if (error instanceof TypeError) return ru.errors.network;

  return ru.errors.unknown;
}
