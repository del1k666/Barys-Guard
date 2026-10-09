import { useEffect, useRef, useState } from "react";

import { Button } from "../../components/Button";
import { Textarea } from "../../components/Textarea";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import { highlight } from "../../lib/highlight";
import page from "../../styles/page.module.css";
import styles from "./rules.module.css";
import { useTestRule } from "./queries";

export interface TestResult {
  ok: boolean;
  count: number;
  text: string;
}

interface Props {
  kind: "regex" | "dictionary";
  pattern?: string;
  ignoreCase?: boolean;
  terms?: string[];
  /** Сообщает родителю результат последней проверки; null — результат устарел. */
  onResult: (result: TestResult | null) => void;
}

export function RuleTester({ kind, pattern, ignoreCase, terms, onResult }: Props) {
  const [text, setText] = useState("");
  const test = useTestRule();
  const [shown, setShown] = useState<{
    spans: { start: number; end: number }[];
    count: number;
    error: string | null;
    text: string;
  } | null>(null);
  const signature = JSON.stringify([kind, pattern, ignoreCase, terms]);
  const checked = useRef<string | null>(null);
  // Актуальная подпись: ответ на устаревший запрос отбрасывается.
  const latest = useRef(signature);

  useEffect(() => {
    latest.current = signature;
  }, [signature]);

  // Изменённый шаблон делает прежнюю проверку недействительной.
  useEffect(() => {
    if (checked.current !== null && checked.current !== signature) {
      onResult(null);
      checked.current = null;
      setShown(null);
    }
  }, [signature, onResult]);

  async function run() {
    const requested = signature;
    try {
      const result = await test.mutateAsync({
        kind,
        pattern,
        ignore_case: ignoreCase ?? false,
        terms,
        text,
      });
      if (latest.current !== requested) return;
      checked.current = requested;
      setShown({ spans: result.matches, count: result.count, error: result.error ?? null, text });
      onResult({ ok: result.ok, count: result.count, text });
    } catch (failure) {
      if (latest.current !== requested) return;
      checked.current = null;
      setShown({ spans: [], count: 0, error: describeError(failure), text });
      onResult(null);
    }
  }

  return (
    <div>
      <h3 className={page.sectionTitle}>{ru.rules.tester.title}</h3>
      <Textarea
        label={ru.rules.tester.text}
        hint={ru.rules.tester.hint}
        rows={5}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <div className={styles.actions}>
        <Button loading={test.isPending} disabled={text === ""} onClick={() => void run()}>
          {ru.rules.tester.run}
        </Button>
      </div>

      {shown ? (
        shown.error ? (
          <p role="alert">{shown.error}</p>
        ) : (
          <>
            <p>{shown.count > 0 ? ru.rules.tester.count(shown.count) : ru.rules.tester.none}</p>
            <div className={styles.sample} aria-label={ru.rules.tester.title}>
              {highlight(shown.text, shown.spans).map((part, index) =>
                part.hit ? (
                  <mark key={index} className={styles.hit}>
                    {part.text}
                  </mark>
                ) : (
                  <span key={index}>{part.text}</span>
                ),
              )}
            </div>
          </>
        )
      ) : null}
    </div>
  );
}
