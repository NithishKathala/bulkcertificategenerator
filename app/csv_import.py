"""CSV parsing and preflight validation for bulk recipient uploads."""

import csv
import io

from pydantic import EmailStr, TypeAdapter, ValidationError


MAX_RECIPIENTS = 1000
EXPECTED_HEADERS = {
    ("name",),
    ("name", "email"),
    ("name", "email", "score_percent"),
}
EMAIL_ADAPTER = TypeAdapter(EmailStr)


def parse_recipient_csv(contents: str) -> list[dict[str, str | float | None]]:
    """Parse recipient rows and report all detectable row errors at once.

    A header is optional. Rows may contain name, optional email, and optional score.
    Duplicate non-empty emails are rejected case-insensitively to avoid issuing
    multiple certificates to the same address in one bulk job.
    """
    try:
        rows = list(csv.reader(io.StringIO(contents)))
    except csv.Error as exc:
        raise ValueError(f"Could not read CSV: {exc}") from exc

    first_row = tuple(cell.strip().casefold() for cell in rows[0]) if rows else ()
    if first_row in EXPECTED_HEADERS:
        rows = rows[1:]
        first_line_number = 2
    else:
        first_line_number = 1

    recipients: list[dict[str, str | float | None]] = []
    errors: list[str] = []
    email_lines: dict[str, int] = {}

    for line_number, row in enumerate(rows, start=first_line_number):
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) not in (1, 2, 3):
            errors.append(f"Line {line_number}: expected name[,email[,score_percent]].")
            continue

        name = row[0].strip()
        email = row[1].strip() if len(row) >= 2 else ""
        score_text = row[2].strip() if len(row) == 3 else ""
        row_has_error = False
        if not name:
            errors.append(f"Line {line_number}: recipient name is required.")
            row_has_error = True

        score: float | None = None
        if score_text:
            try:
                score = float(score_text)
                if not 0 <= score <= 100:
                    raise ValueError
            except ValueError:
                errors.append(f"Line {line_number}: score must be a number from 0 to 100.")
                row_has_error = True

        email_key = email.casefold()
        if email:
            try:
                EMAIL_ADAPTER.validate_python(email)
            except ValidationError:
                errors.append(f"Line {line_number}: email address is invalid.")
                row_has_error = True
            if email_key in email_lines:
                errors.append(
                    f"Line {line_number}: email duplicates line {email_lines[email_key]} "
                    "(email matching ignores letter case)."
                )
                row_has_error = True
            elif not row_has_error:
                email_lines[email_key] = line_number

        if not row_has_error:
            recipient: dict[str, str | float | None] = {"name": name, "score_percent": score}
            if email:
                recipient["email"] = email
            recipients.append(recipient)

    if len(recipients) > MAX_RECIPIENTS:
        errors.append(f"This project supports at most {MAX_RECIPIENTS} recipients per job.")
    if not recipients and not errors:
        errors.append("Add at least one recipient row.")
    if errors:
        raise ValueError("CSV needs attention:\n- " + "\n- ".join(errors))
    return recipients
