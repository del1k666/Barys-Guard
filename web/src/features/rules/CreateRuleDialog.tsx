import { useState } from "react";

import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Modal } from "../../components/Modal";
import { Select } from "../../components/Select";
import { Textarea } from "../../components/Textarea";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import styles from "./rules.module.css";
import { RuleTester, type TestResult } from "./RuleTester";
import { useCreateRule } from "./queries";

type Kind = "dictionary" | "regex";

export function CreateRuleDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} title={ru.rules.dialog.createTitle} onClose={onClose}>
      {open ? <Form onClose={onClose} /> : null}
    </Modal>
  );
}

function Form({ onClose }: { onClose: () => void }) {
  const t = ru.rules.dialog;
  const create = useCreateRule();
  const toast = useToast();
  const [kind, setKind] = useState<Kind>("dictionary");
  const [title, setTitle] = useState("");
  const [weight, setWeight] = useState("20");
  const [cap, setCap] = useState("3");
  const [terms, setTerms] = useState("");
  const [pattern, setPattern] = useState("");
  const [ignoreCase, setIgnoreCase] = useState(false);
  const [tested, setTested] = useState<TestResult | null>(null);

  const regexReady = kind !== "regex" || Boolean(tested && tested.ok && tested.count > 0);

  async function submit() {
    try {
      await create.mutateAsync({
        kind,
        title,
        weight: Number(weight),
        cap: Number(cap),
        // Сгенерированный тип требует ignore_case и для словаря.
        ignore_case: kind === "regex" ? ignoreCase : false,
        ...(kind === "regex"
          ? { pattern, test_text: tested?.text }
          : { terms: terms.split("\n") }),
      });
      toast.notify(t.created, "ok");
      onClose();
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  return (
    <>
      <Select
        label={t.kind}
        value={kind}
        options={[
          { value: "dictionary", label: t.kindOptions.dictionary },
          { value: "regex", label: t.kindOptions.regex },
        ]}
        onChange={(event) => {
          setKind(event.target.value as Kind);
          setTested(null);
        }}
      />
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

      {kind === "dictionary" ? (
        <Textarea
          label={t.terms}
          rows={6}
          value={terms}
          onChange={(event) => setTerms(event.target.value)}
        />
      ) : (
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
          {regexReady ? null : <p role="note">{t.testRequired}</p>}
        </>
      )}

      <div className={styles.actions}>
        <Button
          variant="primary"
          loading={create.isPending}
          disabled={title.trim() === "" || !regexReady}
          onClick={() => void submit()}
        >
          {t.create}
        </Button>
        <Button onClick={onClose}>{ru.common.cancel}</Button>
      </div>
    </>
  );
}
