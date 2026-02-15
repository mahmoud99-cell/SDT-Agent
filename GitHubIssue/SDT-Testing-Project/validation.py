def validate_email(email: str) -> bool:
    """Basic email validation (for testing purposes)"""
    if not email:
        return False
    return "@" in email and "." in email.split("@")[-1]
