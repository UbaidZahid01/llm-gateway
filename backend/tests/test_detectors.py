from src import detectors


def _types(text):
    return [m.type for m in detectors.detect(text, detectors.ALL_TYPES)]


def test_luhn_valid_card_detected():
    assert detectors.CREDIT_CARD in _types("pay with 4111 1111 1111 1111")


def test_non_luhn_16_digits_not_a_card():
    # A random 16-digit run that fails Luhn must not be reported as a card.
    types = _types("ref 1234 5678 9012 3456 end")
    assert detectors.CREDIT_CARD not in types


def test_email_and_ip():
    types = _types("mail a@b.com from 10.0.0.1")
    assert detectors.EMAIL in types
    assert detectors.IP in types


def test_overlap_priority_card_over_phone():
    # The digit run is a valid card; it must be reported once, as a card.
    matches = detectors.detect("4111 1111 1111 1111", detectors.ALL_TYPES)
    assert len(matches) == 1
    assert matches[0].type == detectors.CREDIT_CARD


def test_disabled_type_not_detected():
    assert detectors.detect("a@b.com", [detectors.PHONE]) == []


def test_secret_patterns():
    assert detectors.SECRET in _types("key sk-abcdef012345678901234567 here")
