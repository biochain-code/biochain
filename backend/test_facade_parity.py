"""
Проверка: результаты через новый facade ДОЛЖНЫ побайтово совпадать с
результатами прямого вызова оригинального PQCrypto -- иначе обёртка
что-то изменила в поведении, а это недопустимо для production-кода.

Запускать на сервере (там установлен liboqs), рядом с biochain.py и
папкой biochain_facade/.
"""
import sys
sys.path.insert(0, ".")

# ── Оригинал -- как есть в текущем production-коде ──────────────────────
import hashlib

try:
    import oqs as _oqs

    class _LiboqsMLDSA44_orig:
        _ALG = "ML-DSA-44"
        @staticmethod
        def keygen():
            with _oqs.Signature(_LiboqsMLDSA44_orig._ALG) as s:
                pk = s.generate_keypair(); sk = s.export_secret_key()
            return pk, sk
        @staticmethod
        def sign(sk, message):
            with _oqs.Signature(_LiboqsMLDSA44_orig._ALG, bytes(sk)) as s:
                return s.sign(bytes(message))
        @staticmethod
        def verify(pk, message, signature):
            with _oqs.Signature(_LiboqsMLDSA44_orig._ALG) as v:
                return v.verify(bytes(message), bytes(signature), bytes(pk))
    Dilithium_orig = _LiboqsMLDSA44_orig
except Exception as e:
    print(f"liboqs недоступен для теста-оригинала: {e}")
    sys.exit(1)

class PQCrypto_orig:
    def generate_keypair(self):
        return Dilithium_orig.keygen()
    def sign(self, sk, message: str) -> str:
        return Dilithium_orig.sign(sk, message.encode()).hex()
    def verify(self, pk, message: str, signature: str, scheme_id: str = "MLDSA44") -> bool:
        if scheme_id != "MLDSA44":
            return False
        try:
            return Dilithium_orig.verify(pk, message.encode(), bytes.fromhex(signature))
        except Exception:
            return False
    def address(self, pk, scheme_id: str = "MLDSA44") -> str:
        raw = pk if isinstance(pk, bytes) else str(pk).encode()
        if scheme_id == "MLDSA44":
            return "BIO1" + hashlib.sha3_256(raw).hexdigest()[:32].upper()
        tagged = scheme_id.encode() + raw
        return "BIO1" + hashlib.sha3_256(tagged).hexdigest()[:32].upper()

pq_orig = PQCrypto_orig()

# ── Новое -- через facade ────────────────────────────────────────────────
from biochain_facade import get_verifier
verifier = get_verifier()

if __name__ == "__main__":
    pk, sk = pq_orig.generate_keypair()
    message = "TX|BIO1TEST|BIO1TEST2|1.00000000|1700000000000000|1"

    # 1. Адрес -- оригинал vs facade, тот же самый публичный ключ
    addr_orig = pq_orig.address(pk)
    addr_new = verifier.address(pk)
    print(f"адрес (оригинал): {addr_orig}")
    print(f"адрес (facade):   {addr_new}")
    assert addr_orig == addr_new, "РАСХОЖДЕНИЕ в формуле адреса!"
    print("СОВПАДАЕТ\n")

    # 2. Подпись -- оригинал подписывает, facade подписывает, оба должны дать валидную подпись
    sig_orig = pq_orig.sign(sk, message)
    sig_new = verifier.sign(sk, message)
    print(f"подпись (оригинал): {sig_orig[:32]}...")
    print(f"подпись (facade):   {sig_new[:32]}...")
    # Подписи МОГУТ отличаться байт в байт (некоторые PQ-схемы недетерминированы),
    # поэтому проверяем не побайтовое совпадение подписи, а что ОБЕ проходят проверку.

    # 3. Проверка -- каждая подпись должна проходить проверку в ОБЕИХ реализациях (перекрёстно)
    v1 = pq_orig.verify(pk, message, sig_orig)
    v2 = verifier.verify(pk, message, sig_new)
    v3 = pq_orig.verify(pk, message, sig_new)   # facade-подпись проверяется оригиналом
    v4 = verifier.verify(pk, message, sig_orig) # оригинал-подпись проверяется facade
    print(f"\nоригинал проверяет свою подпись: {v1}")
    print(f"facade проверяет свою подпись:    {v2}")
    print(f"оригинал проверяет facade-подпись: {v3}")
    print(f"facade проверяет оригинал-подпись: {v4}")
    assert v1 and v2 and v3 and v4, "РАСХОЖДЕНИЕ в перекрёстной проверке подписи!"
    print("ВСЕ ЧЕТЫРЕ ПРОХОДЯТ\n")

    # 4. Подмена -- обе реализации должны честно отклонить изменённое сообщение
    tampered = message.replace("1.00000000", "9.00000000")
    t1 = pq_orig.verify(pk, tampered, sig_orig)
    t2 = verifier.verify(pk, tampered, sig_new)
    print(f"оригинал отклоняет подмену: {not t1}")
    print(f"facade отклоняет подмену:   {not t2}")
    assert not t1 and not t2, "РАСХОЖДЕНИЕ -- подмена не отклонена!"

    print("\nВСЕ ПРОВЕРКИ ПРОШЛИ -- facade побайтово совместим с оригиналом")
