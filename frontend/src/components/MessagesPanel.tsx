import { formatDateTime } from "../lib/format";
import { useMessages } from "../lib/queries";
import { Badge, cell, headCell, Panel, QueryView, TableWrap } from "./ui";

export function MessagesPanel() {
  const messages = useMessages();
  return (
    <Panel title="Caller messages">
      <QueryView query={messages} isEmpty={(rows) => rows.length === 0} empty="No messages from callers.">
        {(rows) => (
          <TableWrap label="Messages left for staff">
            <thead>
              <tr>
                {["Name", "Phone", "Message", "Left at", "Handled"].map((name) => (
                  <th key={name} scope="col" className={headCell}>
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td className={cell}>{row.caller_name}</td>
                  <td className={cell}>{row.caller_phone}</td>
                  <td className={`${cell} min-w-48 whitespace-pre-wrap`}>{row.message}</td>
                  <td className={cell}>{formatDateTime(row.created_at)}</td>
                  <td className={cell}>
                    {row.handled_at === null ? (
                      <Badge tone="warn">Not yet</Badge>
                    ) : (
                      <Badge tone="ok">Handled {formatDateTime(row.handled_at)}</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </QueryView>
    </Panel>
  );
}
