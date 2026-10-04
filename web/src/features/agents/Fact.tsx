import type { ReactNode } from "react";

import page from "../../styles/page.module.css";

export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={page.fact}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
