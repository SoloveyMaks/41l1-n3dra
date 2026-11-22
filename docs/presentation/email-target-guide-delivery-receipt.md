# Email delivery receipt — target-guide edition

- **Recipient:** `fi@soloveyka.bond` (only recipient; no CC/BCC)
- **Subject:** `NEDRA — новая полная редакция гайда`
- **Attempted at (UTC):** `2026-11-22T01:52:54+00:00`
- **Completed at (UTC):** `2026-11-22T01:52:57+00:00`
- **Message-ID:** `<179531237498.2700514.12572341388884558180@soloveyka.bond>`
- **SMTP:** `smtp.resend.com:465`, `SMTP_SSL` with default SSL context; sender `brigada@soloveyka.bond`
- **SMTP result:** **accepted** — `send_message` returned no refused recipients (`refused: {}`). This is SMTP acceptance, not independent confirmation of mailbox/inbox delivery.
- **Retries:** none. Exactly one `send_message` call; no blind retry was made.

## Attachment

- `NEDRA-полный-гайд-целевая-редакция.md` — 241 392 bytes, SHA-256 `378845d7a751100f69a64dec512312f4bd97b27dfde27dd35eed0cb88611b2f9`

## Pre-send checks and scope

- The supplied size and SHA-256 matched the attachment exactly.
- The file was non-empty; known credential values were searched in its bytes and no hits were found. Values were not printed or stored in this receipt.
- Only the new Markdown edition was attached. The previous email and old PDF/DOCX files were not re-sent or forwarded.
- The credential file was read programmatically in memory; the API key was not placed in argv, git, logs, or this receipt.
