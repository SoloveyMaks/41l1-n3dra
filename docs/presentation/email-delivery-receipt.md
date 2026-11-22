# Email delivery receipt

- **Recipient:** `fi@soloveyka.bond` (only recipient; no CC/BCC)
- **Subject:** `NEDRA — гайд 48 страниц и материалы для презентации`
- **Attempted at (UTC):** `2026-11-22T01:37:40+00:00`
- **Completed at (UTC):** `2026-11-22T01:37:44+00:00`
- **Message-ID:** `<179531146070.2632563.3399039257367361512@soloveyka.bond>`
- **SMTP:** `smtp.resend.com:465`, `SMTP_SSL` with default SSL context; sender `brigada@soloveyka.bond`
- **SMTP result:** **accepted** — `send_message` returned no refused recipients (`refused: {}`). This is SMTP acceptance, not independent confirmation of mailbox/inbox delivery.
- **Retries:** none. Exactly one `send_message` call; no blind retry was made.

## Attachments

- `NEDRA-полный-гайд.pdf` — 513 232 bytes, 48 pages
- `NEDRA-полный-гайд.docx` — 125 299 bytes
- `NEDRA-полный-гайд.md` — 232 557 bytes
- `NEDRA-слайды-и-речь.txt` — 12 332 bytes

## Pre-send checks

- All four attachments existed and were non-empty.
- PDF page count was verified as 48.
- Known credential values were searched in the attachment bytes; no hits were found. Values were not printed or stored in this receipt.
- The credential file was read programmatically in memory; the API key was not placed in argv, git, logs, or this receipt.
