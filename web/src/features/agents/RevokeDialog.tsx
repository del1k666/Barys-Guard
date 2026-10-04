import { useState } from "react";

import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Modal } from "../../components/Modal";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import { useRevokeAgent } from "./queries";

interface Props {
  agentId: string;
  hostname: string;
  open: boolean;
  onClose: () => void;
}

export function RevokeDialog({ agentId, hostname, open, onClose }: Props) {
  const [reason, setReason] = useState("");
  const revoke = useRevokeAgent(agentId);
  const toast = useToast();

  // Пока запрос в пути, закрыть окно нельзя: иначе оператор решит, что
  // отменил отзыв, который всё равно состоится. Причина не переживает окно.
  function close() {
    if (revoke.isPending) return;
    setReason("");
    revoke.reset();
    onClose();
  }

  async function confirm() {
    try {
      await revoke.mutateAsync(reason.trim());
      toast.notify(ru.agent.revoked, "ok");
      setReason("");
      revoke.reset();
      onClose();
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  return (
    <Modal
      open={open}
      title={ru.agent.revokeTitle}
      onClose={close}
      footer={
        <>
          <Button disabled={revoke.isPending} onClick={close}>{ru.common.cancel}</Button>
          <Button
            variant="danger"
            loading={revoke.isPending}
            disabled={reason.trim() === ""}
            onClick={confirm}
          >
            {ru.agent.revokeConfirm}
          </Button>
        </>
      }
    >
      <p>{ru.agent.revokeWarning(hostname)}</p>
      <Input
        label={ru.agent.revokeReason}
        value={reason}
        maxLength={255}
        onChange={(event) => setReason(event.target.value)}
      />
    </Modal>
  );
}
