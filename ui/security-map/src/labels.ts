export const resolutionText = {
  resolved: "подтверждена",
  partial: "частично подтверждена",
  unknown: "связь не установлена",
} as const;

export const severityText: Record<string, string> = {
  critical: "критично",
  high: "высокий риск",
  medium: "средний риск",
  low: "низкий риск",
  info: "информация",
};

export const statusText: Record<string, string> = {
  confirmed: "подтверждено",
  potential: "возможная проблема",
  inconclusive: "не подтверждено",
  open: "открыто",
  dismissed: "отклонено",
};

export const confidenceText: Record<string, string> = {
  high: "высокая уверенность",
  medium: "средняя уверенность",
  low: "низкая уверенность",
};

export const coverageText: Record<string, string> = {
  complete: "Проверенный объём завершён",
  partial: "Проверено неполностью",
  unknown: "Покрытие неизвестно",
  unavailable: "Покрытие недоступно",
};

export const basisText: Record<string, string> = {
  source: "по исходнику",
  policy_assumption: "Предположение, нужно подтверждение",
};

export const edgeKindText: Record<string, string> = {
  routes_to: "Обращается",
  matches: "Подходит под правило",
  permits: "Разрешает",
  requires: "Требует",
  calls: "Вызывает",
  flows_to: "Передаёт данные",
  guards: "Проверяет",
  reads: "Читает",
  writes: "Изменяет",
};

export const plural = (n: number, one: string, few: string, many: string) => {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
};