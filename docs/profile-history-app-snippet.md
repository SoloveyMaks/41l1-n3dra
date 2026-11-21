# Интеграция профиля и истории: правка App.tsx (root)

`AccountModal` сам грузит `GET /api/profile/history` с фильтрами и на клик по запуску
зовёт `onSelectRun(runId)` + `onClose()`. В `App.tsx` обработчик сейчас только
загружает run, но не переключает вкладку, поэтому клик закрывает профиль и
оставляет пользователя в «Редакторе». Это единственное место, которое нужно
поправить в общем файле.

Правка — в неадминской ветке, у вызова `AccountModal` (район строки 1034):

```diff
           onClose={() => setIsAccountOpen(false)}
           onLogout={() => void handleLogout()}
-          onSelectRun={handleSelectRun}
+          onSelectRun={(runId) => {
+            handleSelectRun(runId);
+            setActiveTab('live');
+          }}
         />
```

`handleSelectRun` уже сбрасывает прошлый run, подключает SSE и грузит детали;
`setActiveTab('live')` показывает «Ход проверки» этого запуска. Для админской
ветки отдельный `AccountModal` не монтируется, правка одна.

Ничего больше в `App.tsx`/`UserDashboard.tsx` не требуется: пропсы `history`,
`loading`, `error` остаются в сигнатуре, модалка их игнорирует и использует
свой запрос, поэтому типы и вызов компилируются без изменений.