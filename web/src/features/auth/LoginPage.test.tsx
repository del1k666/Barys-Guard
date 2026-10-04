import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { json, mockApi, OPERATOR, renderPage } from "../../test/utils";
import { LoginPage } from "./LoginPage";

const unauthenticated = json(401, { detail: "authentication required" });
const where = { route: "/login", path: "/login" };

describe("LoginPage", () => {
  it("успешный вход ведёт на главную", async () => {
    const { calls } = mockApi({
      "GET /auth/me": unauthenticated,
      "POST /auth/login": json(200, OPERATOR),
    });
    renderPage(<LoginPage />, where);

    await userEvent.type(await screen.findByLabelText("Логин"), " ivanov ");
    await userEvent.type(screen.getByLabelText("Пароль"), "секрет-пароль-12");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/));
    const login = calls.find((call) => call.path === "POST /auth/login");
    expect(login?.body).toEqual({ username: "ivanov", password: "секрет-пароль-12" });
  });

  it("возвращает на страницу, с которой оператора увели на вход", async () => {
    mockApi({
      "GET /auth/me": unauthenticated,
      "POST /auth/login": json(200, OPERATOR),
    });
    renderPage(<LoginPage />, {
      route: { pathname: "/login", state: { from: "/agents" } },
      path: "/login",
    });

    await userEvent.type(await screen.findByLabelText("Логин"), "ivanov");
    await userEvent.type(screen.getByLabelText("Пароль"), "секрет-пароль-12");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/agents"));
  });

  it("неверные данные показывают одно общее сообщение и остаются на форме", async () => {
    mockApi({
      "GET /auth/me": unauthenticated,
      "POST /auth/login": json(401, { detail: "invalid credentials" }),
    });
    renderPage(<LoginPage />, where);

    await userEvent.type(await screen.findByLabelText("Логин"), "ivanov");
    await userEvent.type(screen.getByLabelText("Пароль"), "не-тот");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByText("Неверный логин или пароль")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/login");
    expect(screen.getByRole("button", { name: "Войти" })).toBeEnabled();
  });

  it("уже вошедшего сразу уводит с экрана входа", async () => {
    mockApi({ "GET /auth/me": json(200, OPERATOR) });
    renderPage(<LoginPage />, where);

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/));
  });
});
