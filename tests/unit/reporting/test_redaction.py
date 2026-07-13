from codex_auto.reporting.redaction import Redactor


def test_redactor_removes_tokens_private_keys_urls_and_configured_values() -> None:
    redactor = Redactor(secret_values=("known-secret",), extra_patterns=(r"CUSTOM-[0-9]+",))
    credential_url = "https://user:" + "password@" + "example.com/path"
    api_assignment = "OPENAI_API_" + "KEY=" + "sk-" + "example123456789"
    private_key = "-----BEGIN " + "PRIVATE KEY-----\nmaterial\n-----END PRIVATE KEY-----"
    text = (
        "Authorization: Bearer abc.def.ghi\n"
        f"{credential_url}\n"
        f"{api_assignment}\n"
        f"{private_key}\n"
        "known-secret CUSTOM-123"
    )

    redacted = redactor.redact(text)

    for secret in (
        "abc.def.ghi",
        "password",
        "sk-example",
        "material",
        "known-secret",
        "CUSTOM-123",
    ):
        assert secret not in redacted
    assert redacted.count("<redacted>") >= 5
