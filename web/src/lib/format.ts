/** Форматирование для таблиц консоли. */

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Русское склонение: 1 секунда, 2 секунды, 5 секунд. */
function plural(count: number, one: string, few: string, many: string): string {
  const mod100 = count % 100;
  if (mod100 >= 11 && mod100 <= 14) return many;

  const mod10 = count % 10;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

export function relativeTime(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return "—";

  const moment = new Date(value).getTime();
  if (Number.isNaN(moment)) return "—";

  const elapsed = now.getTime() - moment;

  // Часы хоста могут спешить, и heartbeat приходит «из будущего».
  // Показывать «через 30 секунд» бессмысленно — это просто свежая отметка.
  if (elapsed < SECOND) return "только что";

  if (elapsed < MINUTE) {
    const seconds = Math.floor(elapsed / SECOND);
    return `${seconds} ${plural(seconds, "секунду", "секунды", "секунд")} назад`;
  }

  if (elapsed < HOUR) {
    const minutes = Math.floor(elapsed / MINUTE);
    return `${minutes} ${plural(minutes, "минуту", "минуты", "минут")} назад`;
  }

  if (elapsed < DAY) {
    const hours = Math.floor(elapsed / HOUR);
    return `${hours} ${plural(hours, "час", "часа", "часов")} назад`;
  }

  const days = Math.floor(elapsed / DAY);
  return `${days} ${plural(days, "день", "дня", "дней")} назад`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";

  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return "—";

  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(moment);
}

/** Дрейф часов агента. Знак важен: минус — часы хоста отстают. */
export function formatSkew(milliseconds: number): string {
  if (milliseconds === 0) return "0 мс";

  // Настоящий минус, а не дефис: в таблице со столбцом чисел это читается.
  const sign = milliseconds < 0 ? "−" : "+";
  const magnitude = Math.abs(milliseconds);

  if (magnitude < SECOND) return `${sign}${magnitude} мс`;

  const seconds = magnitude / SECOND;
  const rendered = Number.isInteger(seconds) ? String(seconds) : seconds.toFixed(1);
  return `${sign}${rendered} с`;
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat("ru-RU").format(value);
}
