import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { ApiError } from "../../api/client";
import { useSession } from "../../app/session";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Spinner } from "../../components/Spinner";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import styles from "./auth.module.css";

export function LoginPage() {
  const { user, loading, login } = useSession();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? "/";

  if (loading) return <Spinner label={ru.login.checking} />;
  if (user) return <Navigate to={from} replace />;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      // После успеха сессия попадает в кеш, и страница сама уходит
      // на <Navigate> выше. Пароль не обрезается: пробелы в нём значимы.
      await login(username.trim(), password);
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.status === 401
          ? ru.login.invalid
          : describeError(failure),
      );
      setBusy(false);
    }
  }

  return (
    <main className={styles.page}>
      <form className={styles.card} onSubmit={submit}>
        <h1 className={styles.title}>{ru.login.title}</h1>
        <Input
          label={ru.login.username}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          autoComplete="username"
          autoFocus
          required
        />
        <Input
          label={ru.login.password}
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          required
        />
        {error ? (
          <p className={styles.error} role="alert">
            {error}
          </p>
        ) : null}
        <Button type="submit" variant="primary" loading={busy}>
          {ru.login.submit}
        </Button>
      </form>
    </main>
  );
}
