import { useToolLatency } from "../lib/queries";
import { cell, headCell, Panel, QueryView, TableWrap } from "./ui";

export function LatencyPanel() {
  const latency = useToolLatency();
  return (
    <Panel title="Tool latency">
      <p className="mb-3 text-sm text-muted">
        Server-side times from successful requests only; the caller also waits for the network and the platform.
      </p>
      <QueryView query={latency} isEmpty={(rows) => rows.length === 0} empty="No tool requests recorded yet.">
        {(rows) => (
          <TableWrap label="Server time per tool, in milliseconds">
            <thead>
              <tr>
                <th scope="col" className={headCell}>
                  Tool
                </th>
                <th scope="col" className={`${headCell} text-right`}>
                  Requests
                </th>
                <th scope="col" className={`${headCell} text-right`}>
                  p50 (ms)
                </th>
                <th scope="col" className={`${headCell} text-right`}>
                  p95 (ms)
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.tool}>
                  <th scope="row" className={`${cell} font-mono font-normal`}>
                    {row.tool}
                  </th>
                  <td className={`${cell} text-right`}>{row.count}</td>
                  <td className={`${cell} text-right`}>{row.p50_ms.toFixed(1)}</td>
                  <td className={`${cell} text-right`}>{row.p95_ms.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </QueryView>
    </Panel>
  );
}
