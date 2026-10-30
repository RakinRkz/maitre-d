import { useId, useState } from "react";
import { useBookings } from "../lib/queries";
import { Badge, buttonClass, cell, headCell, Panel, QueryView, TableWrap } from "./ui";

export function BookingsPanel() {
  const [date, setDate] = useState("");
  const inputId = useId();
  const bookings = useBookings(date);

  return (
    <Panel
      title={date === "" ? "Today's bookings" : `Bookings for ${date}`}
      actions={
        <div className="flex items-center gap-2">
          <label htmlFor={inputId} className="text-sm text-muted">
            Date (blank is today)
          </label>
          <input
            id={inputId}
            type="date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
            className="rounded-md border border-line bg-surface px-2 py-1 text-sm"
          />
          <button type="button" className={buttonClass} disabled={date === ""} onClick={() => setDate("")}>
            Today
          </button>
        </div>
      }
    >
      <QueryView query={bookings} isEmpty={(rows) => rows.length === 0} empty="No bookings on this day.">
        {(rows) => (
          <TableWrap label="Bookings">
            <thead>
              <tr>
                {["Time", "Table", "Party", "Name", "Phone", "Notes", "Status"].map((name) => (
                  <th key={name} scope="col" className={headCell}>
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.reference} className={row.status === "cancelled" ? "text-muted line-through" : ""}>
                  <td className={cell}>{row.time}</td>
                  <td className={cell}>{row.table}</td>
                  <td className={cell}>{row.party_size}</td>
                  <td className={cell}>{row.guest_name}</td>
                  <td className={cell}>{row.guest_phone}</td>
                  <td className={cell}>{row.notes ?? ""}</td>
                  <td className={cell}>
                    <Badge tone={row.status === "cancelled" ? "neutral" : "ok"}>
                      {row.status === "cancelled" ? "Cancelled" : "Confirmed"}
                    </Badge>
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
