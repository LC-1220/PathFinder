import pytest

from fastapi_app import _password_policy_error


@pytest.mark.parametrize(
    "password",
    [
        "short",
        "lowercase1!",
        "12345678!",
        "UppercaseOnly",
        "NoSymbol88",
        "Aa1!" + "é" * 40,
    ],
)
def test_password_policy_rejects_passwords_missing_requirements(password):
    assert _password_policy_error(password)


@pytest.mark.parametrize("password", ["Abcdefg!", "Password1!", "NoDigits!!"])
def test_password_policy_accepts_eight_character_uppercase_symbol_passwords(password):
    assert _password_policy_error(password) is None