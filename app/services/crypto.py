from cryptography.fernet import Fernet, InvalidToken

from app.services.errors import ServiceError


class TokenCipher:
    def __init__(self, key: str) -> None:
        if not key:
            raise ServiceError("TOKEN_ENCRYPTION_KEY не настроен.")
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise ServiceError("TOKEN_ENCRYPTION_KEY имеет неверный формат.") from exc

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ServiceError("Не удалось расшифровать токен Threads; подключите аккаунт заново.") from exc
