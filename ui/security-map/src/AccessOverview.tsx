import { useMemo, useState } from "react";
import type { MapViewModel } from "./types";
import { buildAccessSummary } from "./access";

export type AccessOverviewProps = {
  model: MapViewModel;
  onSelectRoute: (routeId: string) => void;
};

const severityLabel: Record<string, string> = {
  critical: "критично",
  high: "высокий",
  medium: "средний",
  low: "низкий",
  info: "инфо",
};

export function AccessOverview({ model, onSelectRoute }: AccessOverviewProps) {
  const summary = useMemo(() => buildAccessSummary(model), [model]);
  const [open, setOpen] = useState(true);

  if (!summary.routeCount) return null;

  return (
    <section className="sm-access" aria-label="Доступы по маршрутам">
      <div className="sm-access-head">
        <div>
          <h2>Доступы по маршрутам</h2>
          <p>
            {summary.routeCount} маршрутов · {summary.publicCount} разрешены правилом ·
            {" "}{summary.unknownCount} без guard · {summary.withFindingsCount} с проблемами.
            Сортировка: разрешённые, затем без guard, затем по риску.
          </p>
        </div>
        <button type="button" onClick={() => setOpen((value) => !value)}>
          {open ? "Свернуть" : "Показать"}
        </button>
      </div>
      {open ? (
        <div className="sm-access-scroll">
          <table className="sm-access-table">
            <thead>
              <tr>
                <th>Доступ</th>
                <th>Маршрут</th>
                <th>Обработчик</th>
                <th>Проблемы</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {summary.rows.map((row) => (
                <tr key={row.routeId} className={row.unchecked ? "is-unchecked" : ""}>
                  <td>
                    {row.access === "public" ? (
                      <span className="sm-access-badge is-open">разрешён правилом</span>
                    ) : row.access === "guarded" ? (
                      <span className="sm-access-badge is-ok">guard</span>
                    ) : (
                      <span className="sm-access-badge is-warn">guard не найден</span>
                    )}
                  </td>
                  <td>
                    <span className="sm-access-method">{row.method}</span>
                    <code>{row.path}</code>
                  </td>
                  <td>
                    <span>{row.handler}</span>
                    {row.access === "public" ? (
                      <small className="sm-access-guard is-warn">
                        правило доступа: {row.permitLabels.join(" · ")}
                      </small>
                    ) : row.guards.length ? (
                      <small className="sm-access-guard">{row.guards.join(" · ")}</small>
                    ) : (
                      <small className="sm-access-guard is-warn">
                        в статическом графе связь requires не найдена — это не
                        доказательство, что endpoint открыт
                      </small>
                    )}
                  </td>
                  <td>
                    {row.findings.length ? (
                      <>
                        <b>{row.findings.length}</b>
                        {row.highestSeverity ? (
                          <span className={`sm-access-severity is-${row.highestSeverity}`}>
                            {severityLabel[row.highestSeverity] ?? row.highestSeverity}
                          </span>
                        ) : null}
                      </>
                    ) : (
                      <span className="sm-access-empty">—</span>
                    )}
                  </td>
                  <td>
                    <button type="button" onClick={() => onSelectRoute(row.routeId)}>
                      В графе
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
