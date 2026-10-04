import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { FORCED, json, mockApi, OPERATOR, renderPage } from "../../test/utils";
import { ChangePasswordPage } from "./ChangePasswordPage";

const where = { route: "/password", path: "/password" };

async function fill(current: string, next: string, repeat: string) {
  await userEvent.type(await screen.findByLabelText("Текущий пароль"), current);
  await userEvent.type(screen.getByLabelText("Новый пароль"), next);
  await userEvent.type(screen.getByLabelText("Повторите новый пароль"), repeat);
  await userEvent.click(screen.getByRole("button", { name: "Сменить пароль" }));
}

describe("ChangePasswordPage", () => {
  it("объясняет принудительную смену", async () => {
    mockApi({ "GET /auth/me": json(200, FORCED) });
    renderPage(<ChangePasswordPage />, where);

    expect(await screen.findByText(/временный пароль/i)).toBeInTheDocument();
  });

  it("не отправляет запрос, если повтор не совпал", async () => {
    const { calls } = mockApi({ "GET /auth/me": json(200, FORCED) });
    renderPage(<ChangePasswordPage />, where);

    await fill("временный", "новый-пароль-123", "другой-пароль-123");

    expect(await screen.findByText("Пароли не совпадают")).toBeInTheDocument();
    expect(calls.some((call) => call.path === "POST /auth/password")).toBe(false);
  });

  it("неверный текущий пароль подсвечивается у своего поля", async () => {
    mockApi({
      "GET /auth/me": json(200, FORCED),
      "POST /auth/password": json(400, { detail: "current password does not match" }),
    });
    renderPage(<ChangePasswordPage />, where);

    await fill("не-тот", "новый-пароль-123", "новый-пароль-123");

    expect(await screen.findByText("Текущий пароль неверен")).toBeInTheDocument();
  });

  it("слишком короткий пароль показывает ответ сервера у поля нового пароля", async () => {
    mockApi({
      "GET /auth/me": json(200, FORCED),
      "POST /auth/password": json(422, {
        detail: [{ loc: ["body", "new_password"], msg: "String should have at least 12 characters" }],
      }),
    });
    renderPage(<ChangePasswordPage />, where);

    await fill("временный", "короткий", "короткий");

    expect(await screen.findByText(/at least 12 characters/)).toBeInTheDocument();
  });

  it("после смены перечитывает сессию и уходит на главную", async () => {
    let changed = false;
    const { calls } = mockApi({
      "GET /auth/me": () => json(200, changed ? OPERATOR : FORCED),
      "POST /auth/password": () => {
        changed = true;
        return json(204, null);
      },
    });
    renderPage(<ChangePasswordPage />, where);

    await fill("временный", "новый-пароль-123", "новый-пароль-123");

    expect(await screen.findByText("Пароль изменён")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/));
    const change = calls.find((call) => call.path === "POST /auth/password");
    expect(change?.body).toEqual({
      current_password: "временный",
      new_password: "новый-пароль-123",
    });
  });
});
