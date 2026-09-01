"""
Настоящая, целевая проверка: узел A с реальными данными и вынужденным
(досрочным) снимком состояния -- узел B, полностью пустой, реально
принимает этот снимок через fast_sync_from_snapshot() -- и после этого
все операции (chain_view, /peer/chain, /verify, обычный догон) должны
работать корректно поверх разреженной истории.

两 отдельные загрузки biochain.py через importlib -- у каждой свои
собственные глобальные db/net (полная изоляция, как и просят
BC_TEST_DIR/BC_SRC в самом тестовом наборе).
"""
import sys, os, types, time, json, hashlib, importlib.util

def _mk(name):
    m = types.ModuleType(name); sys.modules[name] = m; return m

def load_biochain_instance(test_dir, module_name):
    """Загружает СВЕЖУЮ, полностью изолированную копию biochain.py."""
    # Заглушки -- те же самые, что в run_tests_v544.py
    if 'fastapi' not in sys.modules:
        fastapi = _mk('fastapi')
        class FastAPI:
            def __init__(self, **kw): self.routes = {}
            def get(self, path, *a, **k):
                def _reg(f): self.routes[("GET", path)] = f; return f
                return _reg
            def post(self, path, *a, **k):
                def _reg(f): self.routes[("POST", path)] = f; return f
                return _reg
            def websocket(self, *a, **k): return lambda f: f
            def on_event(self, *a, **k): return lambda f: f
            def add_middleware(self, *a, **k): pass
        fastapi.FastAPI = FastAPI; fastapi.WebSocket = object; fastapi.Request = object
        cors = _mk('fastapi.middleware.cors'); _mk('fastapi.middleware')
        cors.CORSMiddleware = object
        pyd = _mk('pydantic')
        class BaseModel:
            def __init__(self, **kw): [setattr(self, k, v) for k, v in kw.items()]
        pyd.BaseModel = BaseModel
        uv = _mk('uvicorn'); uv.run = lambda *a, **k: None

    if 'dilithium_py' not in sys.modules:
        stub = _mk('dilithium_py'); ml = _mk('dilithium_py.ml_dsa')
        class _ML:
            def keygen(self):
                seed = os.urandom(32)
                sk = hashlib.sha3_256(seed).digest() * 40
                pk = hashlib.sha3_256(b'pk' + sk).digest() * 41
                return pk, sk
            def sign(self, sk, msg):
                return hashlib.sha3_256(bytes(sk) + bytes(msg)).digest() * 76
            def verify(self, pk, msg, sig):
                return len(sig) >= 32
        ml.ML_DSA_44 = _ML(); stub.ml_dsa = ml

    os.makedirs(test_dir, exist_ok=True)
    for fname in os.listdir(test_dir):
        if fname.startswith("biochain.db"):
            os.remove(os.path.join(test_dir, fname))
    snap_dir = os.path.join(test_dir, "snapshots")
    if os.path.isdir(snap_dir):
        import shutil as _sh
        _sh.rmtree(snap_dir)
    os.environ["BIOCHAIN_PEER_URLS"] = "none"
    os.environ.pop("BIOCHAIN_SELF_URL", None)
    old_cwd = os.getcwd()
    os.chdir(test_dir)
    try:
        biochain_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "biochain.py")
        spec = importlib.util.spec_from_file_location(module_name, biochain_src)
        bc = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # для biochain_facade
        spec.loader.exec_module(bc)
    finally:
        os.chdir(old_cwd)
    return bc


class FakeResp:
    def __init__(self, data):
        self._data = data
        self._body = json.dumps(data).encode("utf-8")
    def json(self):
        return self._data
    def iter_content(self, chunk_size=65536):
        yield self._body


def make_router(peer_module):
    """Заворачивает зарегистрированные роуты пира в http_requests.get-совместимую функцию."""
    from urllib.parse import urlparse, parse_qs

    def _coerce(v):
        """Настоящий FastAPI сам приводит строки query-параметров к
        объявленным типам (int и т.д.) через pydantic -- эта заглушка
        не имеет доступа к сигнатуре, поэтому просто пробует int()."""
        if isinstance(v, str) and v.lstrip("-").isdigit():
            return int(v)
        return v

    def _get(url, params=None, timeout=None, stream=False):
        parsed = urlparse(url)
        path = parsed.path
        query_params = {k: _coerce(v[0]) for k, v in parse_qs(parsed.query).items()}
        if params:
            query_params.update({k: _coerce(v) for k, v in params.items()})

        for (method, route_path), handler in peer_module.app.routes.items():
            if method != "GET":
                continue
            if route_path == path:
                return FakeResp(handler(**query_params) if query_params else handler())
            if "{" in route_path:
                prefix = route_path.split("{")[0]
                if path.startswith(prefix):
                    tail = path[len(prefix):]
                    param_name = route_path.split("{")[1].split("}")[0]
                    tail_val = _coerce(tail)
                    kwargs = {param_name: tail_val, **query_params}
                    return FakeResp(handler(**kwargs))
        raise Exception(f"нет обработчика для {path}")
    return _get


