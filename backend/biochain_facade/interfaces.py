"""
interfaces.py -- абстрактные контракты для подсистем BioChain, которые
однажды могут быть заменены на Rust-реализацию без изменения кода,
который их вызывает.

Эти классы -- ЕДИНСТВЕННАЯ точка правды о том, что обязана уметь любая
реализация (сейчас -- Python, потом, возможно, -- прокси к Rust-сервису).
Сигнатуры здесь менять нельзя без синхронного обновления ВСЕХ реализаций.
"""

from abc import ABC, abstractmethod
from typing import Optional


class SignatureVerifier(ABC):
    """Контракт постквантовой подписи -- генерация ключей, подпись,
    проверка, вывод адреса. Сегодня реализуется через liboqs/dilithium_py
    напрямую в процессе; завтра может быть реализована через сетевой
    вызов к отдельному Rust-сервису, реализующему тот же контракт."""

    @abstractmethod
    def generate_keypair(self):
        """Возвращает (публичный_ключ, секретный_ключ) в исходном
        байтовом представлении бэкенда."""
        raise NotImplementedError

    @abstractmethod
    def sign(self, secret_key, message: str) -> str:
        """Подписывает сообщение секретным ключом, возвращает подпись
        в hex-представлении."""
        raise NotImplementedError

    @abstractmethod
    def verify(self, public_key, message: str, signature_hex: str, scheme_id: str = "MLDSA44") -> bool:
        """Проверяет подпись сообщения публичным ключом. Возвращает
        False (не бросает исключение) при любой ошибке -- вызывающий
        код не должен отличать "неверная подпись" от "сбой проверки"."""
        raise NotImplementedError

    @abstractmethod
    def address(self, public_key, scheme_id: str = "MLDSA44") -> str:
        """Выводит адрес BIO1... из публичного ключа. Для схемы,
        отличной от MLDSA44, публичный ключ помечается именем схемы
        перед хэшированием -- разные схемы дают разные адреса даже
        при совпадающих байтах ключа."""
        raise NotImplementedError


class StateBackend(ABC):
    """Контракт хранения состояния сети -- балансы, узлы, стейки и так
    далее. Сегодня реализуется через SQLite в том же процессе; завтра
    может быть реализована через сетевой вызов к Rust-сервису с тем же
    контрактом. НЕ реализуется в этом заходе -- заложен только контракт,
    чтобы более поздний перевод состояния тоже мог пойти через facade,
    не меняя вызывающий код повторно."""

    @abstractmethod
    def get_balance(self, address: str) -> int:
        """Баланс в сатоши (целое число, не BIO с плавающей точкой)."""
        raise NotImplementedError

    @abstractmethod
    def debit(self, address: str, amount_sat: int) -> bool:
        """Атомарно списывает amount_sat, если хватает средств.
        Возвращает True при успехе, False -- если средств не хватило."""
        raise NotImplementedError

    @abstractmethod
    def credit(self, address: str, amount_sat: int) -> None:
        """Начисляет amount_sat. Не может провалиться по бизнес-причине
        (в отличие от debit)."""
        raise NotImplementedError
