"""
Tests for backend.auth: validation, password hashing, JWT encode/decode.
Usage: cd test && python -m pytest test_auth.py -v
"""
import os
import sys
import pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Must set JWT_SECRET before importing auth module
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-unit-tests")

import backend.auth as auth


# ============================================================
# Account validation
# ============================================================

class TestValidateAccount:
    def test_valid_english(self):
        assert auth.validate_account("hello") is None
        assert auth.validate_account("Test123") is None
        assert auth.validate_account("a") is None

    def test_valid_with_underscore(self):
        assert auth.validate_account("user_name") is None
        assert auth.validate_account("test_123") is None

    def test_valid_boundary_length(self):
        assert auth.validate_account("a") is None  # 1 char
        assert auth.validate_account("a" * 16) is None  # 16 chars

    def test_too_long(self):
        assert auth.validate_account("a" * 17) is not None

    def test_empty(self):
        assert auth.validate_account("") is not None

    def test_chinese_characters(self):
        assert auth.validate_account("中文名") is not None

    def test_special_chars(self):
        assert auth.validate_account("hello world") is not None
        assert auth.validate_account("hello@world") is not None
        assert auth.validate_account("hello-world") is not None


# ============================================================
# Username validation
# ============================================================

class TestValidateUsername:
    def test_valid_ascii(self):
        assert auth.validate_username("Alice") is None
        assert auth.validate_username("Test_User") is None

    def test_valid_chinese(self):
        assert auth.validate_username("德克萨斯") is None
        assert auth.validate_username("用户1") is None

    def test_valid_boundary(self):
        assert auth.validate_username("a") is None  # 1 char
        assert auth.validate_username("a" * 16) is None  # 16 chars

    def test_too_long(self):
        assert auth.validate_username("a" * 17) is not None

    def test_empty(self):
        assert auth.validate_username("") is not None
        assert auth.validate_username("   ") is not None  # stripped to empty


# ============================================================
# Password validation
# ============================================================

class TestValidatePassword:
    def test_valid_simple(self):
        assert auth.validate_password("abcdefgh") is None
        assert auth.validate_password("12345678") is None

    def test_valid_complex(self):
        assert auth.validate_password("Abc123!@") is None
        assert auth.validate_password("P@ssw0rd!") is None

    def test_valid_boundary(self):
        assert auth.validate_password("a" * 8) is None  # 8 chars min
        assert auth.validate_password("a" * 16) is None  # 16 chars max

    def test_too_short(self):
        assert auth.validate_password("a" * 7) is not None

    def test_too_long(self):
        assert auth.validate_password("a" * 17) is not None

    def test_empty(self):
        assert auth.validate_password("") is not None

    def test_spaces(self):
        assert auth.validate_password("a b c d e f g h") is not None


# ============================================================
# Password hashing
# ============================================================

class TestPasswordHashing:
    def test_hash_returns_string(self):
        result = auth.hash_password("testpass")
        assert isinstance(result, str)
        assert result.startswith("$2b$")

    def test_verify_correct_password(self):
        pw = "MyP@ssw0rd!"
        hashed = auth.hash_password(pw)
        assert auth.verify_password(pw, hashed) is True

    def test_verify_wrong_password(self):
        hashed = auth.hash_password("correct")
        assert auth.verify_password("wrong", hashed) is False

    def test_hash_is_salted(self):
        """Same password twice should produce different hashes."""
        h1 = auth.hash_password("samepass")
        h2 = auth.hash_password("samepass")
        assert h1 != h2
        assert auth.verify_password("samepass", h1)
        assert auth.verify_password("samepass", h2)


class TestVerifyPasswordDirtyHash:
    """防回归：库里 password_hash 是脏数据时，登录必须「鉴权失败」而不是 500。

    修复前 verify_password 直接把脏 hash 交给 bcrypt：空串/非 bcrypt 串抛
    ValueError，而被截断的 ``$2b$12$tooshort`` 在 bcrypt 4.x 下抛的是
    ``pyo3_runtime.PanicException`` —— 它继承 BaseException 而非 Exception，
    ``except Exception`` 兜不住，会一路冒到 /auth/login 变成 500 并暴露堆栈。
    这里用 ``except BaseException`` 显式抓住这种漏网异常，确保「不抛、返回 False」。
    """

    @pytest.mark.parametrize("dirty_hash", [
        "",                        # 空串：bcrypt 抛 ValueError(Invalid salt)
        "$2b$12$tooshort",         # 截断 hash：bcrypt 抛 PanicException(非 Exception 子类)
        "not-a-bcrypt-hash",       # 完全不是 hash
        "$2b$12$" + "a" * 53,      # 形态像但 salt/digest 非 base64 合法字符
        None,                      # 非字符串（旧数据/迁移异常）
    ], ids=["empty", "truncated", "garbage", "bad-b64", "none"])
    def test_dirty_hash_returns_false_and_never_raises(self, dirty_hash):
        try:
            result = auth.verify_password("whatever", dirty_hash)
        except BaseException as exc:  # 连 PanicException 这类非 Exception 也要算失败
            pytest.fail(f"verify_password 对脏 hash 抛出了 {type(exc).__name__}: {exc}")
        assert result is False

    def test_valid_hash_still_verifies(self):
        """回归保护：脏数据预校验不能写得太严，把真实 bcrypt hash 一起挡在门外。"""
        hashed = auth.hash_password("MyP@ssw0rd!")
        assert auth.BCRYPT_HASH_PATTERN.match(hashed) is not None
        assert auth.verify_password("MyP@ssw0rd!", hashed) is True
        assert auth.verify_password("MyP@ssw0rd", hashed) is False


