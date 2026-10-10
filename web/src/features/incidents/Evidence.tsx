import { useState } from "react";

import type { IncidentMatch } from "../../api/types";
import { Button } from "../../components/Button";
import { ru } from "../../i18n/ru";
import styles from "./incidents.module.css";
import { ruleName } from "./ruleName";

const FIRST = 3;

function Rule({ match }: { match: IncidentMatch }) {
  const t = ru.incidents.detail.evidence;
  const [expanded, setExpanded] = useState(false);
  const fragments = match.fragments ?? [];
  const visible = expanded ? fragments : fragments.slice(0, FIRST);
  return (
    <div className={styles.evidenceRule}>
      <div className={styles.ruleHead}>
        <span className={styles.ruleName}>{ruleName(match)}</span>
        <span className={styles.muted}>{t.matches(match.count)}</span>
      </div>
      {fragments.length === 0 ? (
        <p className={styles.muted}>{t.none}</p>
      ) : (
        <>
          <ul className={styles.fragments}>
            {visible.map((fragment, index) => (
              <li key={index} className={styles.fragment}>
                <span>{fragment.before}</span>
                <mark className={styles.hit}>{fragment.hit}</mark>
                <span>{fragment.after}</span>
              </li>
            ))}
          </ul>
          {match.count > fragments.length ? (
            <span className={styles.muted}>{t.shown(fragments.length, match.count)}</span>
          ) : null}
          {fragments.length > FIRST ? (
            <Button onClick={() => setExpanded((value) => !value)}>
              {expanded ? t.less : t.more}
            </Button>
          ) : null}
        </>
      )}
    </div>
  );
}

export function Evidence({ matches }: { matches: IncidentMatch[] }) {
  return (
    <section className={styles.block}>
      <h3 className={styles.blockTitle}>{ru.incidents.detail.evidence.title}</h3>
      {[...matches].sort((a, b) => b.points - a.points).map((match) => (
        <Rule key={match.rule_key} match={match} />
      ))}
    </section>
  );
}
