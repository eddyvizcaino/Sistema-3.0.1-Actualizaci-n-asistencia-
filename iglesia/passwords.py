"""Hash de contraseñas V2.9: Argon2id con compatibilidad/migración de hashes anteriores."""
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from werkzeug.security import check_password_hash as werkzeug_check

_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)

def hash_password(password):
    return _ph.hash(password)

def verify_password(stored, password):
    if not stored:
        return False
    if stored.startswith('$argon2'):
        try:
            return _ph.verify(stored, password)
        except (VerifyMismatchError, InvalidHashError):
            return False
    try:
        return werkzeug_check(stored, password)
    except (ValueError, TypeError):
        return False

def needs_rehash(stored):
    if not stored or not stored.startswith('$argon2'):
        return True
    try:
        return _ph.check_needs_rehash(stored)
    except InvalidHashError:
        return True
