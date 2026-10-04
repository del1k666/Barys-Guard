import { Spinner } from "../../components/Spinner";
import { EmptyState, ErrorState } from "../../components/States";
import { CommandStatusBadge } from "../../components/StatusBadge";
import { Table } from "../../components/Table";
import { ru } from "../../i18n/ru";
import { formatDateTime, shortJson } from "../../lib/format";
import page from "../../styles/page.module.css";
import { useAgentCommands } from "./queries";

export function CommandHistory({ agentId }: { agentId: string }) {
  const commands = useAgentCommands(agentId);

  if (commands.isPending) return <Spinner label={ru.common.loading} />;
  if (commands.isError) {
    return <ErrorState error={commands.error} onRetry={() => void commands.refetch()} />;
  }
  if (commands.data.items.length === 0) return <EmptyState title={ru.agent.noCommands} />;

  const columns = ru.agent.historyColumns;

  return (
    <Table caption={ru.agent.history}>
      <thead>
        <tr>
          <th>{columns.type}</th>
          <th>{columns.status}</th>
          <th>{columns.created}</th>
          <th>{columns.completed}</th>
          <th>{columns.result}</th>
        </tr>
      </thead>
      <tbody>
        {commands.data.items.map((command) => (
          <tr key={command.id}>
            <td>{ru.commandType[command.type] ?? command.type}</td>
            <td>
              <CommandStatusBadge status={command.status} />
            </td>
            <td>{formatDateTime(command.created_at)}</td>
            <td>{formatDateTime(command.completed_at)}</td>
            <td className={page.mono}>{shortJson(command.result)}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
