"""
impl_python.py -- сегодняшняя, действующая реализация контрактов из
interfaces.py. Логика внутри LibOQSSignatureVerifier перенесена
ДОСЛОВНО из существующего класса PQCrypto (biochain.py) -- это
обёртка, не переписывание. Поведение обязано быть побитово идентично
тому, что уже работает в production.
"""

import hashlib
from .interfaces import SignatureVerifier

try:
    import oqs as _oqs

    class _LiboqsMLDSA44:
        """Тот же интерфейс keygen/sign/verify, что и у dilithium_py's
        ML_DSA_44, но через liboqs. Объект подписи создаётся на каждый
        вызов заново -- это дешёвые C-аллокации, и создание на каждый
        вызов снимает вопрос совместного использования одного C-объекта
        между потоками uvicorn."""
        _ALG = "ML-DSA-44"

        @staticmethod
        def keygen():
            with _oqs.Signature(_LiboqsMLDSA44._ALG) as s:
                pk = s.generate_keypair()
                sk = s.export_secret_key()
            return pk, sk

        @staticmethod
        def sign(sk, message: bytes) -> bytes:
            with _oqs.Signature(_LiboqsMLDSA44._ALG, bytes(sk)) as s:
                return s.sign(bytes(message))

        @staticmethod
        def verify(pk, message: bytes, signature: bytes) -> bool:
            with _oqs.Signature(_LiboqsMLDSA44._ALG) as v:
                return v.verify(bytes(message), bytes(signature), bytes(pk))

    _pk_t, _sk_t = _LiboqsMLDSA44.keygen()
    _sig_t = _LiboqsMLDSA44.sign(_sk_t, b"backend-selftest")
    if not _LiboqsMLDSA44.verify(_pk_t, b"backend-selftest", _sig_t):
        raise RuntimeError("liboqs self-test: valid signature rejected")
    if _LiboqsMLDSA44.verify(_pk_t, b"tampered", _sig_t):
        raise RuntimeError("liboqs self-test: tampered message accepted")
    del _pk_t, _sk_t, _sig_t

    _Dilithium = _LiboqsMLDSA44
    _PQ_BACKEND = "liboqs"
    print(f"[PQ] ML-DSA-44 via liboqs C backend (liboqs {_oqs.oqs_version()}, "
          f"python bindings {_oqs.oqs_python_version()}) -- self-test passed")
except Exception as _liboqs_err:
    try:
        from dilithium_py.ml_dsa import ML_DSA_44 as _Dilithium
        _PQ_BACKEND = "dilithium_py"
        print("=" * 70)
        print("[PQ][WARNING] liboqs is UNAVAILABLE on this machine:")
        print(f"[PQ][WARNING]   {_liboqs_err}")
        print("[PQ][WARNING] Falling back to dilithium_py (pure python).")
        print("[PQ][WARNING] Signatures remain fully correct and compatible,")
        print("[PQ][WARNING] but verification is roughly 267x SLOWER.")
        print("[PQ][WARNING] For production, install liboqs + liboqs-python.")
        print("=" * 70)
    except ImportError:
        print("[FATAL] No post-quantum backend found (neither liboqs nor dilithium_py).")
        print("        Install one of them, e.g.: pip install dilithium-py")
        print("        There is no insecure fallback -- post-quantum signatures")
        print("        protect real user funds and cannot be silently skipped.")
        raise SystemExit(1)


class LibOQSSignatureVerifier(SignatureVerifier):
    """Действующая, сегодняшняя реализация -- дословный перенос класса
    PQCrypto. Ничего не меняется в поведении, только оборачивается в
    контракт SignatureVerifier, чтобы вызывающий код обращался через
    facade, а не напрямую к этому классу."""

    def generate_keypair(self):
        return _Dilithium.keygen()

    def sign(self, secret_key, message: str) -> str:
        return _Dilithium.sign(secret_key, message.encode()).hex()

    def verify(self, public_key, message: str, signature_hex: str, scheme_id: str = "MLDSA44") -> bool:
        if scheme_id != "MLDSA44":
            print(f"[PQ] verify error: unknown scheme_id '{scheme_id}'")
            return False
        try:
            return _Dilithium.verify(public_key, message.encode(), bytes.fromhex(signature_hex))
        except Exception as e:
            print(f"[PQ] verify error: {e}")
            return False

    def address(self, public_key, scheme_id: str = "MLDSA44") -> str:
        raw = public_key if isinstance(public_key, bytes) else str(public_key).encode()
        if scheme_id == "MLDSA44":
            return "BIO1" + hashlib.sha3_256(raw).hexdigest()[:32].upper()
        tagged = scheme_id.encode() + raw
        return "BIO1" + hashlib.sha3_256(tagged).hexdigest()[:32].upper()


def backend_name() -> str:
    """Для диагностики -- какой бэкенд реально используется (liboqs
    или чистый Python fallback). Не часть контракта SignatureVerifier,
    просто полезно знать при отладке."""
    return _PQ_BACKEND
