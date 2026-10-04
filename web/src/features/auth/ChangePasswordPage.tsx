import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api } from "../../api/client";
import { useSession } from "../../app/session";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import styles from "./auth.module.css";

type Errors = Partial<Record<"current" | "next" | "repeat" | "form", string>>;

export function ChangePasswordPage() {
  const { user, refresh } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (next !== repeat) {
      setErrors({ repeat: ru.password.mismatch });
      return;
    }

    setBusy(true);
    setErrors({});

    try {
      await api.post<null>("/auth/password", { current_password: current, new_password: next });
      // До перечитывания в кеше всё ещё стоит must_change_password, и
      // охрана маршрутов вернула бы оператора на этот же экран.
      await refresh();
      toast.notify(ru.password.done, "ok");
      navigate("/", { replace: true });
    } catch (failure) {
      setBusy(false);

      if (failure instanceof ApiError) {
        if (failure.status === 400) {
          setErrors({ current: ru.password.wrongCurrent });
          return;
        }
        const fields = failure.fieldErrors;
        if (Object.keys(fields).length > 0) {
          setErrors({ current: fields.current_password, next: fields.new_password });
          return;
        }
      }
      setErrors({ form: describeError(failure) });
    }
  }

  return (
    <main className={styles.pageInline}>
      <form className={styles.card} onSubmit={submit}>
        <h1 className={styles.title}>{ru.password.title}</h1>
        {user?.must_change_password ? <p className={styles.notice}>{ru.password.forced}</p> : null}
        <Input
          label={ru.password.current}
          type="password"
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
          autoComplete="current-password"
          error={errors.current}
          required
        />
        <Input
          label={ru.password.next}
          type="password"
          value={next}
          onChange={(event) => setNext(event.target.value)}
          autoComplete="new-password"
          hint={ru.password.hint}
          error={errors.next}
          required
        />
        <Input
          label={ru.password.repeat}
          type="password"
          value={repeat}
          onChange={(event) => setRepeat(event.target.value)}
          autoComplete="new-password"
          error={errors.repeat}
          required
        />
        {errors.form ? (
          <p className={styles.error} role="alert">
            {errors.form}
          </p>
        ) : null}
        <Button type="submit" variant="primary" loading={busy}>
          {ru.password.submit}
        </Button>
      </form>
    </main>
  );
}
