import { describe, expect, it } from "vitest";

import { describeEvent } from "./eventSummary";

describe("describeEvent", () => {
  it("копирование показывает источник и назначение", () => {
    const text = describeEvent({
      channel: "file",
      action: "copy",
      subject: { src_path: "C:\\Docs\\a.xlsx", dst_path: "E:\\a.xlsx" },
    });
    expect(text).toBe("C:\\Docs\\a.xlsx → E:\\a.xlsx");
  });

  it("переименование показывает старое и новое имя", () => {
    const text = describeEvent({
      channel: "file",
      action: "rename",
      subject: { old_path: "C:\\a.txt", dst_path: "C:\\b.txt" },
    });
    expect(text).toBe("C:\\a.txt → C:\\b.txt");
  });

  it("обычное файловое событие — путь назначения", () => {
    expect(
      describeEvent({ channel: "file", action: "create", subject: { dst_path: "C:\\x.txt" } }),
    ).toBe("C:\\x.txt");
  });

  it("USB — диск и метка тома", () => {
    const text = describeEvent({
      channel: "usb",
      action: "mount",
      subject: { drive_letter: "E:", volume: { label: "KINGSTON" } },
    });
    expect(text).toBe("E: · KINGSTON");
  });

  it("служебное событие агента — компонент и подробность", () => {
    const text = describeEvent({
      channel: "agent",
      action: "start",
      subject: { component: "runner", detail: "версия 0.3.1" },
    });
    expect(text).toBe("runner: версия 0.3.1");
  });

  it("незнакомая форма subject не роняет описание", () => {
    expect(describeEvent({ channel: "print", action: "job", subject: { pages: 3 } })).toBe("—");
  });
});
