import type { IncidentMatch } from "../../api/types";
import { ru } from "../../i18n/ru";
import styles from "./incidents.module.css";
import { ruleName } from "./ruleName";

export function WhyTriggered({ matches, score }: { matches: IncidentMatch[]; score: number }) {
  const t = ru.incidents.detail.why;
  const ordered = [...matches].sort((a, b) => b.points - a.points);
  const total = Math.max(score, ordered.reduce((sum, m) => sum + m.points, 0), 1);
  return (
    <section className={styles.block}>
      <h3 className={styles.blockTitle}>{t.title}</h3>
      <p className={styles.note}>{t.summary(score)}</p>
      <ul className={styles.rules}>
        {ordered.map((match) => {
          const weight = match.weight ?? 0;
          const cap = match.cap ?? 0;
          return (
            <li key={match.rule_key} className={styles.rule}>
              <div className={styles.ruleHead}>
                <span className={styles.ruleName}>{ruleName(match)}</span>
                <span className={styles.mono}>
                  {weight > 0 ? `${t.formula(weight, match.count, cap)} = ${match.points}` : match.points}
                </span>
              </div>
              <div className={styles.bar} aria-hidden>
                <div className={styles.barFill} style={{ width: `${Math.round((match.points / total) * 100)}%` }} />
              </div>
              {cap > 0 && match.count > cap ? <span className={styles.muted}>{t.capped(cap)}</span> : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
