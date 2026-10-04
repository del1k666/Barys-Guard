import { useState } from "react";

import type { CommandType } from "../../api/types";
import { Button } from "../../components/Button";
import { Modal } from "../../components/Modal";
import { Select } from "../../components/Select";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import { useSendCommand } from "./queries";

const TYPES: CommandType[] = ["ping", "refresh_config", "collect_diagnostics"];

interface Props {
  agentId: string;
  hostname: string;
  open: boolean;
  onClose: () => void;
}

export function SendCommandDialog({ agentId, hostname, open, onClose }: Props) {
  const [type, setType] = useState<CommandType>("ping");
  const send = useSendCommand(agentId);
  const toast = useToast();

  // Пока запрос в пути, окно не закрывается; выбор не переживает окно.
  function close() {
    if (send.isPending) return;
    setType("ping");
    send.reset();
    onClose();
  }

  async function confirm() {
    try {
      await send.mutateAsync(type);
      toast.notify(ru.agent.commandSent, "ok");
      setType("ping");
      send.reset();
      onClose();
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  return (
    <Modal
      open={open}
      title={ru.agent.commandTitle}
      onClose={close}
      footer={
        <>
          <Button disabled={send.isPending} onClick={close}>{ru.common.cancel}</Button>
          <Button variant="primary" loading={send.isPending} onClick={confirm}>
            {ru.agent.commandSend}
          </Button>
        </>
      }
    >
      <p>{hostname}</p>
      <Select
        label={ru.agent.commandType}
        value={type}
        options={TYPES.map((value) => ({ value, label: ru.commandType[value] ?? value }))}
        onChange={(event) => setType(event.target.value as CommandType)}
      />
    </Modal>
  );
}