class TestValidationHardening:
    """防回归：校验正则必须全串锚定（``\\A...\\Z``），不能用 ``$`` / ``re.DOTALL``。

    ``$`` 还会匹配「末尾换行之前的位置」，``re.DOTALL`` 会让 ``.`` 匹配换行，
    两者都会放行 "user\\n"、"a\\nb" 这类带换行/控制字符的输入注册成功。
    同时确保收紧后合法值（含中文用户名、边界长度）依然通过。
    """

    def test_username_rejects_embedded_newline_and_control_chars(self):
        assert auth.validate_username("a\nb") is not None
        assert auth.validate_username("a\rb") is not None
        assert auth.validate_username("a\x00b") is not None
        assert auth.validate_username("a\x00") is not None
        assert auth.validate_username("a\u2028b") is not None

    def test_account_rejects_trailing_newline(self):
        assert auth.validate_account("user\n") is not None
        assert auth.validate_account("user\r") is not None
        assert auth.validate_account("user\x00") is not None
        assert auth.validate_account("us\ner") is not None

    def test_password_rejects_trailing_newline(self):
        assert auth.validate_password("Abc12345\n") is not None
        assert auth.validate_password("Abc12345\r\n") is not None
        assert auth.validate_password("Abc\n12345") is not None

    def test_legal_values_still_accepted(self):
        """回归保护：收紧校验后正常输入不能被误伤。"""
        assert auth.validate_username("德克萨斯") is None
        assert auth.validate_username("Alice") is None
        assert auth.validate_username("a" * 16) is None
        assert auth.validate_account("user_123") is None
        assert auth.validate_account("a" * 16) is None
        assert auth.validate_password("Abc12345") is None
        assert auth.validate_password("P@ssw0rd!") is None
        assert auth.validate_password("a" * 8) is None
        assert auth.validate_password("a" * 16) is None


# ============================================================
# JWT
# ============================================================

class TestJWT:
    def test_create_and_decode(self):
        token = auth.create_jwt(1, "testuser", "TestUser", "2024-01-01T00:00:00")
        # decode_jwt 必须拿到数据库里该用户当前的 password_changed_at 才能完成校验
        payload = auth.decode_jwt(token, "2024-01-01T00:00:00")
        assert payload is not None
        assert payload["user_id"] == 1
        assert payload["account"] == "testuser"
        assert payload["username"] == "TestUser"
        assert payload["pw_changed_at"] == "2024-01-01T00:00:00"

    def test_decode_fails_closed_without_matching_password_changed_at(self):
        """不传/不匹配 DB 当前密码变更时间一律无效（改密后旧 token 失效）。"""
        token = auth.create_jwt(1, "testuser", "TestUser", "2024-01-01T00:00:00")
        assert auth.decode_jwt(token) is None  # 调用方没查库 -> fail closed
        assert auth.decode_jwt(token, "") is None
        assert auth.decode_jwt(token, "2024-06-01T00:00:00") is None  # 已改密
        assert auth.decode_jwt(token, "2024-01-01T00:00:00") is not None  # 未改密

    def test_decode_invalid_token(self):
        assert auth.decode_jwt("not.a.valid.token") is None
        assert auth.decode_jwt("") is None
        assert auth.decode_jwt("abc.def.ghi") is None

    def test_decode_garbage(self):
        assert auth.decode_jwt("garbage") is None

    def test_token_has_expiry(self):
        token = auth.create_jwt(1, "u", "n", "2024-01-01T00:00:00")
        payload = auth.decode_jwt(token, "2024-01-01T00:00:00")
        assert "exp" in payload
        assert "iat" in payload
