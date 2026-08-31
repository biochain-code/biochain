"""
facade.py -- ЕДИНСТВЕННАЯ точка входа для остального кода biochain.py.
Весь код, которому нужна проверка подписи или вывод адреса, обязан
обращаться СЮДА, а не импортировать impl_python напрямую -- это и есть
"фундамент под будущий переход": когда Rust-сервис будет готов и
проверен, здесь меняется одна строка импорта, ничего в остальном коде
трогать не придётся.

Сейчас: get_verifier() возвращает LibOQSSignatureVerifier (Python,
действующая production-логика, перенесена дословно из PQCrypto).

Потом: get_verifier() будет возвращать RustSignatureVerifier -- прокси,
делающий сетевой вызов к отдельному Rust-сервису, реализующему тот же
контракт SignatureVerifier. Переключение делается ЗДЕСЬ и только здесь.
"""

from .impl_python import LibOQSSignatureVerifier
from .interfaces import SignatureVerifier

_verifier_singleton: SignatureVerifier = None


def get_verifier() -> SignatureVerifier:
    """Возвращает действующий SignatureVerifier. Синглтон -- один
    экземпляр на процесс, как и было с module-level `pq = PQCrypto()`
    в оригинале."""
    global _verifier_singleton
    if _verifier_singleton is None:
        _verifier_singleton = LibOQSSignatureVerifier()
    return _verifier_singleton


# ── Когда Rust-сервис будет готов и проверен, замена выглядит так: ──────
#
# from .impl_rust_proxy import RustSignatureVerifier
#
# def get_verifier() -> SignatureVerifier:
#     global _verifier_singleton
#     if _verifier_singleton is None:
#         _verifier_singleton = RustSignatureVerifier(endpoint="localhost:50051")
#     return _verifier_singleton
#
# impl_rust_proxy.py ЕЩЁ НЕ СУЩЕСТВУЕТ -- это заготовка на будущее, не
# сегодняшняя задача. Когда он появится, он обязан реализовать ТОТ ЖЕ
# контракт SignatureVerifier из interfaces.py -- ни один вызывающий
# код при этом не меняется, только эти несколько строк здесь.
