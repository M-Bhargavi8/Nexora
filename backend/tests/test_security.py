"""Tests for password hashing and session-token generation."""

from __future__ import annotations

from app.core.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    password_needs_rehash,
    verify_password,
)

PASSWORD = "correct-horse-battery-staple"


class TestPasswordHashing:
    def test_hash_does_not_contain_the_password(self) -> None:
        """The single most important property: a database dump must not
        reveal what anyone typed."""
        digest = hash_password(PASSWORD)

        assert PASSWORD not in digest

    def test_uses_argon2id(self) -> None:
        assert hash_password(PASSWORD).startswith("$argon2id$")

    def test_same_password_hashes_differently_each_time(self) -> None:
        """Each hash embeds a random salt.

        Without salting, identical passwords produce identical hashes -- so
        an attacker instantly learns which users share a password, and one
        precomputed rainbow table cracks all of them at once.
        """
        assert hash_password(PASSWORD) != hash_password(PASSWORD)

    def test_verifies_the_correct_password(self) -> None:
        assert verify_password(PASSWORD, hash_password(PASSWORD)) is True

    def test_rejects_an_incorrect_password(self) -> None:
        assert verify_password("wrong-password-entirely", hash_password(PASSWORD)) is False

    def test_rejects_a_near_miss(self) -> None:
        assert verify_password(PASSWORD + "x", hash_password(PASSWORD)) is False

    def test_is_case_sensitive(self) -> None:
        assert verify_password(PASSWORD.upper(), hash_password(PASSWORD)) is False

    def test_returns_false_for_a_malformed_hash_instead_of_raising(self) -> None:
        """A corrupt row must fail closed -- denying access -- rather than
        raising an unhandled error or, far worse, granting access."""
        assert verify_password(PASSWORD, "not-a-valid-argon2-hash") is False

    def test_handles_unicode_passwords(self) -> None:
        unicode_password = "пароль-密码-🔐-secure"

        assert verify_password(unicode_password, hash_password(unicode_password)) is True

    def test_current_parameters_do_not_need_rehashing(self) -> None:
        assert password_needs_rehash(hash_password(PASSWORD)) is False


class TestSessionTokens:
    def test_tokens_are_unique(self) -> None:
        tokens = {generate_session_token() for _ in range(500)}

        assert len(tokens) == 500

    def test_token_carries_enough_entropy(self) -> None:
        """32 random bytes, URL-safe base64 encoded, is at least 43 chars."""
        assert len(generate_session_token()) >= 43

    def test_token_is_url_safe(self) -> None:
        """It travels in a cookie, so it must contain no characters that
        would need escaping."""
        token = generate_session_token()

        assert all(c.isalnum() or c in "-_" for c in token)

    def test_hashing_is_deterministic(self) -> None:
        """Unlike a password hash: we must be able to look a session up by
        hashing the incoming token and matching it against a stored value."""
        token = generate_session_token()

        assert hash_session_token(token) == hash_session_token(token)

    def test_hash_does_not_contain_the_token(self) -> None:
        token = generate_session_token()

        assert token not in hash_session_token(token)

    def test_different_tokens_hash_differently(self) -> None:
        assert hash_session_token(generate_session_token()) != hash_session_token(
            generate_session_token()
        )

    def test_hash_is_a_sha256_hex_digest(self) -> None:
        digest = hash_session_token(generate_session_token())

        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)
