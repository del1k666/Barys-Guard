import { useState } from "react";

import type { RuleSummary, RuleUpdateRequest } from "../../api/types";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Modal } from "../../components/Modal";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import styles from "./rules.module.css";
import { RuleTester, type TestResult } from "./RuleTester";
import { TermsEditor } from "./TermsEditor";
import { VersionsList } from "./VersionsList";
import { useUpdateRule } from "./queries";

export function RuleDialog({ rule, onClose }: { rule: RuleSummary | null; onClose: () => void }) {
  return (
    <Modal open={rule !== null} title={ru.rules.dialog.editTitle} onClose={onClose}>
      {rule ? <Form rule={rule} onClose={onClose} /> : null}
    </Modal>
  );
}

function Form({ rule, onClose }: { rule: RuleSummary; onClose: () => void }) {
  const t = ru.rules.dialog;
  const update = useUpdateRule(rule.id);
  const toast = useToast();
  const [title, setTitle] = useState(rule.title);
  const [weight, setWeight] = useState(String(rule.weight));
  const [cap, setCap] = useState(String(rule.cap));
  const [pattern, setPattern] = useState(rule.pattern ?? "");
  const [ignoreCase, setIgnoreCase] = useState(rule.ignore_case);
  const [tested, setTested] = useState<TestResult | null>(null);

  const isRegex = rule.kind === "regex" && !rule.builtin;
  const patternChanged =
    isRegex && (pattern !== (rule.pattern ?? "") || ignoreCase !== rule.ignore_case);
  const needsTest = patternChanged && !(tested && tested.ok && tested.count > 0);

  async function save() {
    // Сервер не принимает null в этих полях: отправляем только изменённое.
    const body: RuleUpdateRequest = {};
    if (title !== rule.title) body.title = title;
    if (Number(weight) !== rule.weight) body.weight = Number(weight);
    if (Number(cap) !== rule.cap) body.cap = Number(cap);
    if (patternChanged) {
      body.pattern = pattern;
      body.ignore_case = ignoreCase;
      body.test_text = tested?.text;
    }
    try {
      await update.mutateAsync(body);
      toast.notify(t.saved, "ok");
      onClose();
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  return (
    <>
      <p>{t.versionLabel(rule.version)}</p>
      <Input label={t.name} value={title} onChange={(event) => setTitle(event.target.value)} />
      <Input
        label={t.weight}
        hint={t.weightHint}
        type="number"
        min={1}
        max={100}
        value={weight}
        onChange={(event) => setWeight(event.target.value)}
      />
      <Input
        label={t.cap}
        hint={t.capHint}
        type="number"
        min={1}
        max={50}
        value={cap}
        onChange={(event) => setCap(event.target.value)}
      />

      {isRegex ? (
        <>
          <Input
            label={t.pattern}
            hint={t.patternHint}
            value={pattern}
            onChange={(event) => setPattern(event.target.value)}
          />
          <label>
            <input
              type="checkbox"
              checked={ignoreCase}
              onChange={(event) => setIgnoreCase(event.target.checked)}
            />{" "}
            {t.ignoreCase}
          </label>
          <RuleTester kind="regex" pattern={pattern} ignoreCase={ignoreCase} onResult={setTested} />
          {needsTest ? <p role="note">{t.testRequired}</p> : null}
        </>
      ) : null}

      {rule.kind === "dictionary" ? <TermsEditor ruleId={rule.id} /> : null}
      <VersionsList ruleId={rule.id} />

      <div className={styles.actions}>
        <Button
          variant="primary"
          loading={update.isPending}
          disabled={needsTest}
          onClick={() => void save()}
        >
          {t.save}
        </Button>
        <Button onClick={onClose}>{ru.common.cancel}</Button>
      </div>
    </>
  );
}
