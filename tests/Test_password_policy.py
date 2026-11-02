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


@pytest.mark.parametrize("password", [
    "Aa1!xxx", "lowercase1!", "UPPERCASE1!", "Password!", "Password123",
    "Aa1!" + "é" * 35,
])
def test_admin_passwords_require_all_five_rules(password):
    assert _password_policy_error(password, admin=True)


@pytest.mark.parametrize("password", [
    "Abcdef1!", "Password1!", "Aa1!" + "x" * 68, "Aa1!" + "é" * 34,
])
def test_admin_passwords_accept_all_requirements_within_byte_limit(password):
    assert _password_policy_error(password, admin=True) is None