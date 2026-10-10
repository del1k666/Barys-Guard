import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { Button } from "../components/Button";
import { useToast } from "../components/Toast";
import { ru } from "../i18n/ru";
import { describeError } from "../lib/errors";
import styles from "./Shell.module.css";
import { useSession } from "./session";

function linkClass({ isActive }: { isActive: boolean }): string {
  return [styles.link, isActive ? styles.active : undefined].filter(Boolean).join(" ");
}

export function Shell() {
  const { user, loading, logout } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const [open, setOpen] = useState(false);

  // После перехода выдвижное меню на узком экране закрывается.
  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  // Временный пароль знает и тот, кто его выдал: до смены доступен
  // один экран, и ссылкам на остальные здесь делать нечего.
  const locked = user?.must_change_password === true;

  async function leave() {
    try {
      await logout();
    } catch (error) {
      // Сессия на сервере могла остаться жива: молча уводить на вход нельзя.
      toast.notify(describeError(error), "danger");
      return;
    }
    navigate("/login", { replace: true });
  }

  // Пока неизвестно, кто вошёл, ни навигации, ни страницы показывать нельзя:
  // иначе оператору с временным паролем на мгновение достанутся все разделы.
  if (loading) return null;

  return (
    <div className={styles.shell}>
      <header className={styles.topbar}>
        <span className={styles.brand}>{ru.app.name}</span>
        <button
          type="button"
          className={styles.menuButton}
          aria-expanded={open}
          aria-controls="sidebar"
          onClick={() => setOpen((value) => !value)}
        >
          {ru.nav.menu}
        </button>
      </header>

      <aside id="sidebar" className={open ? `${styles.sidebar} ${styles.open}` : styles.sidebar}>
        <div className={styles.brandLarge}>{ru.app.name}</div>

        {locked ? null : (
          <nav aria-label={ru.nav.main} className={styles.nav}>
            <NavLink to="/" end className={linkClass}>
              {ru.nav.overview}
            </NavLink>
            <NavLink to="/agents" className={linkClass}>
              {ru.nav.agents}
            </NavLink>
            <NavLink to="/events" className={linkClass}>
              {ru.nav.events}
            </NavLink>
            <NavLink to="/incidents" className={linkClass}>
              {ru.nav.incidents}
            </NavLink>
            {user?.role === "admin" ? (
              <NavLink to="/rules" className={linkClass}>
                {ru.nav.rules}
              </NavLink>
            ) : null}
          </nav>
        )}

        <div className={styles.account}>
          <div className={styles.username}>{user?.username}</div>
          <div className={styles.role}>{user ? (ru.nav.roles[user.role] ?? user.role) : ""}</div>
          <Button onClick={leave}>{ru.nav.logout}</Button>
        </div>
      </aside>

      <main className={styles.content}>
        <div key={location.pathname} className={styles.page}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}
