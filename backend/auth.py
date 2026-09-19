"""
backend/auth.py — User authentication: register, login, JWT, password reset.
"""
import logging
import os
import bcrypt
import jwt
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET 环境变量未设置，拒绝启动。请在 .env 中配置 JWT_SECRET。")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 30

# 统一用 \A...\Z 全串锚定：`$` 会匹配「末尾换行之前的位置」，
# 会让 "user\n" / "abcdefgh\n" 这类带尾随换行的输入混过校验。
# Password: 8-16 chars, ASCII printable except space/control
PASSWORD_PATTERN = re.compile(r'\A[\x21-\x7E]{8,16}\Z')
# Account: 1-16 chars, alphanumeric + underscore
ACCOUNT_PATTERN = re.compile(r'\A[a-zA-Z0-9_]{1,16}\Z')
# Username: 1-16 chars (any unicode)，显式排除控制字符与行/段分隔符；
# 不能用 re.DOTALL（会让 "." 匹配换行，使 "a\nb" 通过）
USERNAME_PATTERN = re.compile(r'\A[^\x00-\x1f\x7f-\x9f\u2028\u2029]{1,16}\Z')

# bcrypt 哈希固定形态: $2a$/$2b$/$2y$ + 2 位 cost + 22 位 salt + 31 位 digest
BCRYPT_HASH_PATTERN = re.compile(r'\A\$2[aby]?\$\d{2}\$[./A-Za-z0-9]{53}\Z')


def validate_account(account: str) -> Optional[str]:
    if not ACCOUNT_PATTERN.match(account):
        return "账号只能包含英文、数字和下划线，长度1-16"
    return None


def validate_username(username: str) -> Optional[str]:
    if not USERNAME_PATTERN.match(username.strip()):
        return "用户名长度1-16个字符"
    return None


def validate_password(password: str) -> Optional[str]:
    if not PASSWORD_PATTERN.match(password):
        return "密码长度8-16个字符，支持大小写英文、数字和常见符号"
    return None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码；库中 password_hash 是脏数据时按「鉴权失败」返回 False，绝不抛异常。

    bcrypt 对空串/非 bcrypt 串会抛 ValueError(Invalid salt)，对被截断的
    ``$2b$12$xxxxx`` 会抛 pyo3 PanicException（不是 Exception 子类），
    直接冒泡会让 /auth/login 返回 500 并暴露堆栈，且该用户永远无法登录。
    先用形态校验挡掉脏数据，再兜住 bcrypt 自身可能抛出的异常。
    """
    if not isinstance(password_hash, str) or not BCRYPT_HASH_PATTERN.match(password_hash):
        logger.warning("password_hash 为空或格式非法，按鉴权失败处理")
        return False
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except (ValueError, TypeError) as e:
        logger.warning("password_hash 校验失败，按鉴权失败处理: %s", e)
        return False


def create_jwt(user_id: int, account: str, username: str, password_changed_at: str) -> str:
    payload = {
        'user_id': user_id,
        'account': account,
        'username': username,
        'pw_changed_at': password_changed_at,
        'exp': datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS),
        'iat': datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def extract_jwt_claims_unverified(token: str) -> Optional[dict]:
    """仅解出 JWT 的 claims，不校验签名/有效期（读取 user_id 用于查库）。

    只用于「拿到 user_id 去数据库取当前 password_changed_at」这一步；
    鉴权结论必须由 :func:`decode_jwt`（带 ``current_pw_changed_at``）给出，
    绝不能直接采信本函数的返回值。
    """
    try:
        return jwt.decode(
            token, options={"verify_signature": False, "verify_exp": False}
        )
    except jwt.InvalidTokenError:
        return None
    except Exception:
        return None


def decode_jwt(token: str, current_pw_changed_at: str = None) -> Optional[dict]:
    """校验并解码 JWT。

    除签名与 ``exp`` 外，强制比对 token 内的 ``pw_changed_at`` 与调用方查到的
    用户当前密码变更时间：不一致（用户已改密）即视为无效 token，返回 None。

    Args:
        token: JWT 字符串
        current_pw_changed_at: 数据库中该用户当前的 password_changed_at。
            为 None 时无法完成比对，同样拒绝（fail closed）——调用方必须先查库。

    注意：所有调用方都必须传入 ``current_pw_changed_at``，否则 token 一律无效。
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

    token_pw = payload.get('pw_changed_at')
    if not current_pw_changed_at or token_pw != current_pw_changed_at:
        return None
    return payload
