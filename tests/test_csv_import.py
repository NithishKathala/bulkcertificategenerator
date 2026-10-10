import pytest

from app.csv_import import parse_recipient_csv


def test_parse_headered_csv_with_quoted_name_and_optional_email():
    recipients = parse_recipient_csv(
        'name,email,score_percent\n"Rao, Asha",asha@example.com,85\nRavi Kumar,,52\n'
    )

    assert recipients == [
        {"name": "Rao, Asha", "email": "asha@example.com", "score_percent": 85.0},
        {"name": "Ravi Kumar", "score_percent": 52.0},
    ]


def test_parse_recipient_rows_without_assessment_scores():
    recipients = parse_recipient_csv(
        "name,email\nTaylor Morgan,taylor@example.com\nJordan Lee,\n"
    )

    assert recipients == [
        {"name": "Taylor Morgan", "email": "taylor@example.com", "score_percent": None},
        {"name": "Jordan Lee", "score_percent": None},
    ]


def test_reject_duplicate_emails_ignoring_case():
    with pytest.raises(ValueError, match="duplicates line 1"):
        parse_recipient_csv("Asha Rao,asha@example.com,85\nAsha Rao,ASHA@example.com,85")


def test_report_all_invalid_rows_before_generation():
    with pytest.raises(ValueError) as error:
        parse_recipient_csv("name,email,score_percent\n,broken,120\nRavi,ravi@example.com,NaN")

    assert "Line 2: recipient name is required" in str(error.value)
    assert "Line 2: email address is invalid" in str(error.value)
    assert "Line 2: score must be a number from 0 to 100" in str(error.value)
    assert "Line 3: score must be a number from 0 to 100" in str(error.value)


def test_reject_more_than_one_thousand_recipients():
    data = "".join(f"Candidate {index},candidate{index}@example.com,80\n" for index in range(1001))

    with pytest.raises(ValueError, match="at most 1000 recipients"):
        parse_recipient_csv(data)
