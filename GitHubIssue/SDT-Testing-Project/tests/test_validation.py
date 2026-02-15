import pytest
from validation import validate_email

@pytest.fixture
def valid_emails():
    return ["user@example.com", "first.last+alias@domain.co"]

def test_validate_email_valid(valid_emails):
    for email in valid_emails:
        assert validate_email(email) is True

def test_validate_email_empty_string():
    assert validate_email("") is False

def test_validate_email_none():
    assert validate_email(None) is False
