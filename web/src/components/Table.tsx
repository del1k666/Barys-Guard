import type { ReactNode } from "react";

import styles from "./Table.module.css";

export function Table({ caption, children }: { caption: string; children: ReactNode }) {
  return (
    <div className={styles.wrap}>
      <table className={styles.table}>
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}
