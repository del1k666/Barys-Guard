import { ru } from "../i18n/ru";
import { formatNumber } from "../lib/format";
import { Button } from "./Button";
import styles from "./Pagination.module.css";

interface PaginationProps {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}

export function Pagination({ total, limit, offset, onChange }: PaginationProps) {
  if (total === 0) return null;

  const from = offset + 1;
  const to = Math.min(offset + limit, total);

  return (
    <nav className={styles.pagination} aria-label={ru.pagination.label}>
      <span>{ru.pagination.range(formatNumber(from), formatNumber(to), formatNumber(total))}</span>
      <div className={styles.buttons}>
        <Button disabled={offset <= 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          {ru.pagination.prev}
        </Button>
        <Button disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
          {ru.pagination.next}
        </Button>
      </div>
    </nav>
  );
}
