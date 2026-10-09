import { ru } from "../../i18n/ru";
import page from "../../styles/page.module.css";
import styles from "./rules.module.css";

export function GuideTab() {
  return (
    <div>
      {ru.rules.guide.sections.map((section) => (
        <section key={section.title} className={page.section}>
          <h2 className={page.sectionTitle}>{section.title}</h2>
          {section.paragraphs.map((text) => (
            <p key={text}>{text}</p>
          ))}
          {section.examples ? (
            <ul>
              {section.examples.map((example) => (
                <li key={example.pattern}>
                  <code className={styles.mono}>{example.pattern}</code> — {example.note}
                </li>
              ))}
            </ul>
          ) : null}
          {section.items ? (
            <ul>
              {section.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : null}
        </section>
      ))}
    </div>
  );
}
