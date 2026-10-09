export interface Segment {
  text: string;
  hit: boolean;
}

/**
 * Режет текст на куски по позициям совпадений; перекрытия и выход за границы игнорируются.
 * Позиции — в кодовых точках Unicode (так их отдаёт сервер), а не в единицах UTF-16.
 */
export function highlight(text: string, spans: { start: number; end: number }[]): Segment[] {
  const points = Array.from(text);
  const sorted = [...spans].sort((a, b) => a.start - b.start);
  const result: Segment[] = [];
  let cursor = 0;

  for (const { start, end } of sorted) {
    if (start < cursor || end <= start || start >= points.length) continue;
    const to = Math.min(end, points.length);
    if (start > cursor) result.push({ text: points.slice(cursor, start).join(""), hit: false });
    result.push({ text: points.slice(start, to).join(""), hit: true });
    cursor = to;
  }
  if (cursor < points.length) result.push({ text: points.slice(cursor).join(""), hit: false });
  return result;
}
