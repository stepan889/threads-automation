import base64
import hashlib
import hmac
import time

from app.services.errors import ServiceError


class OAuthStateCodec:
    def __init__(self, secret: str, ttl_seconds: int = 600) -> None:
        if not secret:
            raise ServiceError("THREADS_CLIENT_SECRET не настроен.")
        self.secret, self.ttl = secret.encode(), ttl_seconds

    def encode(self, telegram_id: int) -> str:
        payload = f"{telegram_id}:{int(time.time())}".encode()
        signature = hmac.new(self.secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + b":" + signature).decode().rstrip("=")

    def decode(self, state: str) -> int:
        try:
            raw = base64.urlsafe_b64decode(state + "=" * (-len(state) % 4))
            telegram_raw, issued_raw, signature = raw.split(b":", 2)
            payload = telegram_raw + b":" + issued_raw
            telegram_id, issued = telegram_raw.decode(), issued_raw.decode()
            valid = hmac.compare_digest(signature, hmac.new(self.secret, payload, hashlib.sha256).digest())
            if not valid or time.time() - int(issued) > self.ttl or int(issued) > time.time() + 30:
                raise ValueError
            return int(telegram_id)
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise ServiceError("OAuth state недействителен или истёк. Запустите подключение заново.") from exc
