from normalize_email import normalize_email

def test_normalize_email_trims_and_lowercases():
    assert normalize_email("  Alice@Example.COM ") == "alice@example.com"
