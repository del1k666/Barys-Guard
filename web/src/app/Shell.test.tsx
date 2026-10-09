import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ADMIN, FORCED, json, mockApi, OPERATOR, renderPage } from "../test/utils";
import { Shell } from "./Shell";

function shellPage() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<div>содержимое</div>} />
      </Route>
    </Routes>
  );
}

describe("Shell", () => {
  it("показывает навигацию, имя и роль оператора", async () => {
    mockApi({ "GET /auth/me": json(200, ADMIN) });

    renderPage(shellPage());

    expect(await screen.findByText("содержимое")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Обзор" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Агенты" })).toHaveAttribute("href", "/agents");
    expect(screen.getByRole("link", { name: "Инциденты" })).toHaveAttribute("href", "/incidents");
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getByText("Администратор")).toBeInTheDocument();
  });

  it("не предлагает разделов, пока не сменён временный пароль", async () => {
    mockApi({ "GET /auth/me": json(200, FORCED) });

    renderPage(shellPage());

    expect(await screen.findByText("содержимое")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Агенты" })).not.toBeInTheDocument();
  });

  it("выход завершает сессию и ведёт на вход", async () => {
    const { calls } = mockApi({
      "GET /auth/me": json(200, OPERATOR),
      "POST /auth/logout": json(204, null),
    });

    renderPage(shellPage());
    await screen.findByText("содержимое");

    await userEvent.click(screen.getByRole("button", { name: "Выйти" }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/login"));
    expect(calls.some((call) => call.path === "POST /auth/logout")).toBe(true);
  });

  it("сбой выхода показывает ошибку и оставляет оператора на месте", async () => {
    const { calls } = mockApi({
      "GET /auth/me": json(200, OPERATOR),
      "POST /auth/logout": json(500, { detail: "logout failed" }),
    });

    renderPage(shellPage());
    await screen.findByText("содержимое");

    await userEvent.click(screen.getByRole("button", { name: "Выйти" }));

    expect(await screen.findByText("logout failed")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/);
    expect(screen.getByText("содержимое")).toBeInTheDocument();
    expect(calls.filter((call) => call.path === "POST /auth/logout")).toHaveLength(1);
  });

  it("администратору показывает ссылку «Правила»", async () => {
    mockApi({ "GET /auth/me": json(200, ADMIN) });

    renderPage(shellPage());

    expect(await screen.findByRole("link", { name: "Правила" })).toHaveAttribute("href", "/rules");
  });

  it("оператору не показывает ссылку «Правила»", async () => {
    mockApi({ "GET /auth/me": json(200, OPERATOR) });

    renderPage(shellPage());

    await screen.findByText("содержимое");
    expect(screen.queryByRole("link", { name: "Правила" })).not.toBeInTheDocument();
  });
});