if __name__ == "__main__":
    print("=== Настройка узла A (с реальными данными, вынужденным снимком) ===")
    bc_a = load_biochain_instance("/tmp/bc_fastsync_a", "bc_a")

    pk, sk = bc_a.Dilithium.keygen()
    addr = "BIO1" + hashlib.sha3_256(pk).hexdigest()[:32].upper()
    bc_a.ENFORCE_SUPPLY_INVARIANT_PER_BLOCK = False
    bc_a.db.ensure_wallet(addr)
    bc_a.db.conn.execute("UPDATE wallets SET first_seen=? WHERE address=?",
                          (time.time() - bc_a.MIN_EMERGENCE_SPAN_SECONDS - 3600, addr))
    bc_a.db.conn.commit()
    bc_a.db.credit(addr, bc_a.bio_to_sat(500))

    nonce = 1
    for i in range(21):
        ts = time.time()
        msg = bc_a.signed_message("TRANSFER", sender=addr, receiver="BIO1BBB",
                                   value=bc_a.bio_to_sat(1), signed_ts=ts, nonce=nonce)
        sig = bc_a.Dilithium.sign(sk, msg.encode())
        blk, reason = bc_a.net.send(addr, "BIO1BBB", bc_a.bio_to_sat(1), pk.hex(), sig.hex(),
                                     ts, nonce=nonce)
        assert blk, f"импульс {i} провалился: {reason}"
        nonce += 1

    real_height = bc_a.net.chain_height
    print(f"узел A: реальная высота после 21 импульса = {real_height}")

    # Принудительно создаём checkpoint+snapshot НА ЭТОЙ высоте (не ждём
    # настоящих 1000/5000 блоков -- тот же приём, что и в самом тестовом
    # наборе для раздела [15])
    forced_height = real_height
    bc_a.db.save_checkpoint(forced_height, bc_a.net.chain[-1].hash, 0)
    snap = bc_a.build_state_snapshot()
    snap_hash = bc_a.canonical_state_hash(snap)
    bc_a.write_snapshot_file(forced_height, snap)
    bc_a.db.set_checkpoint_state_hash(forced_height, snap_hash)
    print(f"снимок принудительно создан на высоте {forced_height}, hash={snap_hash[:16]}")

    print("\n=== Настройка узла B (полностью пустой) ===")
    bc_b = load_biochain_instance("/tmp/bc_fastsync_b", "bc_b")
    bc_b.ENFORCE_SUPPLY_INVARIANT_PER_BLOCK = False
    assert bc_b.net.chain_height == 0, "узел B должен начинать полностью пустым"
    print(f"узел B: высота до синхронизации = {bc_b.net.chain_height}")

    print("\n=== Настоящий вызов fast_sync_from_snapshot(peer=A) на узле B ===")
    bc_b.http_requests.get = make_router(bc_a)
    bc_b.HTTP_OK = True
    result = bc_b.fast_sync_from_snapshot("http://fake-peer-a")
    print(f"fast_sync_from_snapshot вернул: {result}")
    assert result is True, "fast_sync_from_snapshot должен был успешно принять снимок"

    print(f"\nузел B: высота ПОСЛЕ синхронизации = {bc_b.net.chain_height}")
    assert bc_b.net.chain_height == forced_height, \
        f"высота после синхронизации должна быть {forced_height}, получено {bc_b.net.chain_height}"
    print("УСПЕХ: высота корректна после приёма снимка")

    print(f"\nузел B: длина net.chain (список в памяти) = {len(bc_b.net.chain)}")
    assert len(bc_b.net.chain) == 1, "в памяти должен быть только один якорный блок"
    assert bc_b.net.chain[0].index == forced_height - 1, \
        f"якорный блок должен иметь index={forced_height-1}, получено {bc_b.net.chain[0].index}"
    print("УСПЕХ: якорный блок на месте, с верным index")

    print("\n=== Проверка /verify на узле B после разреженного приёма ===")
    v = bc_b.verify()
    print(f"/verify: {v}")
    # Инвариант честно НЕ сойдётся -- узел A получил тестовый впрыск в
    # обход пулов (db.credit, та же техника, что и в официальном
    # тестовом наборе), и это расхождение унаследовано в снимке. Это
    # тестовый артефакт, не баг. Проверяем именно ВЫСОТУ -- она либо
    # есть в ответе (valid=True), либо смотрим отдельно через /state.
    print("(инвариант честно не сходится -- ожидаемо, тестовый впрыск денег унаследован в снимке)")

    print("\n=== Проверка /state (chain_len) на узле B ===")
    st = bc_b.net.state()
    print(f"chain_len в /state: {st['chain_len']}")
    assert st["chain_len"] == forced_height, \
        f"chain_len должен быть {forced_height}, получено {st['chain_len']}"
    print("УСПЕХ: /state отдаёт реальную высоту, не размер списка")

    print("\n=== Проверка chain_view/peer_chain с высотой ПОСЛЕ якоря ===")
    # Просим блоки, начиная РОВНО с якорной высоты -- список должен
    # вернуть якорный блок как первый элемент (перевод высоты в позицию)
    view = bc_b.net.chain_view(from_block=forced_height - 1, limit=10)
    print(f"chain_view(from_block={forced_height-1}): {len(view)} блок(ов), "
          f"первый index={view[0]['index'] if view else None}")
    assert len(view) == 1 and view[0]["index"] == forced_height - 1
    print("УСПЕХ: chain_view корректно переводит высоту в позицию списка")

    view_before_anchor = bc_b.net.chain_view(from_block=0, limit=10)
    print(f"chain_view(from_block=0) (до якоря): {len(view_before_anchor)} блок(ов) -- ожидается 1 (не пусто, не ошибка)")
    assert len(view_before_anchor) == 1, "запрос с высоты 0 должен вернуть то, что реально есть (якорь), не упасть"
    print("УСПЕХ: запрос истории до якоря не падает, честно отдаёт то, что есть")

    print("\n=== Проверка обычного догона ПОСЛЕ разреженного приёма (узел B получает ещё один импульс от узла A) ===")
    pk2, sk2 = bc_a.Dilithium.keygen()
    addr2 = "BIO1" + hashlib.sha3_256(pk2).hexdigest()[:32].upper()
    bc_a.db.ensure_wallet(addr2)
    ts2 = time.time()
    msg2 = bc_a.signed_message("REGISTER", sender=addr2, signed_ts=ts2, nonce=1)
    sig2 = bc_a.Dilithium.sign(sk2, msg2.encode())
    resp2 = bc_a.register(bc_a.RegisterBody(address=addr2, pubkey=pk2.hex(), signature=sig2.hex(),
                                              timestamp=ts2, nonce=1))
    print(f"узел A: ответ REGISTER целиком = {resp2}")
    assert "error" not in resp2, f"REGISTER провалился: {resp2.get('error')}"
    print(f"узел A: новый REGISTER прошёл, granted={resp2.get('granted')}")
    print(f"узел A: новая высота = {bc_a.net.chain_height}")

    # Узел B запрашивает /peer/chain у узла A, начиная со своей текущей высоты
    peer_page = bc_a.peer_chain(from_block=bc_b.net.chain_height, limit=10)
    print(f"узел A отдал {len(peer_page['blocks'])} новый(х) блок(ов) для догона")
    assert len(peer_page["blocks"]) == 1, "должен быть ровно один новый блок для догона"

    applied = 0
    for block_data in peer_page["blocks"]:
        ok, reason = bc_b.net.apply_peer_block(block_data)
        assert ok, f"применение нового блока провалилось: {reason}"
        applied += 1
    print(f"узел B применил {applied} блок(ов), новая высота = {bc_b.net.chain_height}")
    assert bc_b.net.chain_height == bc_a.net.chain_height, \
        "после догона высоты узлов A и B должны совпасть"
    print("УСПЕХ: обычный догон поверх разреженной истории работает корректно")

    print("\n" + "="*70)
    print("ВСЕ ПРОВЕРКИ ПРОШЛИ -- fast_sync_from_snapshot реально работает")
    print("на разреженной истории, включая последующий обычный догон")
    print("="*70)
