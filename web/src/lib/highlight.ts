export interface Segment {
  text: string;
  hit: boolean;
}

/** Режет текст на куски по позициям совпадений; перекрытия и выход за границы игнорируются. */
export function highlight(text: string, spans: { start: number; end: number }[]): Segment[] {
  const sorted = [...spans].sort((a, b) => a.start - b.start);
  const result: Segment[] = [];
  let cursor = 0;

  for (const { start, end } of sorted) {
    if (start < cursor || end <= start || start >= text.length) continue;
    const to = Math.min(end, text.length);
    if (start > cursor) result.push({ text: text.slice(cursor, start), hit: false });
    result.push({ text: text.slice(start, to), hit: true });
    cursor = to;
  }
  if (cursor < text.length) result.push({ text: text.slice(cursor), hit: false });
  return result;
}
