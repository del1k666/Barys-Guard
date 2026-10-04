import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { Spinner } from "../components/Spinner";
import { ru } from "../i18n/ru";
import { useSession } from "./session";

/**
 * Охрана маршрутов консоли.
 *
 * Пока личность не подтверждена, содержимое не рисуется вовсе: мелькнувший
 * на долю секунды список агентов — это уже показ данных тому, кто мог и
 * не войти.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useSession();
  const location = useLocation();

  if (loading) {
    return <Spinner label={ru.login.checking} />;
  }

  if (user === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  // Временный пароль знает не только его владелец: тот, кто выдал, тоже.
  // До смены оператору доступен ровно один экран.
  if (user.must_change_password && location.pathname !== "/password") {
    return <Navigate to="/password" replace />;
  }

  return <>{children}</>;
}
