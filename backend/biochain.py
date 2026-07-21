"""BioChain — AAECN"""

import time
import hashlib
import random
import json
import threading
import sqlite3
import os
import secrets
import heapq
import math
from contextlib import contextmanager
import copy

try:
    import requests as http_requests
    HTTP_OK = True
except ImportError:
    HTTP_OK = False
    print("[INFO] requests not installed -- peer sync disabled (pip install requests to enable)")

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

EMERGE_THRESHOLD    = 21

MIN_EMERGENCE_SPAN_SECONDS = 7 * 86400
ENERGY_PER_IMPULSE  = 8.0
ENERGY_DECAY_RATE   = 0.02
ENERGY_DEATH        = 5.0
RECENT_ACTIVITY_DECAY = 0.95
CHAIN_HOT_WINDOW = 50_000
REBIRTH_THRESHOLD   = EMERGE_THRESHOLD

TEAM_ADDRESS    = "BIO139339DE8FA694295"
SAT_PER_BIO = 100_000_000

def bio_to_sat(amount) -> int:
    """Boundary IN: parse a BIO amount (float/str/int) into int sats."""
    s = f"{float(amount):.8f}"
    neg = s.startswith("-")
    if neg: s = s[1:]
    whole, frac = s.split(".")
    sat = int(whole) * SAT_PER_BIO + int(frac)
    return -sat if neg else sat

def sat_to_bio(sat: int) -> float:
    """Boundary OUT: JSON/display only."""
    return sat / SAT_PER_BIO

def sat_to_str8(sat: int) -> str:
    """Canonical 8-decimal string for SIGNATURES, built from pure int sats."""
    sat = int(sat)
    sign = "-" if sat < 0 else ""
    sat = abs(sat)
    return f"{sign}{sat // SAT_PER_BIO}.{sat % SAT_PER_BIO:08d}"

def transfer_fee(value_sat: int) -> int:
    """THE canonical transfer fee, in sats -- flat base + PPM share, pure integer, floor rounding."""
    return Emission.TRANSFER_FEE_BASE + (value_sat * Emission.BURN_RATE_PPM) // 1_000_000

VALIDATORS_POOL_GENESIS = 8_400_000 * SAT_PER_BIO
VALIDATORS_TAPER_PERCENT = 10
VALIDATORS_TAPER_FLOOR = VALIDATORS_POOL_GENESIS * VALIDATORS_TAPER_PERCENT // 100

TEAM_POOL_TOTAL = 1_050_000 * SAT_PER_BIO
VESTING_MONTHS  = 114
CLIFF_SECONDS   = 6  * 30 * 24 * 3600
MONTH_SECONDS   = 30 * 24 * 3600
MONTHLY_PAYOUT       = TEAM_POOL_TOTAL // VESTING_MONTHS
FINAL_MONTH_PAYOUT   = TEAM_POOL_TOTAL - MONTHLY_PAYOUT * (VESTING_MONTHS - 1)

STAKE_TIERS = {
    "NONE":             {"min_bio": 0,                          "reward_mult": 1.0, "weight_mult": 1.0, "label": "No stake"},
    "VALIDATOR":        {"min_bio": 1_000  * 100_000_000,      "reward_mult": 1.0, "weight_mult": 1.0, "label": "Validator"},
    "SENIOR_VALIDATOR": {"min_bio": 5_000  * 100_000_000,      "reward_mult": 1.5, "weight_mult": 1.5, "label": "Senior Validator"},
    "ANCHOR_VALIDATOR": {"min_bio": 20_000 * 100_000_000,      "reward_mult": 2.0, "weight_mult": 2.0, "label": "Anchor Validator"},
}

def get_tier(bio_amount: int) -> str:
    """Reads thresholds FROM STAKE_TIERS, not from hardcoded numbers."""
    if bio_amount >= STAKE_TIERS["ANCHOR_VALIDATOR"]["min_bio"]: return "ANCHOR_VALIDATOR"
    if bio_amount >= STAKE_TIERS["SENIOR_VALIDATOR"]["min_bio"]: return "SENIOR_VALIDATOR"
    if bio_amount >= STAKE_TIERS["VALIDATOR"]["min_bio"]:        return "VALIDATOR"
    return "NONE"

GOVERNANCE_THRESHOLD = 0.70
GOVERNANCE_MIN_VOTES = 21
GOVERNANCE_TIMELOCK  = 7 * 86400
UNSTAKE_COOLDOWN     = 7 * 86400
LISTING_REWARD       = 1000 * SAT_PER_BIO

DEVELOPER_GRANTS_POOL_SIZE = 509_000 * SAT_PER_BIO
DEVELOPER_GRANT_MAX        = 5_000 * SAT_PER_BIO

SERVER_REWARDS_POOL_SIZE  = 254_500 * SAT_PER_BIO
DEVELOPER_GRANTS_POOL_SIZE_V41 = 509_000 * SAT_PER_BIO - SERVER_REWARDS_POOL_SIZE
SERVER_REWARD_MAX = 2_000 * SAT_PER_BIO

SWAP_MIN_LOCK        = 1 * SAT_PER_BIO
SWAP_LOCK_TIMEOUT_MIN = 3600
SWAP_LOCK_TIMEOUT_MAX = 7 * 86400
SWAP_OFFER_TTL_MIN    = 3600
SWAP_OFFER_TTL_MAX    = 30 * 86400
SWAP_MAX_ACTIVE_LOCKS = 10
SWAP_ASSET_MAX_LEN    = 32

STATE_SNAPSHOT_EVERY  = 5000
STATE_SNAPSHOT_KEEP   = 3
SNAPSHOT_DIR          = "snapshots"
CHECKPOINT_EVERY     = 1000
assert STATE_SNAPSHOT_EVERY % CHECKPOINT_EVERY == 0, (
    "STATE_SNAPSHOT_EVERY must be a multiple of CHECKPOINT_EVERY -- "
    "otherwise a state snapshot could fire on a height where no "
    "lightweight checkpoint row exists yet to attach its hash to.")

WAL_CHECKPOINT_EVERY = 5000
RATE_LIMIT_PER_MIN   = 60
RATE_LIMIT_WINDOW    = 60
MEMPOOL_MAX          = 1000
PAYLOAD_MAX_CHARS    = 4096

DEFAULT_BOOTSTRAP_PEERS = [
    "https://biochainnetwork.com/api",
    "https://node2.biochainnetwork.com/api",
]
_ENV_PEERS = os.environ.get("BIOCHAIN_PEER_URLS", "").strip()
SELF_URL = os.environ.get("BIOCHAIN_SELF_URL", "").strip().rstrip("/")
if _ENV_PEERS.lower() in ("none", "standalone", "off"):
    PEER_URLS = []
elif _ENV_PEERS:
    PEER_URLS = [u.strip().rstrip("/") for u in _ENV_PEERS.split(",") if u.strip()]
else:
    PEER_URLS = list(DEFAULT_BOOTSTRAP_PEERS)
if SELF_URL:
    PEER_URLS = [u for u in PEER_URLS if u != SELF_URL]
PEER_SYNC_INTERVAL_SECONDS = 15
PEER_REQUEST_TIMEOUT_SECONDS = 5

INSTANCE_ID = secrets.token_hex(16)

GOVERNABLE_PARAMS = {
    "emerge_threshold":    {"min": 3,        "max": 1000,        "cast": int},
    "burn_rate":           {"min": 0.00001,  "max": 0.01,        "cast": float},
    "theta_s":             {"min": 0.01,     "max": 0.9,         "cast": float},
    "theta_w":             {"min": 0.0,      "max": 1_000_000.0, "cast": float},
    "theta_i":             {"min": 1.0,      "max": 1_000_000.0, "cast": float},
    "rate_limit_per_min":  {"min": 1,        "max": 10_000,      "cast": int},
    "checkpoint_every":    {"min": 10,       "max": 100_000,     "cast": int},
    "tier_validator_min":  {"min": 1.0,      "max": 10_000_000.0,"cast": float},
    "tier_senior_min":     {"min": 1.0,      "max": 10_000_000.0,"cast": float},
    "tier_anchor_min":     {"min": 1.0,      "max": 10_000_000.0,"cast": float},
    "governance_min_votes":{"min": 21,       "max": 10_000,      "cast": int},
    "longevity_monthly_reward": {"min": 0.1, "max": 21.0,        "cast": float},
    "min_emergence_span_seconds": {"min": 86400, "max": 90 * 86400, "cast": int},
    "fee_burn_percent": {"min": 0, "max": 50, "cast": int},
    "transfer_fee_flat": {"min": 0.0, "max": 1.0, "cast": float},
}

def _current_param_value(key: str):
    """Current live value of a governable parameter -- for API transparency"""
    return {
        "emerge_threshold":   EMERGE_THRESHOLD,
        "burn_rate":          Emission.BURN_RATE,
        "transfer_fee_flat":  sat_to_bio(Emission.TRANSFER_FEE_BASE),
        "theta_s":            net.THETA_S,
        "theta_w":            net.THETA_W,
        "theta_i":            net.THETA_I,
        "rate_limit_per_min": RATE_LIMIT_PER_MIN,
        "checkpoint_every":   CHECKPOINT_EVERY,
        "tier_validator_min": sat_to_bio(STAKE_TIERS["VALIDATOR"]["min_bio"]),
        "tier_senior_min":    sat_to_bio(STAKE_TIERS["SENIOR_VALIDATOR"]["min_bio"]),
        "tier_anchor_min":    sat_to_bio(STAKE_TIERS["ANCHOR_VALIDATOR"]["min_bio"]),
        "governance_min_votes": GOVERNANCE_MIN_VOTES,
        "longevity_monthly_reward": LONGEVITY_MONTHLY_REWARD,
        "min_emergence_span_seconds": MIN_EMERGENCE_SPAN_SECONDS,
        "fee_burn_percent": Emission.FEE_BURN_PERCENT,
    }.get(key)

def apply_governance_param(key: str, raw_value: str, proposal_id: int = 0):
    """Applies an approved parameter to the live network."""
    global EMERGE_THRESHOLD, REBIRTH_THRESHOLD, RATE_LIMIT_PER_MIN, CHECKPOINT_EVERY, GOVERNANCE_MIN_VOTES, LONGEVITY_MONTHLY_REWARD, MIN_EMERGENCE_SPAN_SECONDS

    if key == "slash":
        try:
            data   = json.loads(raw_value)
            target = data["address"]
            amount = bio_to_sat(data["amount"])
            reason = data.get("reason", "")
        except Exception as e:
            return False, f"invalid slash format -- needs JSON {{address,amount,reason}}: {e}"
        return _apply_slash(target, amount, reason)

    if key == "listing_reward":
        try:
            data   = json.loads(raw_value)
            target = data["address"]
            exchange_name   = data.get("exchange_name", "")
            pair_identifier = data.get("pair_identifier", "")
            amount_bio      = data.get("amount", None)
            if amount_bio is None:
                amount_sat = LISTING_REWARD
            else:
                amount_sat = bio_to_sat(amount_bio)
                if amount_sat < 1 * SAT_PER_BIO or amount_sat > LISTING_REWARD:
                    return False, (f"listing_reward amount out of range: {amount_bio} BIO "
                                   f"(allowed 1 .. {sat_to_bio(LISTING_REWARD):.0f} BIO)")
        except Exception as e:
            return False, f"invalid listing_reward format -- needs JSON {{address,exchange_name,pair_identifier,amount?}}: {e}"
        return _apply_listing_reward(target, exchange_name, pair_identifier, proposal_id, amount_sat)

    if key == "developer_grant":
        try:
            data   = json.loads(raw_value)
            target = data["address"]
            project_name = data.get("project_name", "")
            project_description = data.get("project_description", "")
            amount_bio = data.get("amount", None)
            if amount_bio is None:
                amount_sat = DEVELOPER_GRANT_MAX
            else:
                amount_sat = bio_to_sat(amount_bio)
                if amount_sat < 1 * SAT_PER_BIO or amount_sat > DEVELOPER_GRANT_MAX:
                    return False, (f"developer_grant amount out of range: {amount_bio} BIO "
                                   f"(allowed 1 .. {sat_to_bio(DEVELOPER_GRANT_MAX):.0f} BIO)")
        except Exception as e:
            return False, f"invalid developer_grant format -- needs JSON {{address,project_name,project_description,amount?}}: {e}"
        return _apply_developer_grant(target, project_name, project_description, proposal_id, amount_sat)

    if key == "server_reward":
        try:
            data   = json.loads(raw_value)
            target = data["address"]
            url    = data.get("url", "")
            amount_bio = data.get("amount", None)
            if amount_bio is None:
                amount_sat = SERVER_REWARD_MAX
            else:
                amount_sat = bio_to_sat(amount_bio)
                if amount_sat < 1 * SAT_PER_BIO or amount_sat > SERVER_REWARD_MAX:
                    return False, (f"server_reward amount out of range: {amount_bio} BIO "
                                   f"(allowed 1 .. {sat_to_bio(SERVER_REWARD_MAX):.0f} BIO)")
        except Exception as e:
            return False, f"invalid server_reward format -- needs JSON {{address,url,amount?}}: {e}"
        return _apply_server_reward(target, url, proposal_id, amount_sat)

    spec = GOVERNABLE_PARAMS.get(key)
    if not spec:
        return False, f"parameter '{key}' is not governable by vote"
    try:
        value = spec["cast"](raw_value)
    except Exception:
        return False, f"could not cast '{raw_value}' to the required type"
    if value < spec["min"] or value > spec["max"]:
        return False, f"value {value} is outside bounds [{spec['min']}, {spec['max']}]"

    if key == "emerge_threshold":
        EMERGE_THRESHOLD  = int(value)
        REBIRTH_THRESHOLD = EMERGE_THRESHOLD
    elif key == "burn_rate":
        Emission.BURN_RATE_PPM = int(round(value * 1_000_000))
        Emission.BURN_RATE     = Emission.BURN_RATE_PPM / 1_000_000
    elif key == "transfer_fee_flat":
        Emission.TRANSFER_FEE_BASE = int(round(value * SAT_PER_BIO))
    elif key == "theta_s":
        net.THETA_S = value
    elif key == "theta_w":
        net.THETA_W = value
    elif key == "theta_i":
        net.THETA_I = value
    elif key == "rate_limit_per_min":
        RATE_LIMIT_PER_MIN = int(value)
    elif key == "checkpoint_every":
        CHECKPOINT_EVERY = int(value)
    elif key == "min_emergence_span_seconds":
        MIN_EMERGENCE_SPAN_SECONDS = int(value)
    elif key == "fee_burn_percent":
        Emission.FEE_BURN_PERCENT = int(value)
    elif key == "tier_validator_min":
        STAKE_TIERS["VALIDATOR"]["min_bio"] = bio_to_sat(value)
    elif key == "tier_senior_min":
        STAKE_TIERS["SENIOR_VALIDATOR"]["min_bio"] = bio_to_sat(value)
    elif key == "tier_anchor_min":
        STAKE_TIERS["ANCHOR_VALIDATOR"]["min_bio"] = bio_to_sat(value)
    elif key == "governance_min_votes":
        GOVERNANCE_MIN_VOTES = int(value)
    elif key == "longevity_monthly_reward":
        LONGEVITY_MONTHLY_REWARD = value
    else:
        return False, "not implemented"

    db.set_param_override(key, value)
    return True, f"{key} = {value}"

ROLES = ["VALIDATOR", "KEEPER", "ROUTER"]

ROLE_BONUS = {
    "VALIDATOR": {"energy": 1.0, "reputation": 0.02},
    "KEEPER":    {"energy": 2.0, "reputation": 0.01},
    "ROUTER":    {"energy": 0.5, "reputation": 0.01},
}

INHERITANCE_GOOD = 0.5
INHERITANCE_BAD  = 0.3

class RateLimiter:
    """Spam protection -- at most RATE_LIMIT_PER_MIN transactions per minute from a single address."""
    def __init__(self):
        self._counts = {}
        self._lock   = threading.Lock()

    def check(self, address: str) -> bool:
        """True if allowed, False if the limit was exceeded"""
        now = time.time()
        with self._lock:
            if address not in self._counts:
                self._counts[address] = []
            self._counts[address] = [
                t for t in self._counts[address]
                if now - t < RATE_LIMIT_WINDOW
            ]
            if len(self._counts[address]) >= RATE_LIMIT_PER_MIN:
                return False
            self._counts[address].append(now)
            return True

rate_limiter = RateLimiter()

_chain_lock = threading.RLock()

_PQ_BACKEND = None

try:
    import oqs as _oqs

    class _LiboqsMLDSA44:
        """Same keygen/sign/verify interface as dilithium_py's ML_DSA_44,
        backed by liboqs. Signature objects are created per call -- they
        are cheap C allocations, and per-call creation avoids any
        question of sharing one C object across uvicorn's threads."""
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

    Dilithium = _LiboqsMLDSA44
    _PQ_BACKEND = "liboqs"
    print(f"[PQ] ML-DSA-44 via liboqs C backend (liboqs {_oqs.oqs_version()}, "
          f"python bindings {_oqs.oqs_python_version()}) -- self-test passed")
except Exception as _liboqs_err:
    try:
        from dilithium_py.ml_dsa import ML_DSA_44 as Dilithium
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

class PQCrypto:
    """cryptographic agility foundation: address() and verify() now accept an optional scheme_id, defaulting to "MLDSA44" -- which reproduces the EXACT formula."""

    def generate_keypair(self):
        return Dilithium.keygen()

    def sign(self, sk, message: str) -> str:
        return Dilithium.sign(sk, message.encode()).hex()

    def verify(self, pk, message: str, signature: str, scheme_id: str = "MLDSA44") -> bool:
        if scheme_id != "MLDSA44":
            print(f"[PQ] verify error: unknown scheme_id '{scheme_id}'")
            return False
        try:
            return Dilithium.verify(pk, message.encode(), bytes.fromhex(signature))
        except Exception as e:
            print(f"[PQ] verify error: {e}")
            return False

    def address(self, pk, scheme_id: str = "MLDSA44") -> str:
        raw = pk if isinstance(pk, bytes) else str(pk).encode()
        if scheme_id == "MLDSA44":
            return "BIO1" + hashlib.sha3_256(raw).hexdigest()[:16].upper()
        tagged = scheme_id.encode() + raw
        return "BIO1" + hashlib.sha3_256(tagged).hexdigest()[:16].upper()

pq = PQCrypto()

REQUEST_FRESHNESS_SECONDS = 120

def verify_signed_request(address: str, pubkey_hex: str, signature_hex: str,
                           message: str, timestamp: float):
    """Verifies that `address`'s owner actually authorized this exact request."""
    now = time.time()
    if abs(now - timestamp) > REQUEST_FRESHNESS_SECONDS:
        return False, f"request expired or clock skew too large (signatures are valid for {REQUEST_FRESHNESS_SECONDS}s)"

    try:
        pubkey = bytes.fromhex(pubkey_hex)
    except Exception:
        return False, "pubkey must be hex-encoded"

    if pq.address(pubkey) != address:
        return False, "pubkey does not match the claimed address"

    if not pq.verify(pubkey, message, signature_hex):
        return False, "invalid signature"

    return True, ""

app = FastAPI(title="BioChain AAECN")
_cors_env = os.environ.get("BIOCHAIN_CORS_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()] or ["*"]
if _cors_origins == ["*"]:
    print("[SECURITY] CORS allow_origins='*' -- fine for local/dev use "
          "(same-device browser testing). Before exposing this server "
          "beyond localhost, set BIOCHAIN_CORS_ORIGINS to your wallet's "
          "real origin(s), e.g. BIOCHAIN_CORS_ORIGINS=https://wallet.example.com")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
ws_clients = set()

DB_PATH = "biochain.db"

class Database:
    def __init__(self, path=DB_PATH):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.lock = threading.RLock()
        self._in_txn = False
        self._init()

    def _init(self):
        with self.lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS wallets (
                    address     TEXT PRIMARY KEY,
                    balance     INTEGER DEFAULT 0,
                    first_seen  REAL DEFAULT 0,
                    tx_count    INTEGER DEFAULT 0,
                    genesis_got INTEGER DEFAULT 0,
                    registration_got INTEGER DEFAULT 0
                    ,sig_scheme TEXT DEFAULT 'MLDSA44'
                );

                CREATE TABLE IF NOT EXISTS nodes (
                    address          TEXT PRIMARY KEY,
                    balance          INTEGER DEFAULT 0,
                    energy           REAL DEFAULT 10,
                    activity         INTEGER DEFAULT 0,
                    recent_activity  REAL DEFAULT 0,
                    reputation       REAL DEFAULT 1.0,
                    age              REAL DEFAULT 0,
                    alive            INTEGER DEFAULT 1,
                    births           INTEGER DEFAULT 1,
                    born_at          REAL DEFAULT 0,
                    died_at          REAL DEFAULT 0,
                    role             TEXT DEFAULT 'VALIDATOR',
                    risk             REAL DEFAULT 0,
                    longevity_6mo    INTEGER DEFAULT 0,
                    longevity_12mo   INTEGER DEFAULT 0,
                    last_monthly_payout REAL DEFAULT 0,
                    tx_count_at_death INTEGER DEFAULT 0,
                    inherited_rep    REAL DEFAULT 0,
                    inherited_risk   REAL DEFAULT 0,
                    state_block      INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS blocks (
                    idx          INTEGER PRIMARY KEY,
                    hash         TEXT NOT NULL,
                    prev_hash    TEXT NOT NULL,
                    validator    TEXT NOT NULL,
                    reward       INTEGER DEFAULT 0,
                    timestamp    REAL NOT NULL,
                    imp_id       TEXT NOT NULL,
                    imp_sender   TEXT NOT NULL,
                    imp_receiver TEXT NOT NULL,
                    imp_value    INTEGER NOT NULL,
                    imp_energy   REAL NOT NULL,
                    imp_phi_bio  REAL NOT NULL,
                    imp_pubkey    TEXT DEFAULT '',
                    imp_signature TEXT DEFAULT '',
                    imp_signed_ts REAL DEFAULT 0,
                    imp_kind      TEXT DEFAULT 'TRANSFER',
                    imp_payload   TEXT DEFAULT '',
                    imp_nonce     INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS address_nonces (
                    address TEXT PRIMARY KEY,
                    nonce   INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS economy (
                    id               INTEGER PRIMARY KEY DEFAULT 1,
                    liquidity        REAL DEFAULT 100,
                    risk             REAL DEFAULT 1,
                    minted           INTEGER DEFAULT 0,
                    burned           INTEGER DEFAULT 0,
                    halvings         INTEGER DEFAULT 0,
                    genesis_granted  INTEGER DEFAULT 0,
                    pool_validators  INTEGER DEFAULT 840000000000000,
                    pool_ecosystem   INTEGER DEFAULT 630000000000000,
                    pool_reserve     INTEGER DEFAULT 420000000000000,
                    pool_team        INTEGER DEFAULT 105000000000000,
                    pool_genesis     INTEGER DEFAULT 82000000000000,
                    pool_listing_reserve INTEGER DEFAULT 23000000000000,
                    emission_start   REAL DEFAULT 0,
                    pool_wallet_registration INTEGER DEFAULT 0,
                    total_destroyed INTEGER DEFAULT 0,
                    pool_developer_grants INTEGER DEFAULT 0
                    ,pool_server_rewards INTEGER DEFAULT 0
                );


                CREATE TABLE IF NOT EXISTS server_rewards_paid (
                    url          TEXT PRIMARY KEY,
                    address      TEXT NOT NULL,
                    amount       INTEGER NOT NULL,
                    paid_at      REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS developer_grants (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    address              TEXT NOT NULL,
                    project_name         TEXT NOT NULL,
                    project_description  TEXT NOT NULL,
                    amount               INTEGER NOT NULL,
                    granted_at           REAL NOT NULL,
                    proposal_id          INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    type      TEXT NOT NULL,
                    message   TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS checkpoints (
                    block_idx   INTEGER PRIMARY KEY,
                    block_hash  TEXT NOT NULL,
                    created_at  REAL NOT NULL,
                    nodes_alive INTEGER DEFAULT 0,
                    state_hash  TEXT DEFAULT NULL
                );

                CREATE TABLE IF NOT EXISTS param_overrides (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS promoted_peers (
                    url                         TEXT PRIMARY KEY,
                    promoted_at                 REAL NOT NULL,
                    confirmations_at_promotion  INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS node_candidates (
                    url               TEXT PRIMARY KEY,
                    first_seen_at     REAL NOT NULL,
                    last_confirmed_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS candidate_reports (
                    url            TEXT NOT NULL,
                    reporter_url   TEXT NOT NULL,
                    reported_at    REAL NOT NULL,
                    PRIMARY KEY (url, reporter_url)
                );

                CREATE TABLE IF NOT EXISTS used_signatures (
                    signature TEXT PRIMARY KEY,
                    address   TEXT NOT NULL,
                    used_at   REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vesting (
                    id              INTEGER PRIMARY KEY DEFAULT 1,
                    address         TEXT NOT NULL,
                    start_time      REAL DEFAULT 0,
                    claimed_months  INTEGER DEFAULT 0,
                    total_claimed   INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS stakes (
                    address     TEXT PRIMARY KEY,
                    bio_amount  INTEGER DEFAULT 0,
                    tier        TEXT DEFAULT 'NONE',
                    staked_at   REAL DEFAULT 0,
                    slashed     INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS swap_locks (
                    id           TEXT PRIMARY KEY,
                    sender       TEXT NOT NULL,
                    receiver     TEXT NOT NULL,
                    amount       INTEGER NOT NULL,
                    hash_lock    TEXT NOT NULL UNIQUE,
                    created_t    REAL NOT NULL,
                    timeout      INTEGER NOT NULL,
                    state        TEXT DEFAULT 'LOCKED',
                    preimage     TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS swap_offers (
                    id           TEXT PRIMARY KEY,
                    sender       TEXT NOT NULL,
                    give_amount  INTEGER NOT NULL,
                    want_asset   TEXT NOT NULL,
                    want_amount  INTEGER NOT NULL,
                    ext_address  TEXT NOT NULL,
                    created_t    REAL NOT NULL,
                    ttl          INTEGER NOT NULL,
                    state        TEXT DEFAULT 'ACTIVE'
                );
                CREATE TABLE IF NOT EXISTS pending_unstakes (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    address      TEXT NOT NULL,
                    bio_amount   INTEGER NOT NULL,
                    requested_at REAL NOT NULL,
                    claimed      INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS loans (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    borrower                TEXT NOT NULL,
                    collateral_type         TEXT NOT NULL,
                    collateral_amount       INTEGER NOT NULL,
                    bio_borrowed            INTEGER NOT NULL,
                    opened_at               REAL NOT NULL,
                    status                  TEXT DEFAULT 'PENDING_VERIFICATION',
                    closed_at               REAL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS recognized_pairs (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange_name    TEXT NOT NULL,
                    pair_identifier  TEXT NOT NULL,
                    recognized_at    REAL NOT NULL,
                    proposal_id      INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS proposals (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    title         TEXT NOT NULL,
                    description   TEXT DEFAULT '',
                    proposer      TEXT NOT NULL,
                    created_at    REAL NOT NULL,
                    ends_at       REAL NOT NULL,
                    apply_at      REAL NOT NULL,
                    status        TEXT DEFAULT 'ACTIVE',
                    votes_for     REAL DEFAULT 0,
                    votes_against REAL DEFAULT 0,
                    param_key     TEXT DEFAULT '',
                    param_value   TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS votes (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id INTEGER NOT NULL,
                    voter       TEXT NOT NULL,
                    vote        TEXT NOT NULL,
                    weight      REAL DEFAULT 1,
                    voted_at    REAL NOT NULL,
                    UNIQUE(proposal_id, voter)
                );
            """)
            self._commit()

            try:
                self.conn.execute(
                    "ALTER TABLE blocks ADD COLUMN imp_nonce INTEGER DEFAULT 0"
                )
                self._commit()
            except Exception:
                pass

            try:
                self.conn.execute(
                    "ALTER TABLE economy ADD COLUMN pool_wallet_registration INTEGER DEFAULT 0"
                )
                self._commit()
            except Exception:
                pass

            try:
                self.conn.execute(
                    "ALTER TABLE economy ADD COLUMN total_destroyed INTEGER DEFAULT 0"
                )
                self._commit()
            except Exception:
                pass

            try:
                self.conn.execute(
                    "ALTER TABLE economy ADD COLUMN pool_developer_grants INTEGER DEFAULT 0"
                )
                self._commit()
            except Exception:
                pass

            try:
                self.conn.execute(
                    "ALTER TABLE economy ADD COLUMN pool_server_rewards INTEGER DEFAULT 0"
                )
                self._commit()
            except Exception:
                pass

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS server_rewards_paid (
                    url          TEXT PRIMARY KEY,
                    address      TEXT NOT NULL,
                    amount       INTEGER NOT NULL,
                    paid_at      REAL NOT NULL
                )
            """)
            self._commit()

            try:
                self.conn.execute(
                    "ALTER TABLE wallets ADD COLUMN registration_got INTEGER DEFAULT 0"
                )
                self._commit()
            except Exception:
                pass

            try:
                self.conn.execute(
                    "ALTER TABLE wallets ADD COLUMN sig_scheme TEXT DEFAULT 'MLDSA44'"
                )
                self._commit()
            except Exception:
                pass

            try:
                self.conn.execute(
                    "ALTER TABLE nodes ADD COLUMN state_block INTEGER DEFAULT 0"
                )
                self._commit()
            except Exception:
                pass

    def _commit(self):
        """Commits right away, or defers if inside a transaction() block."""
        if not self._in_txn:
            self.conn.commit()

    @contextmanager
    def transaction(self):
        """Groups writes into one all-or-nothing unit."""
        with self.lock:
            if self._in_txn:
                yield
                return
            self._in_txn = True
            try:
                yield
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            finally:
                self._in_txn = False

    def ensure_wallet(self, address: str):
        with self.lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO wallets (address, balance, first_seen, tx_count, genesis_got, registration_got) "
                "VALUES (?,0,?,0,0,0)",
                (address, time.time())
            )
            self._commit()

    def get_balance(self, address: str) -> int:
        """Returns the balance in SATS (int)."""
        with self.lock:
            row = self.conn.execute(
                "SELECT balance FROM wallets WHERE address=?", (address,)
            ).fetchone()
            return int(row["balance"]) if row else 0

    def get_wallet(self, address: str):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM wallets WHERE address=?", (address,)
            ).fetchone()

    def get_tx_count(self, address: str) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT tx_count FROM wallets WHERE address=?", (address,)
            ).fetchone()
            return int(row["tx_count"]) if row else 0

    def count_wallets(self) -> int:
        with self.lock:
            row = self.conn.execute("SELECT COUNT(*) as c FROM wallets").fetchone()
            return int(row["c"]) if row else 0


    def debit(self, address: str, amount: int) -> bool:
        """amount is in SATS (int)."""
        amount = int(amount)
        if amount < 0:
            return False
        with self.lock:
            row = self.conn.execute(
                "SELECT balance FROM wallets WHERE address=?", (address,)
            ).fetchone()
            if not row or int(row["balance"]) < amount:
                return False
            self.conn.execute(
                "UPDATE wallets SET balance=balance-?, tx_count=tx_count+1 WHERE address=?",
                (amount, address)
            )
            self._commit()
        return True

    def credit(self, address: str, amount: int):
        """amount is in SATS (int)."""
        amount = int(amount)
        if amount < 0:
            raise ValueError("negative credit is forbidden -- use debit")
        self.ensure_wallet(address)
        with self.lock:
            self.conn.execute(
                "UPDATE wallets SET balance=balance+? WHERE address=?",
                (amount, address)
            )
            self._commit()

    def inc_tx_count(self, address: str):
        """Increment the outgoing impulse counter"""
        with self.lock:
            self.conn.execute(
                "UPDATE wallets SET tx_count=tx_count+1 WHERE address=?",
                (address,)
            )
            self._commit()

    def try_give_genesis(self, address: str, amount: int) -> int:
        with self.lock:
            cur = self.conn.execute(
                "UPDATE wallets SET balance=balance+?, genesis_got=1 "
                "WHERE address=? AND genesis_got=0",
                (amount, address)
            )
            if cur.rowcount == 0:
                return 0
            self.conn.execute(
                "INSERT INTO events(timestamp,type,message) VALUES(?,?,?)",
                (time.time(), "GENESIS_GRANT", f"{address} +{sat_to_bio(amount)} BIO")
            )
            self._commit()
        return amount

    def registration_granted_count(self) -> int:
        """counted directly from the wallets table (COUNT of registration_got=1 rows), not a separately-persisted in-memory counter like genesis_granted."""
        with self.lock:
            return int(self.conn.execute(
                "SELECT COUNT(*) c FROM wallets WHERE registration_got=1").fetchone()["c"])

    def try_give_registration(self, address: str, amount: int) -> int:
        """first-100 wallet-registration grant."""
        with self.lock:
            cur = self.conn.execute(
                "UPDATE wallets SET balance=balance+?, registration_got=1 "
                "WHERE address=? AND registration_got=0",
                (amount, address)
            )
            if cur.rowcount == 0:
                return 0
            self.conn.execute(
                "INSERT INTO events(timestamp,type,message) VALUES(?,?,?)",
                (time.time(), "REGISTRATION_GRANT", f"{address} +{sat_to_bio(amount)} BIO")
            )
            self._commit()
        return amount

    def save_node(self, node):
        with self.lock:
            self.conn.execute("""
                INSERT OR REPLACE INTO nodes
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                node.address, node.balance, node.energy,
                node.activity, node.recent_activity, node.reputation, node.age,
                1 if node.alive else 0, node.births,
                node.born_at, node.died_at,
                node.role, node.risk,
                1 if node.longevity_6mo_paid else 0,
                1 if node.longevity_12mo_paid else 0,
                node.last_monthly_payout,
                getattr(node, "tx_count_at_death", 0) or 0,
                getattr(node, "inherited_rep", 0.0) or 0.0,
                getattr(node, "inherited_risk", 0.0) or 0.0,
                getattr(node, "state_block", 0) or 0,
            ))
            self._commit()

    def load_nodes(self):
        with self.lock:
            return self.conn.execute("SELECT * FROM nodes").fetchall()

    def count_alive_nodes(self) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) as c FROM nodes WHERE alive=1"
            ).fetchone()
            return int(row["c"]) if row else 0


    def save_block(self, block):
        with self.lock:
            self.conn.execute("""
                INSERT OR REPLACE INTO blocks VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                block.index, block.hash, block.prev_hash,
                block.validator,
                block.reward, block.t,
                block.impulse.id,
                block.impulse.sender, block.impulse.receiver,
                block.impulse.value, block.impulse.energy,
                block.impulse.phi_bio,
                getattr(block.impulse, "pubkey_hex", "") or "",
                getattr(block.impulse, "signature_hex", "") or "",
                getattr(block.impulse, "signed_timestamp", 0.0) or 0.0,
                getattr(block.impulse, "kind", "TRANSFER") or "TRANSFER",
                getattr(block.impulse, "payload", "") or "",
                getattr(block.impulse, "nonce", 0) or 0,
            ))
            self._commit()

    def save_economy(self, eco, em):
        with self.lock:
            self.conn.execute("""
                INSERT OR REPLACE INTO economy
                (id, liquidity, risk, minted, burned, halvings, genesis_granted,
                 pool_validators, pool_ecosystem, pool_reserve, pool_team,
                 pool_genesis, pool_listing_reserve, emission_start,
                 pool_wallet_registration, total_destroyed, pool_developer_grants,
                 pool_server_rewards)
                VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                eco.liquidity, eco.risk,
                em.minted, em.burned, em.halvings, em.genesis_granted,
                em.pools["validators"], em.pools["ecosystem"],
                em.pools["reserve"], em.pools["team"], em.pools["genesis"],
                em.pools["listing_reserve"],
                em.start_time,
                em.pools.get("wallet_registration", 0),
                em.total_destroyed,
                em.pools.get("developer_grants", 0),
                em.pools.get("server_rewards", 0),
            ))
            self._commit()

    def load_economy(self):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM economy WHERE id=1"
            ).fetchone()


    def log(self, event_type: str, message: str):
        with self.lock:
            self.conn.execute(
                "INSERT INTO events(timestamp,type,message) VALUES(?,?,?)",
                (time.time(), event_type, message)
            )
            self._commit()

    def recent_events(self, limit=30):
        with self.lock:
            return self.conn.execute(
                "SELECT timestamp,type,message FROM events ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()

    def load_blocks(self):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM blocks ORDER BY idx"
            ).fetchall()

    def load_blocks_streaming(self):
        """PERF FIX (v5.43): same query as load_blocks(), but yields rows
        one at a time via the cursor instead of fetchall()'ing the whole
        result set into memory up front. Used by restore() specifically
        so that cold-block rows (outside the hot window) can be dropped
        by Python's GC as soon as they're processed into a lightweight
        _LazyBlock, rather than every row's raw pubkey/signature hex
        strings staying resident for the whole restore just because
        fetchall() grabbed them all at once."""
        with self.lock:
            cursor = self.conn.execute("SELECT * FROM blocks ORDER BY idx")
            for row in cursor:
                yield row

    def count_blocks_table(self) -> int:
        """Cheap COUNT(*) -- used by restore() to compute the hot-window
        cutoff before streaming rows, without needing two full passes."""
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) c FROM blocks").fetchone()["c"]

    def load_block_by_index(self, index: int):
        """Single-row lookup, used by _LazyBlock.impulse's first access --
        the whole point of lazy loading is to never pay for this unless
        someone actually needs a specific COLD block's full impulse
        details (fork resolution touching old history, a manual /block
        query -- not the hot per-transaction path)."""
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM blocks WHERE idx = ?", (index,)
            ).fetchone()

    def wal_checkpoint(self):
        """Forces SQLite to flush its WAL file into the main DB file and
        truncate it back down -- pure local disk housekeeping, not
        consensus-relevant in any way. See WAL_CHECKPOINT_EVERY for the
        naming-collision note and the real incident that motivated this.

        Returns (busy, log_frames, checkpointed_frames) so the caller can
        tell a full truncation from a partial one -- a partial checkpoint
        does NOT raise an exception on its own (SQLite just returns
        busy=1 silently), which is exactly how an earlier version of
        this went unnoticed: it "succeeded" without actually truncating,
        inside a single long-running process with many prior queries."""
        with self.lock:
            row = self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            busy, log_frames, checkpointed = tuple(row)
            if busy:
                print(f"[WAL] checkpoint did NOT fully truncate -- busy={busy}, "
                      f"{checkpointed}/{log_frames} frames checkpointed. Likely "
                      f"cause: another open cursor/read transaction on this "
                      f"connection. WAL will keep growing until a checkpoint "
                      f"succeeds fully.")
            return busy, log_frames, checkpointed

    def count_blocks(self) -> int:
        with self.lock:
            row = self.conn.execute("SELECT COUNT(*) as c FROM blocks").fetchone()
            return int(row["c"]) if row else 0


    def save_checkpoint(self, block_idx: int, block_hash: str, nodes_alive: int, state_hash: str = None):
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO checkpoints (block_idx, block_hash, created_at, nodes_alive, state_hash) VALUES (?,?,?,?,?)",
                (block_idx, block_hash, time.time(), nodes_alive, state_hash)
            )
            self._commit()

    def set_checkpoint_state_hash(self, block_idx: int, state_hash: str):
        """Attach a state_hash to an EXISTING checkpoint row after the snapshot file has been written and hashed -- keeps checkpoint creation (fast, always happens)."""
        with self.lock:
            cur = self.conn.execute(
                "UPDATE checkpoints SET state_hash=? WHERE block_idx=?",
                (state_hash, block_idx))
            self._commit()
            if cur.rowcount == 0:
                print(f"[SNAPSHOT] WARNING: no checkpoint row at height {block_idx} "
                      f"to attach state_hash to -- this should be unreachable "
                      f"given the STATE_SNAPSHOT_EVERY invariant")

    def get_checkpoint(self, block_idx: int):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM checkpoints WHERE block_idx=?", (block_idx,)).fetchone()

    def get_last_checkpoint(self):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM checkpoints ORDER BY block_idx DESC LIMIT 1"
            ).fetchone()

    def get_all_checkpoints(self):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM checkpoints ORDER BY block_idx DESC"
            ).fetchall()


    def get_param_overrides(self):
        with self.lock:
            return self.conn.execute("SELECT key, value FROM param_overrides").fetchall()

    def set_param_override(self, key: str, value):
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO param_overrides VALUES (?,?)",
                (key, str(value))
            )
            self._commit()

    def use_signature_once(self, signature: str, address: str, used_at: float) -> bool:
        """Atomically records a signature as spent."""
        try:
            with self.lock:
                self.conn.execute(
                    "INSERT INTO used_signatures VALUES (?,?,?)",
                    (signature, address, used_at)
                )
                self._commit()
            return True
        except Exception:
            return False

    def peek_nonce(self, address: str) -> int:
        """Highest nonce this address has successfully used so far."""
        with self.lock:
            row = self.conn.execute(
                "SELECT nonce FROM address_nonces WHERE address=?", (address,)
            ).fetchone()
            return int(row[0]) if row else 0

    def use_nonce(self, address: str, nonce: int) -> bool:
        """Atomically spends `nonce` for `address`."""
        try:
            nonce = int(nonce)
        except Exception:
            return False
        with self.lock:
            row = self.conn.execute(
                "SELECT nonce FROM address_nonces WHERE address=?", (address,)
            ).fetchone()
            current = int(row[0]) if row else 0
            if nonce <= current:
                return False
            self.conn.execute(
                "INSERT INTO address_nonces(address, nonce) VALUES(?,?) "
                "ON CONFLICT(address) DO UPDATE SET nonce=excluded.nonce",
                (address, nonce),
            )
            self._commit()
            return True

    def prune_old_signatures(self, older_than: float):
        """Anything older than the freshness window can never be replayed successfully anyway (verify_signed_request rejects it on staleness first) -- safe to delete so."""
        with self.lock:
            self.conn.execute("DELETE FROM used_signatures WHERE used_at < ?", (older_than,))
            self._commit()

    def get_vesting(self):
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM vesting WHERE id=1"
            ).fetchone()
            return row

    def init_vesting(self, address: str):
        """Initializes vesting on first launch"""
        with self.lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO vesting VALUES (1,?,?,0,0)",
                (address, time.time())
            )
            self._commit()

    def save_vesting(self, claimed_months: int, total_claimed: float):
        with self.lock:
            self.conn.execute(
                "UPDATE vesting SET claimed_months=?, total_claimed=? WHERE id=1",
                (claimed_months, total_claimed)
            )
            self._commit()

    def set_vesting_start(self, start_time: float):
        """Re-anchors the vesting clock to a deterministic instant (the chain's own genesis time)."""
        with self.lock:
            self.conn.execute(
                "UPDATE vesting SET start_time=? WHERE id=1",
                (start_time,)
            )
            self._commit()

    def get_stake(self, address: str):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM stakes WHERE address=?", (address,)
            ).fetchone()

    def save_stake(self, address: str, bio_amount: float, tier: str):
        """UPSERT that PRESERVES the slashed accumulator."""
        with self.lock:
            self.conn.execute("""
                INSERT INTO stakes(address, bio_amount, tier, staked_at, slashed)
                VALUES (?,?,?,?,0)
                ON CONFLICT(address) DO UPDATE SET
                    bio_amount = excluded.bio_amount,
                    tier       = excluded.tier,
                    staked_at  = excluded.staked_at
            """, (address, bio_amount, tier, time.time()))
            self._commit()

    def update_stake_tier(self, address: str, tier: str):
        """Updates ONLY the tier label, leaving bio_amount, staked_at and the slashed accumulator untouched."""
        with self.lock:
            self.conn.execute(
                "UPDATE stakes SET tier=? WHERE address=?",
                (tier, address)
            )
            self._commit()

    def slash_stake(self, address: str, amount: float):
        with self.lock:
            self.conn.execute(
                "UPDATE stakes SET bio_amount=MAX(0,bio_amount-?), slashed=slashed+? WHERE address=?",
                (amount, amount, address)
            )
            self._commit()

    def create_swap_lock(self, lock_id, sender, receiver, amount, hash_lock, created_t, timeout):
        with self.lock:
            self.conn.execute(
                "INSERT INTO swap_locks (id,sender,receiver,amount,hash_lock,created_t,timeout) VALUES (?,?,?,?,?,?,?)",
                (lock_id, sender, receiver, int(amount), hash_lock, created_t, int(timeout)))
            self.conn.commit()

    def get_swap_lock(self, lock_id):
        with self.lock:
            return self.conn.execute("SELECT * FROM swap_locks WHERE id=?", (lock_id,)).fetchone()

    def swap_hash_exists(self, hash_lock) -> bool:
        with self.lock:
            return self.conn.execute("SELECT 1 FROM swap_locks WHERE hash_lock=?", (hash_lock,)).fetchone() is not None

    def count_active_locks(self, sender) -> int:
        with self.lock:
            return int(self.conn.execute(
                "SELECT COUNT(*) c FROM swap_locks WHERE sender=? AND state='LOCKED'", (sender,)).fetchone()["c"])

    def settle_swap_lock(self, lock_id, new_state, preimage=""):
        """LOCKED -> CLAIMED/REFUNDED."""
        with self.lock:
            self.conn.execute("UPDATE swap_locks SET state=?, preimage=? WHERE id=?",
                              (new_state, preimage, lock_id))
            self.conn.commit()

    def locked_total(self) -> int:
        with self.lock:
            return int(self.conn.execute(
                "SELECT COALESCE(SUM(amount),0) s FROM swap_locks WHERE state='LOCKED'").fetchone()["s"])

    def staked_total(self) -> int:
        """fix: staked BIO is debited from the wallet on /stake but was never added back into /verify's supply sum -- a fourth bucket that the three-bucket invariant."""
        with self.lock:
            return int(self.conn.execute(
                "SELECT COALESCE(SUM(bio_amount),0) s FROM stakes").fetchone()["s"])

    def pending_unstakes_total(self) -> int:
        """A fifth invariant bucket: BIO in cooldown after UNSTAKE, before it's spendable again."""
        with self.lock:
            return int(self.conn.execute(
                "SELECT COALESCE(SUM(bio_amount),0) s FROM pending_unstakes WHERE claimed=0"
            ).fetchone()["s"])


    def note_node_candidate(self, url: str, reporter_url: str, now: float = None):
        """Record that reporter_url (a peer we already trust enough to have gossiped with) told us about this candidate url."""
        now = now if now is not None else time.time()
        with self.lock:
            existing = self.conn.execute(
                "SELECT 1 FROM node_candidates WHERE url=?", (url,)).fetchone()
            if existing:
                self.conn.execute(
                    "UPDATE node_candidates SET last_confirmed_at=? WHERE url=?", (now, url))
            else:
                self.conn.execute(
                    "INSERT INTO node_candidates (url, first_seen_at, last_confirmed_at) VALUES (?,?,?)",
                    (url, now, now))
            self.conn.execute(
                "INSERT OR IGNORE INTO candidate_reports (url, reporter_url, reported_at) VALUES (?,?,?)",
                (url, reporter_url, now))
            self._commit()

    def note_self_announcement(self, url: str, now: float = None):
        """self-announcement (Bitcoin/Ethereum-style: a new node tells an existing node it exists, matching addr/FINDNODE messages in those networks)."""
        now = now if now is not None else time.time()
        with self.lock:
            existing = self.conn.execute(
                "SELECT 1 FROM node_candidates WHERE url=?", (url,)).fetchone()
            if existing:
                self.conn.execute(
                    "UPDATE node_candidates SET last_confirmed_at=? WHERE url=?", (now, url))
            else:
                self.conn.execute(
                    "INSERT INTO node_candidates (url, first_seen_at, last_confirmed_at) VALUES (?,?,?)",
                    (url, now, now))
            self._commit()

    def list_node_candidates(self, min_confirmations: int = 0):
        """Candidates with at least min_confirmations DISTINCT reporters."""
        with self.lock:
            return self.conn.execute(
                """SELECT nc.url, nc.first_seen_at, nc.last_confirmed_at,
                          COUNT(DISTINCT cr.reporter_url) AS confirmations
                   FROM node_candidates nc
                   LEFT JOIN candidate_reports cr ON cr.url = nc.url
                   GROUP BY nc.url
                   HAVING confirmations >= ?
                   ORDER BY confirmations DESC""",
                (min_confirmations,)).fetchall()

    def prune_stale_candidates(self, max_age_days: int = 7):
        """Spec section 4.3: a candidate not reconfirmed by anyone in max_age_days is dropped -- prevents unbounded accumulation of dead or abandoned addresses in the."""
        cutoff = time.time() - max_age_days * 86400
        with self.lock:
            self.conn.execute("DELETE FROM candidate_reports WHERE url IN "
                              "(SELECT url FROM node_candidates WHERE last_confirmed_at < ?)", (cutoff,))
            self.conn.execute("DELETE FROM node_candidates WHERE last_confirmed_at < ?", (cutoff,))

    def load_promoted_peers(self) -> list:
        """every peer ever auto-promoted, in promotion order."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT url FROM promoted_peers ORDER BY promoted_at ASC").fetchall()
            return [r["url"] for r in rows]

    def save_promoted_peer(self, url: str, confirmations: int, now: float = None) -> bool:
        """Returns False if url was already promoted (idempotent -- the gossip loop runs continuously)."""
        now = now if now is not None else time.time()
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO promoted_peers (url, promoted_at, confirmations_at_promotion) VALUES (?,?,?)",
                (url, now, confirmations))
            promoted = cur.rowcount > 0
            if promoted:
                self.conn.execute("DELETE FROM candidate_reports WHERE url=?", (url,))
                self.conn.execute("DELETE FROM node_candidates WHERE url=?", (url,))
            self._commit()
            return promoted
            self._commit()

    def create_swap_offer(self, offer_id, sender, give_amount, want_asset, want_amount, ext_address, created_t, ttl):
        with self.lock:
            self.conn.execute(
                "INSERT INTO swap_offers (id,sender,give_amount,want_asset,want_amount,ext_address,created_t,ttl) VALUES (?,?,?,?,?,?,?,?)",
                (offer_id, sender, int(give_amount), want_asset, int(want_amount), ext_address, created_t, int(ttl)))
            self.conn.commit()

    def get_swap_offer(self, offer_id):
        with self.lock:
            return self.conn.execute("SELECT * FROM swap_offers WHERE id=?", (offer_id,)).fetchone()

    def cancel_swap_offer(self, offer_id):
        with self.lock:
            self.conn.execute("UPDATE swap_offers SET state='CANCELLED' WHERE id=?", (offer_id,))
            self.conn.commit()

    def active_swap_offers(self, chain_now: float):
        """ACTIVE and not yet expired by chain time."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM swap_offers WHERE state='ACTIVE'").fetchall()
            return [r for r in rows if r["created_t"] + r["ttl"] > chain_now]

    def create_pending_unstake(self, address: str, bio_amount: float, requested_at: float):
        with self.lock:
            self.conn.execute(
                "INSERT INTO pending_unstakes(address,bio_amount,requested_at,claimed) VALUES (?,?,?,0)",
                (address, bio_amount, requested_at)
            )
            self._commit()

    def get_unclaimed_unstakes(self):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM pending_unstakes WHERE claimed=0"
            ).fetchall()

    def get_pending_unstakes_for(self, address: str):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM pending_unstakes WHERE address=? AND claimed=0", (address,)
            ).fetchall()

    def mark_unstake_claimed(self, unstake_id: int):
        with self.lock:
            self.conn.execute(
                "UPDATE pending_unstakes SET claimed=1 WHERE id=?", (unstake_id,)
            )
            self._commit()

    def add_recognized_pair(self, exchange_name: str, pair_identifier: str, recognized_at: float, proposal_id: int):
        with self.lock:
            self.conn.execute(
                "INSERT INTO recognized_pairs(exchange_name,pair_identifier,recognized_at,proposal_id) VALUES (?,?,?,?)",
                (exchange_name, pair_identifier, recognized_at, proposal_id)
            )
            self._commit()

    def get_recognized_pairs(self):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM recognized_pairs ORDER BY recognized_at"
            ).fetchall()

    def add_developer_grant(self, address: str, project_name: str, project_description: str, amount: int, granted_at: float, proposal_id: int):
        with self.lock:
            self.conn.execute(
                "INSERT INTO developer_grants(address,project_name,project_description,amount,granted_at,proposal_id) VALUES (?,?,?,?,?,?)",
                (address, project_name, project_description, amount, granted_at, proposal_id)
            )
            self._commit()

    def get_developer_grants(self):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM developer_grants ORDER BY granted_at"
            ).fetchall()

    def get_all_stakes(self):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM stakes ORDER BY bio_amount DESC"
            ).fetchall()


    def create_proposal(self, title: str, description: str,
                        proposer: str, now: float, duration_days: int = 7,
                        param_key: str = "", param_value: str = ""):
        ends_at = now + duration_days * 86400
        apply_at = ends_at + GOVERNANCE_TIMELOCK
        with self.lock:
            self.conn.execute("""
                INSERT INTO proposals
                (title,description,proposer,created_at,ends_at,apply_at,param_key,param_value)
                VALUES (?,?,?,?,?,?,?,?)
            """, (title, description, proposer, now, ends_at, apply_at,
                  param_key, param_value))
            new_id = self.conn.execute(
                "SELECT last_insert_rowid() as id"
            ).fetchone()["id"]
            self._commit()
        return new_id

    def get_proposals(self, status: str = None):
        with self.lock:
            if status:
                return self.conn.execute(
                    "SELECT * FROM proposals WHERE status=? ORDER BY id DESC",
                    (status,)
                ).fetchall()
            return self.conn.execute(
                "SELECT * FROM proposals ORDER BY id DESC"
            ).fetchall()

    def get_open_proposals(self):
        with self.lock:
            """Proposals that are not yet finalized (for governance_loop)"""
            return self.conn.execute(
                "SELECT * FROM proposals WHERE status IN ('ACTIVE','APPROVED')"
            ).fetchall()

    def update_proposal_status(self, proposal_id: int, status: str):
        with self.lock:
            self.conn.execute(
                "UPDATE proposals SET status=? WHERE id=?",
                (status, proposal_id)
            )
            self._commit()

    def has_voted(self, proposal_id: int, voter: str) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT 1 FROM votes WHERE proposal_id=? AND voter=?",
                (proposal_id, voter)
            ).fetchone()
            return row is not None

    def cast_vote(self, proposal_id: int, voter: str,
                  vote: str, weight: float = 1.0) -> bool:
        """IMPORTANT: votes_for/votes_against is a SUM OF WEIGHTS, not a vote count."""
        try:
            with self.lock:
                self.conn.execute("""
                    INSERT INTO votes(proposal_id,voter,vote,weight,voted_at)
                    VALUES (?,?,?,?,?)
                """, (proposal_id, voter, vote, weight, time.time()))
                if vote == "FOR":
                    self.conn.execute(
                        "UPDATE proposals SET votes_for=votes_for+? WHERE id=?",
                        (weight, proposal_id)
                    )
                else:
                    self.conn.execute(
                        "UPDATE proposals SET votes_against=votes_against+? WHERE id=?",
                        (weight, proposal_id)
                    )
                self._commit()
            return True
        except Exception:
            return False

    def size_kb(self) -> float:
        return round(os.path.getsize(self.path) / 1024, 1) if os.path.exists(self.path) else 0.0

db = Database()

class Emission:
    MAX_SUPPLY        = 21_000_000
    GENESIS_TIERS = [
        {"count": 1_000,  "amount": 100 * SAT_PER_BIO},
        {"count": 5_000,  "amount": 20  * SAT_PER_BIO},
        {"count": 10_000, "amount": 10  * SAT_PER_BIO},
    ]
    GENESIS_MAX_COUNT = sum(t["count"] for t in GENESIS_TIERS)
    HALVING_EVERY     = 365 * 24 * 3600
    INITIAL_REWARD    = 10 * SAT_PER_BIO
    MIN_REWARD        = SAT_PER_BIO // 1000
    TRANSFER_FEE_BASE = SAT_PER_BIO // 100
    BURN_RATE_PPM     = 500
    BURN_RATE         = BURN_RATE_PPM / 1_000_000
    STAKE_FEE         = 1 * SAT_PER_BIO

    def __init__(self):
        self.pools = {
            "validators":      8_400_000 * SAT_PER_BIO,
            "ecosystem":       6_300_000 * SAT_PER_BIO,
            "reserve":         4_200_000 * SAT_PER_BIO,
            "team":            1_050_000 * SAT_PER_BIO,
            "genesis":           820_000 * SAT_PER_BIO,
            "listing_reserve":  230_000 * SAT_PER_BIO,
            "wallet_registration": 0,
            "developer_grants": 0,
        }
        self.minted          = 0
        self.burned          = 0
        self.total_destroyed = 0
        self.halvings        = 0
        self.genesis_granted = 0
        self.start_time      = time.time()
        self._lock           = threading.Lock()

    def block_reward(self, now: float) -> float:
        """Year 1: 10 BIO Year 2: 5 BIO Year 3: 2.5 BIO ...minimum 0.001 BIO `now` is the chain's own time (see Network.chain_time), not this server's wall clock -- so."""
        elapsed  = now - self.start_time
        halvings = int(elapsed // self.HALVING_EVERY)
        return max(self.INITIAL_REWARD // (2 ** halvings), self.MIN_REWARD)

    def check_halving(self, chain_len: int, now: float):
        """Halving based on chain time -- called for logging"""
        elapsed      = now - self.start_time
        new_halvings = int(elapsed // self.HALVING_EVERY)
        if new_halvings > self.halvings:
            self.halvings = new_halvings
            db.log("HALVING", f"Year {self.halvings+1}: reward={sat_to_bio(self.block_reward(now)):.4f} BIO")
            print(f"[HALVING] Year {self.halvings+1} -- reward {sat_to_bio(self.block_reward(now)):.4f} BIO")

    def _genesis_amount_for_index(self, index: int) -> int:
        """Returns the grant amount for the (0-based) Nth genesis grant ever issued"""
        cumulative = 0
        for tier in self.GENESIS_TIERS:
            cumulative += tier["count"]
            if index < cumulative:
                return tier["amount"]
        return 0

    def try_genesis_grant(self, address: str) -> int:
        if self.genesis_granted >= self.GENESIS_MAX_COUNT:
            return 0
        if self.pools["genesis"] <= 0:
            return 0
        amount = self._genesis_amount_for_index(self.genesis_granted)
        amount = min(amount, self.pools["genesis"])
        given  = db.try_give_genesis(address, amount)
        if given > 0:
            with self._lock:
                self.pools["genesis"]  -= given
                self.minted            += given
                self.genesis_granted   += 1
            print(f"[GENESIS] {address[:16]}... +{sat_to_bio(given):.1f} BIO #{self.genesis_granted}/{self.GENESIS_MAX_COUNT}")
        return given

    def mint_reward(self, node, chain_len: int, now: float) -> int:
        """Validator reward -- now actually applies the tier multiplier, not just displays it in the API."""
        self.check_halving(chain_len, now)
        if self.pools["validators"] <= 0:
            return 0
        base_full  = self.block_reward(now)
        if self.pools["validators"] < VALIDATORS_TAPER_FLOOR:
            base = base_full * self.pools["validators"] // VALIDATORS_TAPER_FLOOR
        else:
            base = base_full
        stake_row  = db.get_stake(node.address)
        tier       = stake_row["tier"] if stake_row else "NONE"
        mult       = STAKE_TIERS.get(tier, STAKE_TIERS["NONE"])["reward_mult"]
        desired    = (base * int(mult * 10)) // 10
        actual     = min(desired, self.pools["validators"])
        node.balance              += actual
        self.pools["validators"]  -= actual
        self.minted               += actual
        return actual

    FEE_BURN_PERCENT = 0

    def burn(self, amount: float):
        """fees now split two ways."""
        destroyed = (amount * self.FEE_BURN_PERCENT) // 100
        to_pool   = amount - destroyed
        self.burned          += amount
        self.total_destroyed += destroyed
        self.pools["validators"] += to_pool

    def crisis_payout(self, alive_nodes: list) -> float:
        if self.pools["reserve"] <= 0 or not alive_nodes:
            return 0
        per = min((50 * SAT_PER_BIO) // len(alive_nodes),
                  self.pools["reserve"] // len(alive_nodes))
        for n in alive_nodes:
            n.balance += per
            db.credit(n.address, per)
        total = per * len(alive_nodes)
        self.pools["reserve"] -= total
        self.minted               += total
        db.log("CRISIS_PAYOUT", f"{sat_to_bio(total):.2f} BIO across {len(alive_nodes)} nodes")
        return total

    def state(self) -> dict:
        return {
            "max_supply":        self.MAX_SUPPLY,
            "minted":            round(sat_to_bio(self.minted), 2),
            "burned":            round(sat_to_bio(self.burned), 4),
            "net_supply":        round(sat_to_bio(self.minted - self.burned), 2),
            "halvings":          self.halvings,
            "block_reward":      round(sat_to_bio(self.block_reward(time.time())), 4),
            "burn_rate":         f"{self.BURN_RATE_PPM/10000:.2f}%",
            "genesis_tiers":     [{"count": t["count"], "amount": sat_to_bio(t["amount"])}
                                  for t in self.GENESIS_TIERS],
            "genesis_current_amount": sat_to_bio(self._genesis_amount_for_index(self.genesis_granted)),
            "genesis_granted":   self.genesis_granted,
            "genesis_remaining": self.GENESIS_MAX_COUNT - self.genesis_granted,
            "pools":             {k: round(sat_to_bio(v), 2) for k, v in self.pools.items()},
        }

class Vesting:
    """5% of emission -- to the developer."""
    def __init__(self):
        db.init_vesting(TEAM_ADDRESS)
        row = db.get_vesting()
        self.start_time     = row["start_time"] if row else time.time()
        self.claimed_months = row["claimed_months"] if row else 0
        self.total_claimed  = int(row["total_claimed"]) if row else 0

    def check_and_pay(self, emission, stability: float, now: float) -> int:
        """Called after each block, using the chain's own time (not this server's wall clock -- see Network.chain_time)."""
        elapsed = now - self.start_time

        if elapsed < CLIFF_SECONDS:
            remaining = CLIFF_SECONDS - elapsed
            days = int(remaining / 86400)
            return 0

        months_after_cliff = int((elapsed - CLIFF_SECONDS) / MONTH_SECONDS)
        payable_months     = min(months_after_cliff, VESTING_MONTHS)
        unpaid_months      = payable_months - self.claimed_months

        if unpaid_months <= 0:
            return 0

        if stability < 0.15:
            db.log("VESTING_PAUSED",
                   f"Crisis S={stability:.3f} -- payout deferred")
            return 0

        if emission.pools["team"] <= 0:
            return 0

        amount = 0
        for m in range(self.claimed_months + 1, self.claimed_months + unpaid_months + 1):
            amount += FINAL_MONTH_PAYOUT if m == VESTING_MONTHS else MONTHLY_PAYOUT
        amount = min(amount, emission.pools["team"])
        db.ensure_wallet(TEAM_ADDRESS)
        db.credit(TEAM_ADDRESS, amount)
        emission.pools["team"] -= amount
        emission.minted         += amount
        self.claimed_months     += unpaid_months
        self.total_claimed      += amount
        db.save_vesting(self.claimed_months, self.total_claimed)
        db.log("VESTING_PAID",
               f"{TEAM_ADDRESS} +{sat_to_bio(amount):.2f} BIO month #{self.claimed_months}")
        print(f"[VESTING] +{sat_to_bio(amount):.2f} BIO -> {TEAM_ADDRESS} (month #{self.claimed_months}/{VESTING_MONTHS})")
        return amount

    def state(self, now: float = None) -> dict:
        now = now if now is not None else time.time()
        elapsed       = now - self.start_time
        cliff_passed  = elapsed >= CLIFF_SECONDS
        months_passed = max(0, int((elapsed - CLIFF_SECONDS) / MONTH_SECONDS)) if cliff_passed else 0
        return {
            "address":        TEAM_ADDRESS,
            "cliff_passed":   cliff_passed,
            "cliff_days_left":max(0, int((CLIFF_SECONDS - elapsed) / 86400)),
            "months_paid":    self.claimed_months,
            "months_total":   VESTING_MONTHS,
            "total_claimed":  round(sat_to_bio(self.total_claimed),  2),
            "remaining":      round(sat_to_bio(TEAM_POOL_TOTAL - self.total_claimed), 2),
            "monthly_payout": round(sat_to_bio(MONTHLY_PAYOUT), 2),
            "next_payout":    f"month {self.claimed_months + 1}" if cliff_passed and self.claimed_months < VESTING_MONTHS else "waiting for cliff",
        }

class Economy:
    ALPHA = 0.1; BETA = 2.0; GAMMA = 0.90; DELTA = 0.01
    CRISIS_THRESHOLD = 0.15

    def __init__(self):
        self.liquidity = 100.0
        self.risk      = 1.0

    def update(self, I: float, emission: Emission, alive: list):
        self.liquidity = min(max(self.liquidity - self.ALPHA*I + self.BETA, 10.0), 100.0)
        self.risk      = min(max(self.GAMMA*self.risk + self.DELTA*I, 0.1), 10.0)
        if self.stability() < self.CRISIS_THRESHOLD:
            payout = emission.crisis_payout(alive)
            if payout > 0:
                self.risk *= 0.8

    def stability(self) -> float:
        return 1.0 / (1.0 + self.risk)

    def state(self) -> dict:
        return {
            "liquidity":  round(self.liquidity, 2),
            "risk":       round(self.risk, 4),
            "stability":  round(self.stability(), 6),
        }

_balance_rollback_record = None
_balance_rollback_seen   = None

class Node:
    """A node is not registered manually."""
    def __init__(self, address: str, births: int = 1, now: float = None):
        self.address    = address
        self._balance         = db.get_balance(address)
        self.energy          = 10.0
        self.activity        = 0
        self.recent_activity = 0.0
        self.reputation      = 1.0
        self.age             = 0.0
        self.alive           = True
        self.births          = births
        self.born_at         = now if now is not None else time.time()
        self.died_at         = 0.0
        self.tx_count_at_death = 0
        self.role            = random.choice(ROLES)
        self.risk            = 0.0
        self.inherited_rep   = 0.0
        self.inherited_risk  = 0.0
        self.longevity_6mo_paid  = False
        self.longevity_12mo_paid = False
        self.last_monthly_payout = 0.0
        self.state_block = 0
        self.scheduled_death_block = None

    @property
    def balance(self):
        return self._balance

    @balance.setter
    def balance(self, value):
        global _balance_rollback_record, _balance_rollback_seen
        if _balance_rollback_record is not None and self.address not in _balance_rollback_seen:
            _balance_rollback_record.append((self, self._balance))
            _balance_rollback_seen.add(self.address)
        self._balance = value

    def materialize(self, now_block: int):
        """Bring self.energy and self.recent_activity up to date as of
        now_block -- EXACT, not approximate: constant per-block subtraction
        and constant-ratio multiplicative decay both telescope exactly
        across N blocks, whether applied N times in a row or once as a
        single closed-form step."""
        elapsed = now_block - self.state_block
        if elapsed > 0:
            self.energy = max(self.energy - ENERGY_DECAY_RATE * elapsed, 0.0)
            self.recent_activity = round(self.recent_activity * (RECENT_ACTIVITY_DECAY ** elapsed), 4)
            self.state_block = now_block

    def weight(self, liquidity: float, risk: float) -> float:
        """Weight is based on RECENT activity -- not accumulated."""
        if not self.alive:
            return 0.0
        base = self.recent_activity * 1.0 + self.reputation * 2.0 + self.energy * 3.0
        stake_row   = db.get_stake(self.address)
        tier        = stake_row["tier"] if stake_row else "NONE"
        weight_mult = STAKE_TIERS.get(tier, STAKE_TIERS["NONE"])["weight_mult"]
        return base * (liquidity / (1.0 + risk)) * weight_mult

    def on_impulse_sent(self, value: int):
        """Node sent an impulse -- grows according to its role."""
        value_bio = sat_to_bio(value)
        bonus = ROLE_BONUS.get(self.role, ROLE_BONUS["VALIDATOR"])
        self.energy          += ENERGY_PER_IMPULSE * bonus["energy"] + 0.1 * value_bio
        self.activity        += 1
        self.recent_activity  = min(self.recent_activity + 1.0, 100.0)
        self.reputation       = min(self.reputation + bonus["reputation"], 10.0)
        self.risk            += 0.01 * value_bio
        self.age             += 0.1

    def on_impulse_received(self, value: int):
        """Node received an impulse -- a small boost (BIO scale, see above)"""
        self.energy += 0.5 * sat_to_bio(value)

    def decay(self):
        """Called after every block -- energy decays"""
        self.energy = max(self.energy - ENERGY_DECAY_RATE, 0.0)

    def check_alive(self, now: float) -> bool:
        """Dies when energy is exhausted -- this is what "no longer useful to the system" means (no activity, no contribution)."""
        if self.energy <= ENERGY_DEATH:
            self.alive   = False
            self.died_at = now
            self.tx_count_at_death = db.get_tx_count(self.address)
            self._save_inheritance()
            db.log("NODE_DIED",
                   f"{self.address[:16]} died | rep={self.reputation:.2f} risk={self.risk:.2f} "
                   f"balance={self.balance:.2f} (held for one year)")
            print(f"[NODE] {self.address[:16]}... died | balance {self.balance:.2f} BIO held for one year")
        return self.alive

    def _save_inheritance(self):
        """Saves inheritance for a successor"""
        import json
        good = round(self.reputation * INHERITANCE_GOOD, 4)
        bad  = round(self.risk       * INHERITANCE_BAD,  4)
        db.log("INHERITANCE_DATA", json.dumps({
            "address":  self.address,
            "good_rep": good,
            "bad_risk": bad,
            "role":     self.role,
            "births":   self.births,
        }))

    def apply_inheritance(self, good_rep: float, bad_risk: float, parent_role: str):
        """Applies inheritance from an ancestor upon rebirth"""
        self.inherited_rep  = good_rep
        self.inherited_risk = bad_risk
        self.reputation     = 1.0 + good_rep
        self.risk           = bad_risk
        self.energy         = 10.0 + good_rep * 5
        seed = hashlib.sha256(f"{self.address}{self.births}".encode()).hexdigest()
        if int(seed, 16) % 100 < 30:
            self.role = parent_role
        db.log("INHERITANCE_APPLIED",
               f"{self.address[:16]} inherited rep+{good_rep:.2f} risk+{bad_risk:.2f} role={self.role}")
        print(f"[NODE] {self.address[:16]}... reborn with inheritance | "
              f"rep={self.reputation:.2f} risk={self.risk:.2f} role={self.role}")

    def to_dict(self, liquidity: float, risk: float) -> dict:
        stake_row  = db.get_stake(self.address)
        tier       = stake_row["tier"] if stake_row else "NONE"
        bio_staked = int(stake_row["bio_amount"]) if stake_row else 0
        tier_info  = STAKE_TIERS.get(tier, STAKE_TIERS["NONE"])

        days_alive = round((time.time() - self.born_at) / 86400, 1) if self.alive else 0.0
        return {
            "address":          self.address,
            "role":             self.role,
            "tier":             tier,
            "bio_staked":       round(sat_to_bio(bio_staked), 2),
            "tier_label":       tier_info["label"],
            "balance":          round(sat_to_bio(self.balance),      2),
            "energy":           round(self.energy,                  2),
            "activity":         self.activity,
            "recent_activity":  round(self.recent_activity,         2),
            "reputation":       round(self.reputation,              3),
            "risk":             round(self.risk,                    4),
            "inherited_rep":    round(self.inherited_rep,           3),
            "inherited_risk":   round(self.inherited_risk,          3),
            "weight":           round(self.weight(liquidity, risk),  4),
            "alive":            self.alive,
            "age":              round(self.age,                     1),
            "births":           self.births,
            "born_at":          round(self.born_at),
            "days_alive":       days_alive,
            "longevity_6mo_paid":  self.longevity_6mo_paid,
            "longevity_12mo_paid": self.longevity_12mo_paid,
        }

class Impulse:
    """Impulse energy = transaction value."""
    LAMBDA = 1.0

    def __init__(self, sender, receiver, value, index, phi_bio_snap, pubkey_hex="", signature_hex="", signed_timestamp=0.0, kind="TRANSFER", payload="", nonce=0):
        self.sender   = sender
        self.receiver = receiver
        self.value    = int(value)
        self.t        = time.time()
        self.phi_bio  = phi_bio_snap
        self.energy   = self.LAMBDA * sat_to_bio(value)
        self.kind     = kind
        self.payload  = payload
        self.pubkey_hex       = pubkey_hex
        self.signature_hex    = signature_hex
        self.signed_timestamp = signed_timestamp
        self.nonce = int(nonce)
        raw           = f"{kind}{sender}{receiver}{value}{self.t}{index}{payload}"
        self.id       = hashlib.sha256(raw.encode()).hexdigest()

class Block:
    """A block's legitimacy rests on two independently verifiable things -- not a validator signature, which would add nothing real: the sender's own signature on the."""
    def __init__(self, index, prev_hash, impulse, validator, reward=0):
        self.index     = index
        self.prev_hash = prev_hash
        self.impulse   = impulse
        self.validator = validator
        self.reward    = reward
        self.t         = impulse.t
        raw            = f"{index}{prev_hash}{impulse.id}{validator}{self.t}"
        self.hash      = hashlib.sha256(raw.encode()).hexdigest()

class _ImpulseStub:
    """Lightweight stub for restoring an impulse from the database"""
    def __init__(self, sender, receiver, value, energy,
                 phi_bio, imp_id, t, pubkey_hex="", signature_hex="", signed_timestamp=0.0, kind="TRANSFER", payload="", nonce=0):
        self.sender   = sender
        self.receiver = receiver
        self.value    = int(value)
        self.energy   = energy
        self.phi_bio  = phi_bio
        self.id       = imp_id
        self.t        = t
        self.pubkey_hex       = pubkey_hex
        self.signature_hex    = signature_hex
        self.signed_timestamp = signed_timestamp
        self.kind             = kind
        self.payload           = payload
        self.nonce             = int(nonce)

class _BlockStub:
    """Lightweight stub for restoring a block from the database"""
    def __init__(self, index, hash_, prev_hash, validator,
                 reward, t, impulse):
        self.index     = index
        self.hash      = hash_
        self.prev_hash = prev_hash
        self.validator = validator
        self.reward    = int(reward)
        self.t         = t
        self.impulse   = impulse

def _impulse_from_row(row) -> "_ImpulseStub":
    """Shared row -> Impulse construction, used both when eagerly loading
    a hot-window block (restore()) and when lazily loading a cold one
    (_LazyBlock.impulse) -- one source of truth for the mapping."""
    return _ImpulseStub(
        row["imp_sender"],   row["imp_receiver"],
        row["imp_value"],    row["imp_energy"],
        row["imp_phi_bio"],  row["imp_id"],
        row["timestamp"],
        row["imp_pubkey"],   row["imp_signature"],
        row["imp_signed_ts"], row["imp_kind"], row["imp_payload"],
        row["imp_nonce"] or 0,
    )

class _LazyBlock:
    """PERF FIX (v5.43): lightweight representation for the 'cold' part
    of the chain -- everything outside the recent hot window (see
    CHAIN_HOT_WINDOW). Holds only the cheap scalar fields every
    consensus-relevant operation actually needs (hash, prev_hash, index,
    validator, reward, t). The dominant memory cost per block is the
    impulse's signature+pubkey hex strings (~7.5KB, measured directly
    this session) -- .impulse is a lazy property that only pays for
    those the first time something ACTUALLY needs this specific old
    block's full impulse data (fork resolution touching deep history, a
    manual /chain query), not on every restart or ever for blocks nobody
    asks about again. Caches after first access, same object identity
    guarantees as a normal attribute from then on."""
    __slots__ = ("index", "hash", "prev_hash", "validator", "reward", "t", "_impulse_cache")

    def __init__(self, index, hash_, prev_hash, validator, reward, t):
        self.index     = index
        self.hash      = hash_
        self.prev_hash = prev_hash
        self.validator = validator
        self.reward    = int(reward)
        self.t         = t
        self._impulse_cache = None

    @property
    def impulse(self):
        if self._impulse_cache is None:
            row = db.load_block_by_index(self.index)
            if row is None:
                raise RuntimeError(f"cold block {self.index} missing from database -- data corruption")
            self._impulse_cache = _impulse_from_row(row)
        return self._impulse_cache


def signed_message(kind: str, *, sender: str = "", receiver: str = "",
                   value: int = 0, signed_ts: float = 0.0,
                   nonce: int = 0, payload: str = ""):
    """The exact byte string the sender signs for each kind of action."""
    n = int(nonce)
    if kind == "TRANSFER":
        return f"TX|{sender}|{receiver}|{sat_to_str8(value)}|{signed_ts:.6f}|{n}"
    if kind == "STAKE":
        return f"STAKE|{sender}|{sat_to_str8(value)}|{signed_ts:.6f}|{n}"
    if kind == "REGISTER":
        return f"REGISTER|{sender}|{signed_ts:.6f}|{n}"
    if kind == "UNSTAKE":
        return f"UNSTAKE|{sender}|{sat_to_str8(value)}|{signed_ts:.6f}|{n}"
    if kind == "PROPOSAL":
        try:
            d = json.loads(payload or "{}")
        except Exception:
            return None
        return (f"PROPOSAL|{sender}|{d.get('title','')}|"
                f"{d.get('param_key','')}|{d.get('param_value','')}|"
                f"{signed_ts:.6f}|{n}")
    if kind == "VOTE":
        try:
            d = json.loads(payload or "{}")
        except Exception:
            return None
        return (f"VOTE|{d.get('proposal_id','')}|{sender}|"
                f"{d.get('vote','')}|{signed_ts:.6f}|{n}")
    if kind == "SWAP_OFFER":
        try:
            d = json.loads(payload or "{}")
        except Exception:
            return None
        if "cancel_offer_id" in d:
            return f"SWAP_OFFER|{sender}|CANCEL|{d.get('cancel_offer_id','')}|{signed_ts:.6f}|{n}"
        return (f"SWAP_OFFER|{sender}|{sat_to_str8(int(d.get('give_bio',0)))}|"
                f"{d.get('want_asset','')}|{int(d.get('want_amount',0))}|"
                f"{d.get('ext_address','')}|{int(d.get('ttl',0))}|{signed_ts:.6f}|{n}")
    if kind == "SWAP_LOCK":
        try:
            d = json.loads(payload or "{}")
        except Exception:
            return None
        return (f"SWAP_LOCK|{sender}|{receiver}|{sat_to_str8(value)}|"
                f"{d.get('hash_lock','')}|{int(d.get('timeout',0))}|{signed_ts:.6f}|{n}")
    if kind == "SWAP_CLAIM":
        try:
            d = json.loads(payload or "{}")
        except Exception:
            return None
        return f"SWAP_CLAIM|{sender}|{d.get('lock_id','')}|{d.get('preimage','')}|{signed_ts:.6f}|{n}"
    if kind == "SWAP_REFUND":
        try:
            d = json.loads(payload or "{}")
        except Exception:
            return None
        return f"SWAP_REFUND|{sender}|{d.get('lock_id','')}|{signed_ts:.6f}|{n}"
    return None


def swap_feasibility(kind, sender, receiver, value, payload, chain_now):
    """ONE set of swap validation rules for both the local send() path and the peer block path."""
    try:
        d = json.loads(payload or "{}")
    except Exception:
        raise _Reject(f"invalid {kind} payload -- not JSON")

    if kind == "SWAP_OFFER":
        if "cancel_offer_id" in d:
            off = db.get_swap_offer(str(d["cancel_offer_id"]))
            if not off:
                raise _Reject("offer to cancel does not exist")
            if off["sender"] != sender:
                raise _Reject("only the offer's creator may cancel it")
            if off["state"] != "ACTIVE":
                raise _Reject("offer is not active")
            return d
        give = int(d.get("give_bio", 0))
        if give < SWAP_MIN_LOCK:
            raise _Reject(f"offer below minimum ({sat_to_bio(SWAP_MIN_LOCK):.0f} BIO)")
        asset = str(d.get("want_asset", "")).strip()
        if not asset or len(asset) > SWAP_ASSET_MAX_LEN:
            raise _Reject(f"want_asset must be non-empty and at most {SWAP_ASSET_MAX_LEN} characters")
        if int(d.get("want_amount", 0)) <= 0:
            raise _Reject("want_amount must be positive")
        if not d.get("ext_address") or len(str(d["ext_address"])) > 128:
            raise _Reject("ext_address missing or too long")
        ttl = int(d.get("ttl", 0))
        if not (SWAP_OFFER_TTL_MIN <= ttl <= SWAP_OFFER_TTL_MAX):
            raise _Reject(f"offer ttl out of range ({SWAP_OFFER_TTL_MIN}..{SWAP_OFFER_TTL_MAX} s)")
        fee = transfer_fee(give)
        if not db.debit(sender, fee):
            raise _Reject("insufficient BIO to pay the offer fee")
        return d

    if kind == "SWAP_LOCK":
        h = str(d.get("hash_lock", "")).lower()
        if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
            raise _Reject("hash_lock must be 64 hex characters (SHA-256)")
        if db.swap_hash_exists(h):
            raise _Reject("hash_lock already used -- preimage reuse is forbidden")
        if value < SWAP_MIN_LOCK:
            raise _Reject(f"lock below minimum ({sat_to_bio(SWAP_MIN_LOCK):.0f} BIO)")
        if receiver == sender:
            raise _Reject("lock receiver must differ from sender")
        if not receiver.startswith("BIO1"):
            raise _Reject("invalid lock receiver address")
        t_out = int(d.get("timeout", 0))
        if not (SWAP_LOCK_TIMEOUT_MIN <= t_out <= SWAP_LOCK_TIMEOUT_MAX):
            raise _Reject(f"lock timeout out of range ({SWAP_LOCK_TIMEOUT_MIN}..{SWAP_LOCK_TIMEOUT_MAX} s)")
        if db.count_active_locks(sender) >= SWAP_MAX_ACTIVE_LOCKS:
            raise _Reject(f"too many active locks (max {SWAP_MAX_ACTIVE_LOCKS})")
        fee = transfer_fee(value)
        if not db.debit(sender, value + fee):
            raise _Reject("insufficient BIO for lock + fee")
        return d

    if kind == "SWAP_CLAIM":
        lock = db.get_swap_lock(str(d.get("lock_id", "")))
        if not lock:
            raise _Reject("lock does not exist")
        if lock["state"] != "LOCKED":
            raise _Reject(f"lock is not claimable (state: {lock['state']})")
        if lock["receiver"] != sender:
            raise _Reject("only the designated receiver may claim this lock")
        pre = str(d.get("preimage", "")).lower()
        if len(pre) != 64 or any(c not in "0123456789abcdef" for c in pre):
            raise _Reject("preimage must be exactly 64 hex characters (32 bytes)")
        if hashlib.sha256(bytes.fromhex(pre)).hexdigest() != lock["hash_lock"]:
            raise _Reject("preimage does not match the hash lock")
        if chain_now >= lock["created_t"] + lock["timeout"]:
            raise _Reject("lock has expired -- only REFUND is possible now")
        return d

    if kind == "SWAP_REFUND":
        lock = db.get_swap_lock(str(d.get("lock_id", "")))
        if not lock:
            raise _Reject("lock does not exist")
        if lock["state"] != "LOCKED":
            raise _Reject(f"lock is not refundable (state: {lock['state']})")
        if lock["sender"] != sender:
            raise _Reject("only the lock's creator may refund it")
        if chain_now < lock["created_t"] + lock["timeout"]:
            left = int(lock["created_t"] + lock["timeout"] - chain_now)
            raise _Reject(f"lock has not expired yet ({left} s left)")
        return d

    raise _Reject(f"unknown swap kind: {kind}")


class _Reject(Exception):
    """Internal: a peer block failed validation."""
    pass


class Network:
    THETA_S = 0.15
    THETA_W = 5.0
    THETA_I = 80.0

    def __init__(self):
        self.nodes    = {}
        self.chain    = []
        self.mempool  = []
        self.eco      = Economy()
        self.emission = Emission()
        self.vesting  = Vesting()
        self._alive_sorted_cache = []
        self._alive_cache_dirty  = True
        self._death_schedule = []
        self._alive_energy_sum = 0.0
        self._verified_up_to_index = -1

    def _invalidate_alive_cache(self):
        self._alive_cache_dirty = True

    def _get_alive_sorted(self):
        """Returns the sorted list of alive node addresses -- rebuilds
        from scratch only if the alive set changed since the last call,
        otherwise returns the cached list in O(1)."""
        if self._alive_cache_dirty:
            self._alive_sorted_cache = sorted(
                addr for addr, n in self.nodes.items() if n.alive)
            self._alive_cache_dirty = False
        return self._alive_sorted_cache

    def materialize_node(self, node, now_block: int):
        """Bring a single node's energy/recent_activity up to date as of
        now_block. Cheap (O(1)) -- call before any consensus-relevant read
        of node.energy or node.recent_activity for a SPECIFIC node (e.g.
        the selected validator's weight() check). Never loop this over
        every alive node -- that's exactly the O(n)-per-block cost this
        whole mechanism replaces."""
        node.materialize(now_block)

    def _schedule_death(self, node, now_block: int):
        """(Re)schedules when `node` will die, given its CURRENT (already
        materialized) energy. Must be called any time a node's energy
        changes -- birth, rebirth, or any impulse that adds energy. The
        old heap entry (if any) is left in place and becomes stale; stale
        entries are detected and discarded cheaply in _process_deaths via
        the scheduled_death_block comparison, not removed eagerly (heapq
        has no cheap arbitrary-entry removal, and doesn't need one here)."""
        if node.energy <= ENERGY_DEATH:
            death_block = now_block
        else:
            blocks_needed = math.ceil((node.energy - ENERGY_DEATH) / ENERGY_DECAY_RATE)
            death_block = now_block + blocks_needed
        node.scheduled_death_block = death_block
        heapq.heappush(self._death_schedule, (death_block, node.address))

    def _process_deaths(self, now_block: int, now_time: float):
        """Replaces the old _decay_all's O(n)-per-block sweep. Pops only
        the (typically zero or very few) entries actually due at
        now_block -- everyone else's decay is accounted for lazily,
        exactly, whenever they're next touched or checked, never eagerly
        recomputed here."""
        while self._death_schedule and self._death_schedule[0][0] <= now_block:
            death_block, address = heapq.heappop(self._death_schedule)
            node = self.nodes.get(address)
            if node is None or not node.alive:
                continue
            if node.scheduled_death_block != death_block:
                continue
            node.materialize(now_block)
            if node.energy > ENERGY_DEATH:
                self._schedule_death(node, now_block)
                continue
            node.alive   = False
            self._alive_energy_sum -= node.energy
            node.died_at = now_time
            node.tx_count_at_death = db.get_tx_count(node.address)
            node._save_inheritance()
            db.log("NODE_DIED",
                   f"{node.address[:16]} died | rep={node.reputation:.2f} risk={node.risk:.2f} "
                   f"balance={node.balance:.2f} (held for one year)")
            print(f"[NODE] {node.address[:16]}... died | balance {node.balance:.2f} BIO held for one year")
            db.save_node(node)
            self._invalidate_alive_cache()

    def chain_time(self) -> float:
        """The network's own notion of "now" -- the latest block's embedded timestamp, not this server's wall clock."""
        return self.chain[-1].t if self.chain else self.emission.start_time

    def nodes_snapshot(self):
        """A thread-safe COPY of all nodes (alive and dead) -- safe to iterate freely afterwards without holding any lock."""
        with _chain_lock:
            return list(self.nodes.values())

    def phi_bio(self) -> float:
        alive_count = len(self._get_alive_sorted())
        if alive_count == 0:
            return 1.0
        biofield = self._alive_energy_sum * self.eco.stability()
        return biofield / 500.0

    def _try_emerge(self, address: str, now: float):
        """Checks whether there is enough activity to birth/revive a node."""
        if address in self.nodes and self.nodes[address].alive:
            return

        tx_count = db.get_tx_count(address)

        if address not in self.nodes and tx_count >= EMERGE_THRESHOLD:
            wallet_row = db.get_wallet(address)
            first_seen = float(wallet_row["first_seen"]) if wallet_row else now
            if now - first_seen >= MIN_EMERGENCE_SPAN_SECONDS:
                self._emerge(address, now, births=1)

        elif address in self.nodes and not self.nodes[address].alive:
            node = self.nodes[address]
            impulses_since_death = tx_count - node.tx_count_at_death
            if impulses_since_death >= REBIRTH_THRESHOLD:
                import json
                inheritance = self._load_inheritance(address)
                node.alive   = True
                node.births += 1
                node.born_at = now
                self._invalidate_alive_cache()
                node.recent_activity = 0.0
                node.longevity_6mo_paid  = False
                node.longevity_12mo_paid = False
                node.last_monthly_payout = 0.0
                if inheritance:
                    node.apply_inheritance(
                        inheritance["good_rep"],
                        inheritance["bad_risk"],
                        inheritance["role"],
                    )
                else:
                    node.energy = 15.0
                node.state_block = len(self.chain)
                self._schedule_death(node, node.state_block)
                self._alive_energy_sum += node.energy
                db.save_node(node)
                db.log("NODE_REBORN", f"{address[:16]} reborn x{node.births}")
                print(f"[NODE] {address[:16]}... reborn! (birth #{node.births})")

    def _load_inheritance(self, address: str) -> dict:
        """Reads a node's most recent inheritance from events"""
        import json
        rows = db.conn.execute(
            "SELECT message FROM events WHERE type='INHERITANCE_DATA' "
            "AND message LIKE ? ORDER BY id DESC LIMIT 1",
            (f'%"address": "{address}"%',)
        ).fetchone()
        if rows:
            try:
                return json.loads(rows["message"])
            except Exception:
                return None
        return None

    def _emerge(self, address: str, now: float, births: int = 1):
        """Birth of a new node from an address"""
        tx_count = db.get_tx_count(address)
        node     = Node(address, births, now)
        node.activity        = tx_count
        node.recent_activity = float(tx_count)
        node.age             = round(tx_count * 0.1, 1)
        node.energy          = 10.0 + tx_count * ENERGY_PER_IMPULSE
        node.state_block = len(self.chain)
        self.nodes[address] = node
        self._schedule_death(node, node.state_block)
        self._alive_energy_sum += node.energy
        self._invalidate_alive_cache()
        self.emission.try_genesis_grant(address)
        node.balance = db.get_balance(address)
        db.save_node(node)
        db.log("NODE_EMERGED", f"{address[:16]} emerged from {tx_count} impulses")
        print(f"[NODE] * {address[:16]}... EMERGED (after {tx_count} impulses | energy={node.energy:.1f} activity={node.activity})")

    def _select_validator(self, impulse):
        """Deterministic, verifiable selection -- replaces random.choice so that every peer, given the same chain state and the same impulse, computes the SAME validator."""
        alive_sorted = self._get_alive_sorted()
        if not alive_sorted:
            return None, None
        prev_hash = self.chain[-1].hash if self.chain else "0" * 64
        seed_input = f"{prev_hash}{impulse.id}".encode()
        seed_int = int(hashlib.sha256(seed_input).hexdigest(), 16)
        index = seed_int % len(alive_sorted)
        chosen_address = alive_sorted[index]
        chosen = self.nodes[chosen_address]
        return chosen.address, chosen

    @staticmethod
    def verify_validator_selection(prev_hash: str, impulse_id: str, alive_addresses: list, claimed_validator: str) -> bool:
        """Lets a peer verify that a block's claimed validator was legitimately selected, without trusting whoever sent the block."""
        if claimed_validator not in alive_addresses:
            return False
        alive_sorted = sorted(alive_addresses)
        seed_input = f"{prev_hash}{impulse_id}".encode()
        seed_int = int(hashlib.sha256(seed_input).hexdigest(), 16)
        index = seed_int % len(alive_sorted)
        return alive_sorted[index] == claimed_validator

    @staticmethod
    def verify_impulse_signature(impulse) -> bool:
        """Independently re-verifies that an impulse's embedded signature really authorizes the transfer it describes -- using only data carried in the impulse itself."""
        pubkey_hex    = getattr(impulse, "pubkey_hex", "")
        signature_hex = getattr(impulse, "signature_hex", "")
        if not pubkey_hex or not signature_hex:
            return False
        try:
            pubkey = bytes.fromhex(pubkey_hex)
        except Exception:
            return False
        if pq.address(pubkey) != impulse.sender:
            return False
        signed_ts = getattr(impulse, "signed_timestamp", 0.0)
        nonce     = getattr(impulse, "nonce", 0)
        kind      = getattr(impulse, "kind", "TRANSFER")
        message = signed_message(
            kind, sender=impulse.sender, receiver=impulse.receiver,
            value=impulse.value, signed_ts=signed_ts, nonce=nonce,
            payload=getattr(impulse, "payload", "") or "",
        )
        if message is None:
            return False
        return pq.verify(pubkey, message, signature_hex)

    def _can_finalize(self, validator, impulse) -> bool:
        if not validator:
            return False
        validator.materialize(len(self.chain))
        S = self.eco.stability()
        W = validator.weight(self.eco.liquidity, self.eco.risk)
        return S > self.THETA_S and W > self.THETA_W and impulse.energy < self.THETA_I

    def send(self, sender: str, receiver: str, value: float, pubkey_hex: str = "", signature_hex: str = "", signed_timestamp: float = 0.0, kind: str = "TRANSFER", payload: str = "", nonce: int = 0):
        """Submits an impulse -- a transfer, a stake/unstake request, a governance proposal, or a vote."""
        with _chain_lock:
            snap = self._snapshot_inmem()
            block, reason = None, ""
            try:
                with db.transaction():
                    db.ensure_wallet(sender)

                    if len(self.mempool) >= MEMPOOL_MAX:
                        raise _Reject(f"mempool is full ({MEMPOOL_MAX}) -- try again shortly")
                    if payload and len(payload) > PAYLOAD_MAX_CHARS:
                        raise _Reject(f"payload too large (max {PAYLOAD_MAX_CHARS} chars)")
                    if not db.use_nonce(sender, int(nonce)):
                        raise _Reject(f"bad nonce: must be > {db.peek_nonce(sender)}")
                    if signature_hex and not db.use_signature_once(signature_hex, sender, time.time()):
                        raise _Reject("signature already used (replay)")

                    if kind == "TRANSFER":
                        fee = transfer_fee(value)
                        if value <= fee:
                            raise _Reject(f"transfer too small to cover the network fee ({sat_to_bio(fee):.4f} BIO)")
                        db.ensure_wallet(receiver)
                        if not db.debit(sender, value):
                            raise _Reject(f"insufficient BIO: have {sat_to_bio(db.get_balance(sender)):.4f}")
                    elif kind == "STAKE":
                        total_debit = value + Emission.STAKE_FEE
                        if not db.debit(sender, total_debit):
                            raise _Reject(f"insufficient BIO: have {sat_to_bio(db.get_balance(sender)):.4f}, need {sat_to_bio(total_debit):.4f} ({sat_to_bio(value):.4f} stake + {sat_to_bio(Emission.STAKE_FEE):.4f} fee)")
                    elif kind == "REGISTER":
                        wallet_row = db.get_wallet(sender)
                        if wallet_row and int(wallet_row["registration_got"]) == 1:
                            raise _Reject("this address already claimed its registration grant")
                        if db.registration_granted_count() >= WALLET_REGISTRATION_MAX_COUNT:
                            raise _Reject(f"wallet registration grant exhausted (first {WALLET_REGISTRATION_MAX_COUNT} only)")
                        if self.emission.pools.get("wallet_registration", 0) < WALLET_REGISTRATION_GRANT:
                            raise _Reject("wallet_registration pool is empty")
                    elif kind == "CLAIM_SERVER_REWARD":
                        raise _Reject("CLAIM_SERVER_REWARD is no longer supported -- "
                                       "use a server_reward governance PROPOSAL instead")
                    elif kind == "UNSTAKE":
                        existing = db.get_stake(sender)
                        staked   = int(existing["bio_amount"]) if existing else 0
                        if value > staked:
                            raise _Reject(f"cannot unstake more than is staked: have {sat_to_bio(staked):.2f}, requested {sat_to_bio(value):.2f}")
                    elif kind == "PROPOSAL":
                        if sender not in self.nodes:
                            raise _Reject("only network nodes may create proposals")
                        try:
                            data = json.loads(payload)
                        except Exception:
                            raise _Reject("invalid proposal payload")
                        if "title" not in data or "param_key" not in data or "param_value" not in data:
                            raise _Reject("proposal payload missing required fields")
                    elif kind == "VOTE":
                        if sender not in self.nodes or not self.nodes[sender].alive:
                            raise _Reject("only live network nodes may vote")
                        try:
                            data = json.loads(payload)
                        except Exception:
                            raise _Reject("invalid vote payload")
                        if data.get("vote") not in ("FOR", "AGAINST"):
                            raise _Reject("vote must be FOR or AGAINST")
                        if db.has_voted(data.get("proposal_id"), sender):
                            raise _Reject("already voted on this proposal")
                    elif kind in ("SWAP_OFFER", "SWAP_LOCK", "SWAP_CLAIM", "SWAP_REFUND"):
                        swap_feasibility(kind, sender, receiver, value, payload, time.time())
                    else:
                        raise _Reject(f"unknown action kind: {kind}")

                    phi_bio_snap = self.phi_bio()
                    imp = Impulse(sender, receiver, value, len(self.chain), phi_bio_snap, pubkey_hex, signature_hex, signed_timestamp, kind, payload, nonce)
                    self.mempool.append(imp)

                    block, reason = self._mine()

                    if block:
                        self._after_block(block, sender, receiver, value)
            except _Reject as e:
                self._restore_inmem(snap, self._end_balance_recording())
                return None, str(e)
            except Exception as e:
                self._restore_inmem(snap, self._end_balance_recording())
                return None, f"internal error while sending: {e}"
            finally:
                self._end_balance_recording()

        if block and block.index > 0 and block.index % WAL_CHECKPOINT_EVERY == 0:
            try:
                db.wal_checkpoint()
            except Exception as e:
                db.log("WAL_CHECKPOINT_ERROR", f"block {block.index}: {e}")
                print(f"[WAL] checkpoint failed at block {block.index}: {e}")

        return block, reason

    def _after_block(self, block, sender: str, receiver: str, value: float):
        """Everything that happens after ANY block is appended -- whether it was just created locally (send/_mine) or received and validated from a peer (see /peer/block)."""
        now_block = len(self.chain)
        self._try_emerge(sender, block.t)
        if receiver != sender:
            self._try_emerge(receiver, block.t)

        if sender in self.nodes and self.nodes[sender].alive:
            _e_before = self.nodes[sender].energy
            self.nodes[sender].materialize(now_block)
            self.nodes[sender].on_impulse_sent(value)
            self._alive_energy_sum += self.nodes[sender].energy - _e_before
            self._schedule_death(self.nodes[sender], now_block)
            db.save_node(self.nodes[sender])
        if receiver != sender and receiver in self.nodes and self.nodes[receiver].alive:
            _e_before = self.nodes[receiver].energy
            self.nodes[receiver].materialize(now_block)
            self.nodes[receiver].on_impulse_received(value)
            self._alive_energy_sum += self.nodes[receiver].energy - _e_before
            self._schedule_death(self.nodes[receiver], now_block)
            db.save_node(self.nodes[receiver])

        self._process_deaths(now_block, block.t)

        try:
            _governance_tick(block.t)
        except Exception as e:
            print(f"[GOV] tick error: {e}")
        try:
            _longevity_tick(block.t)
        except Exception as e:
            print(f"[LONGEVITY] tick error: {e}")
        try:
            self.vesting.check_and_pay(self.emission, self.eco.stability(), block.t)
        except Exception as e:
            print(f"[VESTING] tick error: {e}")
        try:
            _unstake_tick(block.t)
        except Exception as e:
            print(f"[UNSTAKE] tick error: {e}")

    @staticmethod
    def block_to_peer_dict(b) -> dict:
        """Same shape /peer/chain serializes -- shared so fork resolution and the endpoint can never silently drift apart."""
        return {
            "index": b.index, "hash": b.hash, "prev_hash": b.prev_hash,
            "validator": b.validator, "reward": b.reward, "timestamp": b.t,
            "imp_id": b.impulse.id, "imp_sender": b.impulse.sender,
            "imp_receiver": b.impulse.receiver, "imp_value": b.impulse.value,
            "imp_energy": b.impulse.energy, "imp_phi_bio": b.impulse.phi_bio,
            "imp_pubkey": getattr(b.impulse, "pubkey_hex", ""),
            "imp_signature": getattr(b.impulse, "signature_hex", ""),
            "imp_signed_ts": getattr(b.impulse, "signed_timestamp", 0.0),
            "imp_kind": getattr(b.impulse, "kind", "TRANSFER"),
            "imp_payload": getattr(b.impulse, "payload", ""),
            "imp_nonce": getattr(b.impulse, "nonce", 0),
        }

    def apply_peer_block(self, block_data: dict):
        """Validates and applies a block received from a peer."""
        with _chain_lock:
            return self._apply_peer_block_locked(block_data)

    def _expected_reward(self, validator: str, timestamp: float) -> float:
        """Predicts exactly what Emission.mint_reward() would hand out for this validator at this chain-time, without mutating any state."""
        if validator == "NETWORK" or validator not in self.nodes:
            return 0.0
        if self.emission.pools["validators"] <= 0:
            return 0.0
        base      = self.emission.block_reward(timestamp)
        stake_row = db.get_stake(validator)
        tier      = stake_row["tier"] if stake_row else "NONE"
        mult      = STAKE_TIERS.get(tier, STAKE_TIERS["NONE"])["reward_mult"]
        desired   = base * mult
        return min(desired, self.emission.pools["validators"])

    def _begin_balance_recording(self):
        global _balance_rollback_record, _balance_rollback_seen
        _balance_rollback_record = []
        _balance_rollback_seen = set()

    def _end_balance_recording(self):
        """Always safe to call, including a second time as a no-op safety
        net (see the `finally` blocks at both call sites) -- returns the
        recorded (node, old_balance) list exactly once, then clears the
        global state so the NEXT transaction starts clean regardless of
        whether this one succeeded or failed."""
        global _balance_rollback_record, _balance_rollback_seen
        if _balance_rollback_record is None:
            return []
        record = _balance_rollback_record
        _balance_rollback_record = None
        _balance_rollback_seen   = None
        return record

    def _snapshot_inmem(self) -> dict:
        """Captures the in-memory state that db.transaction()'s rollback
        does not cover, and starts balance-change recording (see
        _begin_balance_recording / PERF FIX note above class Node) --
        replaces copying every alive node's balance up front with
        recording only whichever nodes this specific transaction actually
        touches, wherever in the codebase that happens."""
        self._begin_balance_recording()
        return {
            "chain_len":     len(self.chain),
            "em_pools":      dict(self.emission.pools),
            "em_minted":     self.emission.minted,
            "em_burned":     self.emission.burned,
            "em_halvings":   self.emission.halvings,
            "em_start_time": self.emission.start_time,
            "eco_state":     dict(self.eco.__dict__),
        }

    def _restore_inmem(self, snap: dict, balance_record: list = None):
        """Undoes whatever _snapshot_inmem captured, plus every recorded
        balance change since -- the in-memory twin of a DB transaction
        rollback. Pass the list from _end_balance_recording()."""
        del self.chain[snap["chain_len"]:]
        self._verified_up_to_index = min(self._verified_up_to_index, len(self.chain) - 1)
        self.emission.pools      = snap["em_pools"]
        self.emission.minted     = snap["em_minted"]
        self.emission.burned     = snap["em_burned"]
        self.emission.halvings   = snap["em_halvings"]
        self.emission.start_time = snap["em_start_time"]
        self.eco.__dict__.update(snap["eco_state"])
        for node, old_balance in (balance_record or []):
            node._balance = old_balance

    def _apply_peer_block_locked(self, block_data: dict):
        try:
            with db.transaction():
                expected_prev = self.chain[-1].hash if self.chain else "0" * 64
                if block_data.get("prev_hash") != expected_prev:
                    raise _Reject("does not extend our current chain tip")

                sender    = block_data.get("imp_sender", "")
                receiver  = block_data.get("imp_receiver", "")
                value     = int(block_data.get("imp_value", 0))
                timestamp = float(block_data.get("timestamp", 0))
                index     = int(block_data.get("index", -1))
                kind      = block_data.get("imp_kind", "TRANSFER") or "TRANSFER"
                payload   = block_data.get("imp_payload", "") or ""
                if payload and len(payload) > PAYLOAD_MAX_CHARS:
                    raise _Reject(f"payload too large (max {PAYLOAD_MAX_CHARS} chars)")
                if index != len(self.chain):
                    raise _Reject(f"unexpected block index (expected {len(self.chain)}, got {index})")

                raw_imp = f"{kind}{sender}{receiver}{value}{timestamp}{index}{payload}"
                expected_imp_id = hashlib.sha256(raw_imp.encode()).hexdigest()
                if expected_imp_id != block_data.get("imp_id"):
                    raise _Reject("impulse id does not match its own claimed fields")

                imp = _ImpulseStub(
                    sender, receiver, value,
                    float(block_data.get("imp_energy", 0)),
                    float(block_data.get("imp_phi_bio", 0)),
                    block_data.get("imp_id"), timestamp,
                    block_data.get("imp_pubkey", ""),
                    block_data.get("imp_signature", ""),
                    float(block_data.get("imp_signed_ts", 0)),
                    kind, payload,
                    int(block_data.get("imp_nonce", 0)),
                )

                if not Network.verify_impulse_signature(imp):
                    raise _Reject("invalid sender signature")

                if not db.use_nonce(imp.sender, int(block_data.get("imp_nonce", 0))):
                    raise _Reject("nonce already used or not strictly increasing (replay rejected)")
                if not db.use_signature_once(imp.signature_hex, imp.sender, time.time()):
                    raise _Reject("signature already used (replay rejected)")

                validator = block_data.get("validator", "")
                if validator != "NETWORK":
                    alive_addrs = self._get_alive_sorted()
                    if not Network.verify_validator_selection(block_data["prev_hash"], imp.id, alive_addrs, validator):
                        raise _Reject("validator was not legitimately selected")

                raw_block = f"{index}{block_data['prev_hash']}{imp.id}{validator}{timestamp}"
                expected_hash = hashlib.sha256(raw_block.encode()).hexdigest()
                if expected_hash != block_data.get("hash"):
                    raise _Reject("block hash does not match its own claimed fields")

                prev_t = self.chain[-1].t if self.chain else 0.0
                if timestamp < prev_t:
                    raise _Reject("block timestamp moves chain time backwards")
                if timestamp > time.time() + 120:
                    raise _Reject("block timestamp is too far in the future")

                claimed_reward  = int(block_data.get("reward", 0))
                expected_reward = self._expected_reward(validator, timestamp)
                if claimed_reward != expected_reward:
                    raise _Reject(f"claimed reward {claimed_reward} sat does not match "
                                  f"deterministic recomputation {expected_reward} sat")

                db.ensure_wallet(sender)
                if kind == "TRANSFER":
                    fee = transfer_fee(value)
                    if value <= fee:
                        raise _Reject(f"transfer too small to cover the network fee ({sat_to_bio(fee):.4f} BIO)")
                    db.ensure_wallet(receiver)
                    if not db.debit(sender, value):
                        raise _Reject("sender has insufficient balance to apply this block")
                elif kind == "STAKE":
                    total_debit = value + Emission.STAKE_FEE
                    if not db.debit(sender, total_debit):
                        raise _Reject("sender has insufficient balance to apply this block (stake + fee)")
                elif kind == "REGISTER":
                    wallet_row = db.get_wallet(sender)
                    if wallet_row and int(wallet_row["registration_got"]) == 1:
                        raise _Reject("this address already claimed its registration grant")
                    if db.registration_granted_count() >= WALLET_REGISTRATION_MAX_COUNT:
                        raise _Reject(f"wallet registration grant exhausted (first {WALLET_REGISTRATION_MAX_COUNT} only)")
                    if self.emission.pools.get("wallet_registration", 0) < WALLET_REGISTRATION_GRANT:
                        raise _Reject("wallet_registration pool is empty")
                elif kind == "CLAIM_SERVER_REWARD":
                    raise _Reject("CLAIM_SERVER_REWARD is no longer supported -- "
                                   "use a server_reward governance PROPOSAL instead")
                elif kind == "UNSTAKE":
                    existing = db.get_stake(sender)
                    staked   = int(existing["bio_amount"]) if existing else 0
                    if value > staked:
                        raise _Reject(f"sender has insufficient active stake to apply this block (has {sat_to_bio(staked):.2f}, needs {sat_to_bio(value):.2f})")
                elif kind == "PROPOSAL":
                    if sender not in self.nodes:
                        raise _Reject("proposer is not a network node")
                    try:
                        pdata = json.loads(imp.payload)
                    except Exception:
                        raise _Reject("invalid proposal payload")
                    if "title" not in pdata or "param_key" not in pdata or "param_value" not in pdata:
                        raise _Reject("proposal payload missing required fields")
                elif kind == "VOTE":
                    if sender not in self.nodes or not self.nodes[sender].alive:
                        raise _Reject("voter is not a live network node")
                    try:
                        vdata = json.loads(imp.payload)
                    except Exception:
                        raise _Reject("invalid vote payload")
                    if vdata.get("vote") not in ("FOR", "AGAINST"):
                        raise _Reject("vote must be FOR or AGAINST")
                    if db.has_voted(vdata.get("proposal_id"), sender):
                        raise _Reject("already voted on this proposal")
                elif kind in ("SWAP_OFFER", "SWAP_LOCK", "SWAP_CLAIM", "SWAP_REFUND"):
                    swap_feasibility(kind, sender, receiver, value, imp.payload, timestamp)
                else:
                    raise _Reject(f"unknown impulse kind: {kind}")

                snap = self._snapshot_inmem()
                try:
                    self._apply_impulse_effect(imp)

                    if sender in self.nodes:
                        self.nodes[sender].balance = db.get_balance(sender)
                    if receiver in self.nodes:
                        self.nodes[receiver].balance = db.get_balance(receiver)

                    alive = [n for n in self.nodes_snapshot() if n.alive]
                    self.eco.update(imp.energy, self.emission, alive)

                    reward = expected_reward
                    if validator != "NETWORK" and reward > 0 and validator in self.nodes:
                        db.credit(validator, reward)
                        self.nodes[validator].balance = db.get_balance(validator)
                        self.emission.pools["validators"] = max(0, self.emission.pools["validators"] - reward)
                        self.emission.minted += reward

                    block = _BlockStub(index, block_data["hash"], block_data["prev_hash"],
                                       validator, reward, timestamp, imp)
                    self.chain.append(block)
                    self._demote_old_blocks()
                    if len(self.chain) == 1:
                        self.emission.start_time = block.t
                        self.vesting.start_time = block.t
                        db.set_vesting_start(block.t)
                    db.save_block(block)
                    db.save_economy(self.eco, self.emission)

                    self._after_block(block, sender, receiver, value)
                except Exception:
                    self._restore_inmem(snap, self._end_balance_recording())
                    raise
                finally:
                    self._end_balance_recording()
            if block and block.index > 0 and block.index % WAL_CHECKPOINT_EVERY == 0:
                try:
                    db.wal_checkpoint()
                except Exception as e:
                    db.log("WAL_CHECKPOINT_ERROR", f"block {block.index}: {e}")
                    print(f"[WAL] checkpoint failed at block {block.index}: {e}")
            return True, "ok"
        except _Reject as e:
            return False, str(e)
        except Exception as e:
            return False, f"internal error while applying block: {e}"

    def _find_divergence_index(self, peer_blocks: list) -> int:
        """Compares our chain against a peer's full block list (in /peer/chain format), index by index, and returns the index of the first block where the hashes differ."""
        n = min(len(self.chain), len(peer_blocks))
        for i in range(n):
            if self.chain[i].hash != peer_blocks[i]["hash"]:
                return i
        return n

    def resolve_fork(self, peer_blocks: list):
        """Called when a peer's next block does not extend our current tip -- a real fork, not just "we're behind"."""
        if len(peer_blocks) <= len(self.chain):
            return False, "peer's chain is not longer than ours -- nothing to adopt"

        d = self._find_divergence_index(peer_blocks)
        our_prefix = [Network.block_to_peer_dict(b) for b in self.chain[:d]]
        candidate  = our_prefix + peer_blocks[d:]

        ok, reason, temp_path = _replay_candidate_chain(candidate)
        if not ok:
            try:
                os.remove(temp_path)
            except Exception:
                pass
            return False, f"candidate chain failed replay at/after block {d}: {reason}"

        self._adopt_replayed_chain(temp_path)
        return True, f"adopted peer's chain (diverged at block {d}, {len(candidate)} blocks total)"

    def _adopt_replayed_chain(self, temp_db_path: str):
        """Swaps the live database for the one built during an isolated, already-fully-validated replay (see _replay_candidate_chain), then reloads all in-memory state."""
        global db
        db.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(DB_PATH + suffix)
            except FileNotFoundError:
                pass
        os.replace(temp_db_path, DB_PATH)
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(temp_db_path + suffix)
            except FileNotFoundError:
                pass
        db = Database(DB_PATH)
        self.chain    = []
        self.nodes    = {}
        self.eco      = Economy()
        self.emission = Emission()
        self.vesting  = Vesting()
        self.restore()

    def _mine(self):
        if not self.mempool:
            return None, "mempool is empty"

        imp = self.mempool[0]
        alive = [n for n in self.nodes_snapshot() if n.alive]

        if not alive:
            return self._bootstrap(imp)

        addr, validator = self._select_validator(imp)
        if not self._can_finalize(validator, imp):
            return self._bootstrap(imp)

        return self._finalize(imp, addr, validator)

    def _demote_old_blocks(self):
        """PERF FIX (v5.43): called after every new block append during
        live operation -- keeps only the most recent CHAIN_HOT_WINDOW
        blocks as full objects; whichever one just aged out of the
        window gets converted to a lightweight _LazyBlock. restore()
        already gives a freshly-restarted server this same memory
        saving for its whole history; this is what maintains it
        continuously for a long-running server that never restarts.
        O(1) per new block -- demotes exactly the one block that just
        crossed the boundary, never rescans the whole chain."""
        demote_index = len(self.chain) - CHAIN_HOT_WINDOW - 1
        if demote_index >= 0:
            old = self.chain[demote_index]
            if not isinstance(old, _LazyBlock):
                self.chain[demote_index] = _LazyBlock(
                    old.index, old.hash, old.prev_hash, old.validator, old.reward, old.t)

    def _verify_chain_integrity(self) -> bool:
        """Checks chain integrity before adding a new block"""
        if len(self.chain) < 2:
            return True
        last = self.chain[-1]
        prev = self.chain[-2]
        if last.prev_hash != prev.hash:
            db.log("CHAIN_ERROR",
                   f"block {last.index}: prev_hash mismatch")
            print(f"[CHAIN] WARNING: integrity violation at block {last.index}")
            return False
        return True

    def _apply_impulse_effect(self, imp):
        """What happens to a value once it's already locked/validated for this impulse's kind."""
        if imp.kind == "TRANSFER":
            fee     = transfer_fee(imp.value)
            net_amt = imp.value - fee
            self.emission.burn(fee)
            db.credit(imp.receiver, net_amt)
        elif imp.kind == "STAKE":
            self.emission.burn(Emission.STAKE_FEE)
            existing     = db.get_stake(imp.sender)
            old_staked   = int(existing["bio_amount"]) if existing else 0
            total_staked = old_staked + imp.value
            tier         = get_tier(total_staked)
            db.save_stake(imp.sender, total_staked, tier)
            db.log("STAKE", f"{imp.sender[:16]} +{sat_to_bio(imp.value)} BIO staked -> {tier} (total {sat_to_bio(total_staked)})")
            print(f"[STAKE] {imp.sender[:16]}... +{sat_to_bio(imp.value)} BIO -> tier {tier}")
        elif imp.kind == "REGISTER":
            given = db.try_give_registration(imp.sender, WALLET_REGISTRATION_GRANT)
            if given > 0:
                self.emission.pools["wallet_registration"] -= given
                self.emission.minted                       += given
                db.save_economy(self.eco, self.emission)
                print(f"[REGISTER] {imp.sender[:16]}... +{sat_to_bio(given)} BIO "
                      f"wallet-registration grant (#{db.registration_granted_count()}/{WALLET_REGISTRATION_MAX_COUNT})")
        elif imp.kind == "UNSTAKE":
            existing  = db.get_stake(imp.sender)
            staked    = int(existing["bio_amount"]) if existing else 0
            remaining = staked - imp.value
            new_tier  = get_tier(remaining)
            db.save_stake(imp.sender, remaining, new_tier)
            db.create_pending_unstake(imp.sender, imp.value, imp.t)
            db.log("UNSTAKE_REQUESTED", f"{imp.sender[:16]} -{sat_to_bio(imp.value)} BIO -- {UNSTAKE_COOLDOWN//86400:.0f}-day cooldown started")
            print(f"[UNSTAKE] {imp.sender[:16]}... requested -{sat_to_bio(imp.value)} BIO, cooldown started")
        elif imp.kind == "SWAP_OFFER":
            data = json.loads(imp.payload)
            if "cancel_offer_id" in data:
                db.cancel_swap_offer(str(data["cancel_offer_id"]))
                db.log("SWAP_OFFER_CANCELLED", f"{imp.sender[:16]} cancelled offer {str(data['cancel_offer_id'])[:12]}")
                print(f"[SWAP] {imp.sender[:16]}... offer cancelled")
            else:
                give = int(data["give_bio"])
                self.emission.burn(transfer_fee(give))
                db.create_swap_offer(imp.id, imp.sender, give, data["want_asset"],
                                     int(data["want_amount"]), str(data["ext_address"]),
                                     imp.t, int(data["ttl"]))
                db.log("SWAP_OFFER", f"{imp.sender[:16]} offers {sat_to_bio(give)} BIO for {data['want_amount']} {data['want_asset']} (sat-units)")
                print(f"[SWAP] {imp.sender[:16]}... OFFER {sat_to_bio(give)} BIO -> {data['want_asset']}")
        elif imp.kind == "SWAP_LOCK":
            data = json.loads(imp.payload)
            self.emission.burn(transfer_fee(imp.value))
            db.create_swap_lock(imp.id, imp.sender, imp.receiver, imp.value,
                                str(data["hash_lock"]).lower(), imp.t, int(data["timeout"]))
            db.log("SWAP_LOCK", f"{imp.sender[:16]} locked {sat_to_bio(imp.value)} BIO for {imp.receiver[:16]} (timeout {int(data['timeout'])} s)")
            print(f"[SWAP] {imp.sender[:16]}... LOCKED {sat_to_bio(imp.value)} BIO under hash-lock")
        elif imp.kind == "SWAP_CLAIM":
            data = json.loads(imp.payload)
            lock = db.get_swap_lock(str(data["lock_id"]))
            db.credit(lock["receiver"], int(lock["amount"]))
            db.settle_swap_lock(lock["id"], "CLAIMED", str(data["preimage"]).lower())
            db.log("SWAP_CLAIM", f"{imp.sender[:16]} claimed {sat_to_bio(int(lock['amount']))} BIO (preimage revealed)")
            print(f"[SWAP] {imp.sender[:16]}... CLAIMED {sat_to_bio(int(lock['amount']))} BIO -- secret is now public")
        elif imp.kind == "SWAP_REFUND":
            data = json.loads(imp.payload)
            lock = db.get_swap_lock(str(data["lock_id"]))
            db.credit(lock["sender"], int(lock["amount"]))
            db.settle_swap_lock(lock["id"], "REFUNDED")
            db.log("SWAP_REFUND", f"{imp.sender[:16]} refunded {sat_to_bio(int(lock['amount']))} BIO after timeout")
            print(f"[SWAP] {imp.sender[:16]}... REFUNDED {sat_to_bio(int(lock['amount']))} BIO")
        elif imp.kind == "PROPOSAL":
            data = json.loads(imp.payload)
            pid = db.create_proposal(
                data["title"], data.get("description", ""), imp.sender,
                imp.t, data.get("duration_days", 7),
                data["param_key"], data["param_value"],
            )
            db.log("PROPOSAL_CREATED", f"#{pid} {data['title']} by {imp.sender[:16]}")
            print(f"[PROPOSAL] #{pid} '{data['title']}' created by {imp.sender[:16]}...")
        elif imp.kind == "VOTE":
            data = json.loads(imp.payload)
            cast_ok = db.cast_vote(data["proposal_id"], imp.sender, data["vote"], 1.0)
            if cast_ok:
                db.log("VOTE", f"{imp.sender[:16]} {data['vote']} proposal #{data['proposal_id']}")
                print(f"[VOTE] {imp.sender[:16]}... {data['vote']} proposal #{data['proposal_id']}")
            else:
                print(f"[VOTE] {imp.sender[:16]}... vote on #{data['proposal_id']} not counted (already voted)")

    def _bootstrap(self, imp):
        """Processing without a validator -- at startup and with weak nodes"""
        self.mempool.pop(0)
        self._apply_impulse_effect(imp)

        alive = [n for n in self.nodes_snapshot() if n.alive]
        self.eco.update(imp.energy, self.emission, alive)

        prev  = self.chain[-1].hash if self.chain else "0" * 64
        block = Block(len(self.chain), prev, imp, "NETWORK", 0)
        self.chain.append(block)
        self._demote_old_blocks()
        if len(self.chain) == 1:
            self.emission.start_time = block.t
            self.vesting.start_time  = block.t
            db.set_vesting_start(block.t)

        self._verify_chain_integrity()

        db.save_block(block)
        db.save_economy(self.eco, self.emission)
        return block, "ok (bootstrap)"

    def _finalize(self, imp, addr, validator):
        """Processing with a validator"""
        self.mempool.pop(0)

        reward = self.emission.mint_reward(validator, len(self.chain), imp.t)
        if reward > 0:
            db.credit(validator.address, reward)

        self._apply_impulse_effect(imp)

        if imp.sender in self.nodes:
            self.nodes[imp.sender].balance = db.get_balance(imp.sender)
        if imp.receiver in self.nodes:
            self.nodes[imp.receiver].balance = db.get_balance(imp.receiver)

        alive = [n for n in self.nodes_snapshot() if n.alive]
        self.eco.update(imp.energy, self.emission, alive)

        prev  = self.chain[-1].hash if self.chain else "0" * 64
        block = Block(len(self.chain), prev, imp, addr, reward)
        self.chain.append(block)
        self._demote_old_blocks()
        if len(self.chain) == 1:
            self.emission.start_time = block.t
            self.vesting.start_time  = block.t
            db.set_vesting_start(block.t)

        self._verify_chain_integrity()

        db.save_block(block)
        db.save_node(validator)
        db.save_economy(self.eco, self.emission)

        if block.index > 0 and block.index % CHECKPOINT_EVERY == 0:
            alive_count = sum(1 for n in self.nodes_snapshot() if n.alive)
            db.save_checkpoint(block.index, block.hash, alive_count)
            db.log("CHECKPOINT",
                   f"block {block.index} | hash={block.hash[:16]} | nodes={alive_count}")
            print(f"[CHECKPOINT] block {block.index} recorded | nodes={alive_count}")
            try:
                maybe_create_state_snapshot(block.index)
            except Exception as e:
                db.log("SNAPSHOT_ERROR", f"block {block.index}: {e}")
                print(f"[SNAPSHOT] FAILED at block {block.index}: {e}")

        return block, "ok"


    def restore(self):
        eco_row = db.load_economy()
        if eco_row:
            self.eco.liquidity                 = eco_row["liquidity"]
            self.eco.risk                      = eco_row["risk"]
            self.emission.minted               = eco_row["minted"]
            self.emission.burned               = eco_row["burned"]
            self.emission.halvings             = eco_row["halvings"]
            self.emission.genesis_granted      = eco_row["genesis_granted"]
            self.emission.pools["validators"]  = eco_row["pool_validators"]
            self.emission.pools["ecosystem"]   = eco_row["pool_ecosystem"]
            self.emission.pools["reserve"]     = eco_row["pool_reserve"]
            self.emission.pools["team"]        = eco_row["pool_team"]
            self.emission.pools["genesis"]     = eco_row["pool_genesis"]
            self.emission.pools["listing_reserve"] = eco_row["pool_listing_reserve"]
            if "pool_wallet_registration" in eco_row.keys():
                self.emission.pools["wallet_registration"] = eco_row["pool_wallet_registration"]
            if "total_destroyed" in eco_row.keys():
                self.emission.total_destroyed = eco_row["total_destroyed"]
            if "pool_developer_grants" in eco_row.keys():
                self.emission.pools["developer_grants"] = eco_row["pool_developer_grants"]
            if "pool_server_rewards" in eco_row.keys():
                self.emission.pools["server_rewards"] = eco_row["pool_server_rewards"]
            if eco_row["emission_start"] and eco_row["emission_start"] > 0:
                self.emission.start_time = eco_row["emission_start"]

        for row in db.load_nodes():
            addr = row["address"]
            node                 = Node(addr, row["births"])
            node.balance         = row["balance"]
            node.energy          = row["energy"]
            node.activity        = row["activity"]
            node.recent_activity = row["recent_activity"] or 0.0
            node.reputation      = row["reputation"]
            node.age             = row["age"]
            node.alive           = bool(row["alive"])
            node.born_at         = row["born_at"]
            node.died_at         = row["died_at"]
            node.role            = row["role"] or "VALIDATOR"
            node.risk            = row["risk"] or 0.0
            row_keys = row.keys()
            node.longevity_6mo_paid  = bool(row["longevity_6mo"])  if "longevity_6mo"  in row_keys else False
            node.longevity_12mo_paid = bool(row["longevity_12mo"]) if "longevity_12mo" in row_keys else False
            node.last_monthly_payout = (row["last_monthly_payout"] or 0.0) if "last_monthly_payout" in row_keys else 0.0
            node.tx_count_at_death   = (row["tx_count_at_death"] or 0)   if "tx_count_at_death" in row_keys else 0
            node.inherited_rep       = (row["inherited_rep"] or 0.0)     if "inherited_rep"     in row_keys else 0.0
            node.inherited_risk      = (row["inherited_risk"] or 0.0)    if "inherited_risk"    in row_keys else 0.0
            node.state_block = (row["state_block"] or 0) if "state_block" in row_keys else 0
            self.nodes[addr]     = node

        alive  = sum(1 for n in self.nodes_snapshot() if n.alive)
        dead   = len(self.nodes) - alive
        print(f"[DB] Restored {len(self.nodes)} nodes ({alive} alive, {dead} dead)")

        _chain_integrity_ok = True
        total_blocks = db.count_blocks_table()
        hot_window_start = max(0, total_blocks - CHAIN_HOT_WINDOW)
        _cold_count = 0
        for row in db.load_blocks_streaming():
            idx = row["idx"]
            if idx >= hot_window_start:
                imp = _impulse_from_row(row)
                block = _BlockStub(
                    idx,                 row["hash"],
                    row["prev_hash"],   row["validator"],
                    row["reward"],      row["timestamp"],
                    imp,
                )
            else:
                block = _LazyBlock(
                    idx,                 row["hash"],
                    row["prev_hash"],   row["validator"],
                    row["reward"],      row["timestamp"],
                )
                _cold_count += 1
            if _chain_integrity_ok and self.chain and block.prev_hash != self.chain[-1].hash:
                print(f"[DB][WARNING] chain integrity break detected at block "
                      f"{len(self.chain)} during restore -- /verify will "
                      f"report this via its own full scan")
                _chain_integrity_ok = False
            self.chain.append(block)
        if _chain_integrity_ok:
            self._verified_up_to_index = len(self.chain) - 1
        if self.chain:
            print(f"[DB] Restored {len(self.chain)} blocks in the chain "
                  f"({_cold_count} cold/lazy, {len(self.chain)-_cold_count} hot/full)")
            if abs(self.emission.start_time - self.chain[0].t) > 1e-6:
                self.emission.start_time = self.chain[0].t
            if abs(self.vesting.start_time - self.chain[0].t) > 1e-6:
                self.vesting.start_time = self.chain[0].t
                db.set_vesting_start(self.chain[0].t)

        now_block = len(self.chain)
        legacy_corrected = 0
        for node in self.nodes.values():
            if not node.alive:
                continue
            if node.state_block == 0 and now_block > 0:
                node.state_block = now_block
                legacy_corrected += 1
            self._schedule_death(node, node.state_block)
            self._alive_energy_sum += node.energy
        if legacy_corrected:
            print(f"[DB] {legacy_corrected} node(s) had their decay clock "
                  f"anchored to the current restore point (pre-migration rows)")
        print(f"[DB] death schedule rebuilt: {len(self._death_schedule)} entries, "
              f"alive-energy sum: {self._alive_energy_sum:.1f}")

    def state(self) -> dict:
        alive = [n for n in self.nodes_snapshot() if n.alive]
        return {
            "nodes_alive":  len(alive),
            "nodes_total":  len(self.nodes),
            "nodes":        {n.address: n.to_dict(self.eco.liquidity, self.eco.risk)
                             for n in self.nodes_snapshot()},
            "economy":      self.eco.state(),
            "biofield": {
                "phi_bio": round(self.phi_bio(), 6),
            },
            "chain_len":    len(self.chain),
            "mempool":      len(self.mempool),
            "wallets":      db.count_wallets(),
            "emerge_threshold": EMERGE_THRESHOLD,
            "thresholds": {
                "theta_s": self.THETA_S,
                "theta_w": self.THETA_W,
                "theta_i": self.THETA_I,
            },
        }

    def chain_view(self) -> list:
        def fee_for(imp):
            k = getattr(imp, "kind", "TRANSFER")
            if k == "TRANSFER":
                return transfer_fee(imp.value)
            if k == "STAKE":
                return Emission.STAKE_FEE
            if k == "SWAP_LOCK":
                return transfer_fee(imp.value)
            if k == "SWAP_OFFER":
                try:
                    dd = json.loads(getattr(imp, "payload", "") or "{}")
                    if "cancel_offer_id" not in dd:
                        return transfer_fee(int(dd.get("give_bio", 0)))
                except Exception:
                    pass
                return 0
            return 0

        return [
            {
                "index":     b.index,
                "hash":      b.hash[:16],
                "validator": b.validator,
                "reward":    round(sat_to_bio(b.reward), 4),
                "kind":      getattr(b.impulse, "kind", "TRANSFER"),
                "tx": {
                    "from":     b.impulse.sender,
                    "to":       b.impulse.receiver,
                    "value":    sat_to_bio(b.impulse.value),
                    "energy":   round(b.impulse.energy,   4),
                    "phi_bio":  round(b.impulse.phi_bio,  6),
                    "fee":      round(sat_to_bio(fee_for(b.impulse)), 6),
                },
            }
            for b in self.chain
        ]

def _replay_candidate_chain(candidate_blocks: list):
    """Replays a full candidate chain (a list of block dicts, in /peer/chain format) into a fresh, temporary, completely isolated database -- the live `db`/`net` are."""
    global db
    temp_path = f"{DB_PATH}.candidate_{int(time.time()*1000)}.db"
    saved_db  = db
    db = Database(temp_path)
    try:
        temp_net = Network()
        _apply_founder_grant(temp_net)
        _fund_developer_grants_pool(temp_net)
        _fund_wallet_registration_pool(temp_net)
        for block_data in candidate_blocks:
            ok, reason = temp_net._apply_peer_block_locked(block_data)
            if not ok:
                return False, reason, temp_path
        return True, "ok", temp_path
    finally:
        db.conn.close()
        db = saved_db

FOUNDER_GRANT = 10000 * SAT_PER_BIO

WALLET_REGISTRATION_GRANT     = 10 * SAT_PER_BIO
WALLET_REGISTRATION_MAX_COUNT = 100
WALLET_REGISTRATION_POOL_SIZE = WALLET_REGISTRATION_GRANT * WALLET_REGISTRATION_MAX_COUNT

def _apply_founder_grant(target_net) -> int:
    """Developer's starting balance -- drawn from the genesis pool's own unassigned remainder, not minted on top of the 21,000,000 cap."""
    if db.count_blocks() > 0:
        return 0
    existing = db.get_wallet(TEAM_ADDRESS)
    if existing and int(existing["balance"]) > 0:
        return 0
    actual = min(FOUNDER_GRANT, target_net.emission.pools["genesis"])
    db.ensure_wallet(TEAM_ADDRESS)
    db.credit(TEAM_ADDRESS, actual)
    target_net.emission.pools["genesis"] -= actual
    target_net.emission.minted           += actual
    db.save_economy(target_net.eco, target_net.emission)
    db.log("FOUNDER_GRANT", f"{TEAM_ADDRESS} +{sat_to_bio(actual)} BIO (starting capital, from genesis pool remainder)")
    print(f"[FOUNDER] {TEAM_ADDRESS} +{sat_to_bio(actual)} BIO starting capital (from genesis pool remainder)")
    return actual


def _fund_wallet_registration_pool(target_net) -> int:
    """moves WALLET_REGISTRATION_POOL_SIZE (1,000 BIO) out of the founder's own wallet into the wallet_registration pool -- literally "from the founder's 10,000", not."""
    if target_net.emission.pools.get("wallet_registration", 0) > 0:
        return 0
    carve = WALLET_REGISTRATION_POOL_SIZE
    if not db.debit(TEAM_ADDRESS, carve):
        print(f"[FOUNDER] could not fund wallet_registration pool -- "
              f"{TEAM_ADDRESS} balance below {sat_to_bio(carve)} BIO")
        return 0
    if TEAM_ADDRESS in target_net.nodes:
        target_net.nodes[TEAM_ADDRESS].balance = db.get_balance(TEAM_ADDRESS)
        db.save_node(target_net.nodes[TEAM_ADDRESS])
    target_net.emission.pools["wallet_registration"] = \
        target_net.emission.pools.get("wallet_registration", 0) + carve
    db.save_economy(target_net.eco, target_net.emission)
    db.log("WALLET_REGISTRATION_POOL_FUNDED",
           f"{sat_to_bio(carve)} BIO moved from {TEAM_ADDRESS} to wallet_registration pool "
           f"(funds the first {WALLET_REGISTRATION_MAX_COUNT} new-wallet grants of "
           f"{sat_to_bio(WALLET_REGISTRATION_GRANT)} BIO each)")
    print(f"[FOUNDER] -{sat_to_bio(carve)} BIO carved into wallet_registration pool")
    return carve

def _fund_developer_grants_pool(target_net) -> int:
    """moves DEVELOPER_GRANTS_POOL_SIZE (509,000 BIO) out of the genesis pool's remainder into developer_grants -- pool-to-pool, no wallet involved."""
    if target_net.emission.pools.get("developer_grants", 0) > 0:
        return 0
    carve = min(DEVELOPER_GRANTS_POOL_SIZE, target_net.emission.pools["genesis"])
    target_net.emission.pools["genesis"] -= carve
    target_net.emission.pools["developer_grants"] = \
        target_net.emission.pools.get("developer_grants", 0) + carve
    db.save_economy(target_net.eco, target_net.emission)
    db.log("DEVELOPER_GRANTS_POOL_FUNDED", f"{sat_to_bio(carve)} BIO moved from genesis to developer_grants pool")
    print(f"[GENESIS] -{sat_to_bio(carve)} BIO carved into developer_grants pool")
    return carve

def _fund_server_rewards_pool(target_net) -> int:
    """moves SERVER_REWARDS_POOL_SIZE (254,500 BIO -- exactly half of the original 509,000 developer_grants pool) OUT of developer_grants into a new server_rewards."""
    if target_net.emission.pools.get("server_rewards", 0) > 0:
        return 0
    carve = min(SERVER_REWARDS_POOL_SIZE, target_net.emission.pools.get("developer_grants", 0))
    target_net.emission.pools["developer_grants"] -= carve
    target_net.emission.pools["server_rewards"] = \
        target_net.emission.pools.get("server_rewards", 0) + carve
    db.save_economy(target_net.eco, target_net.emission)
    db.log("SERVER_REWARDS_POOL_FUNDED", f"{sat_to_bio(carve)} BIO moved from developer_grants to server_rewards pool")
    print(f"[GENESIS] -{sat_to_bio(carve)} BIO moved from developer_grants into server_rewards pool")
    return carve

net = Network()

if db.count_blocks() > 0 or db.count_wallets() > 0:
    print("[DB] Restoring network state...")
    net.restore()
else:
    print("[BIOCHAIN] Fresh network -- no nodes yet")
    print(f"[BIOCHAIN] Nodes are born after {EMERGE_THRESHOLD} impulses from an address")
    _apply_founder_grant(net)

_fund_developer_grants_pool(net)
_fund_server_rewards_pool(net)
_fund_wallet_registration_pool(net)

if TEAM_ADDRESS in net.nodes:
    _wallet_bal = db.get_balance(TEAM_ADDRESS)
    if net.nodes[TEAM_ADDRESS].balance != _wallet_bal:
        print(f"[REPAIR] {TEAM_ADDRESS} node.balance was stale "
              f"({sat_to_bio(net.nodes[TEAM_ADDRESS].balance)} BIO) vs wallets "
              f"({sat_to_bio(_wallet_bal)} BIO) -- resyncing")
        net.nodes[TEAM_ADDRESS].balance = _wallet_bal
        db.save_node(net.nodes[TEAM_ADDRESS])

_overrides = db.get_param_overrides()
if _overrides:
    print(f"[GOV] Restoring {len(_overrides)} parameter(s) from past decisions...")
    for row in _overrides:
        ok, msg = apply_governance_param(row["key"], row["value"])
        if ok:
            print(f"[GOV] restored: {msg}")
        else:
            print(f"[GOV] failed to restore {row['key']}: {msg}")

_promoted = db.load_promoted_peers()
if _promoted:
    _new_peers = [p for p in _promoted if p not in PEER_URLS]
    if _new_peers:
        PEER_URLS.extend(_new_peers)
        print(f"[DISCOVERY] Restored {len(_new_peers)} auto-promoted peer(s) from past sessions")

def signature_pruning_loop():
    """Background cleanup of spent replay-protection signatures -- once a minute."""
    while True:
        db.prune_old_signatures(time.time() - REQUEST_FRESHNESS_SECONDS - 60)
        time.sleep(60)

threading.Thread(target=signature_pruning_loop, daemon=True).start()

def sync_with_peer(peer_url: str):
    """Checks one peer's chain length; if they are ahead, first tries the common, cheap case -- their chain cleanly EXTENDS ours -- applying just the blocks we're."""
    if not HTTP_OK:
        return
    try:
        info = http_requests.get(f"{peer_url}/peer/chain_info", timeout=PEER_REQUEST_TIMEOUT_SECONDS).json()
    except Exception as e:
        print(f"[PEER] {peer_url} unreachable: {e}")
        return

    if info.get("instance_id") == INSTANCE_ID:
        print(f"[PEER] {peer_url} responded with OUR OWN instance_id -- "
              f"this peer IS this server (check SELF_URL / proxy config). "
              f"Removing it from PEER_URLS.")
        if peer_url in PEER_URLS:
            PEER_URLS.remove(peer_url)
        return

    their_genesis = info.get("genesis_hash", "")
    my_genesis    = net.chain[0].hash if net.chain else ""
    if their_genesis and my_genesis and their_genesis != my_genesis:
        print(f"[PEER] {peer_url} has a DIFFERENT genesis hash "
              f"({their_genesis[:16]} vs ours {my_genesis[:16]}) -- "
              f"this is a different network, not a fork. Refusing to sync.")
        return

    their_len = info.get("chain_len", 0)
    my_len    = len(net.chain)
    if their_len <= my_len:
        return

    print(f"[PEER] {peer_url} has {their_len} blocks, we have {my_len} -- catching up")
    try:
        resp = http_requests.get(
            f"{peer_url}/peer/chain", params={"from_block": my_len},
            timeout=PEER_REQUEST_TIMEOUT_SECONDS
        ).json()
    except Exception as e:
        print(f"[PEER] failed to fetch blocks from {peer_url}: {e}")
        return

    applied = 0
    fork_detected = False
    for block_data in resp.get("blocks", []):
        ok, reason = net.apply_peer_block(block_data)
        if not ok:
            fork_detected = True
            print(f"[PEER] {peer_url} block {block_data.get('index')} "
                  f"does not cleanly extend our tip ({reason}) -- this is a fork")
            break
        applied += 1
    if applied:
        print(f"[PEER] caught up {applied} block(s) from {peer_url} -- now at {len(net.chain)}")

    if not fork_detected:
        return

    print(f"[PEER] attempting deep fork resolution with {peer_url}...")
    try:
        full_resp = http_requests.get(
            f"{peer_url}/peer/chain", params={"from_block": 0},
            timeout=PEER_REQUEST_TIMEOUT_SECONDS
        ).json()
    except Exception as e:
        print(f"[PEER] failed to fetch full chain from {peer_url} for fork resolution: {e}")
        return

    peer_blocks = full_resp.get("blocks", [])
    with _chain_lock:
        ok, reason = net.resolve_fork(peer_blocks)
    print(f"[PEER] fork resolution with {peer_url}: {'adopted' if ok else 'kept our own chain'} -- {reason}")

def fast_sync_from_snapshot(peer_url: str) -> bool:
    """Spec section 6: try to skip full replay by adopting a verified state snapshot before falling back to the normal block-by-block peer sync."""
    if not HTTP_OK:
        return False
    if net.chain:
        return False
    try:
        info = http_requests.get(f"{peer_url}/peer/chain_info", timeout=PEER_REQUEST_TIMEOUT_SECONDS).json()
        if info.get("instance_id") == INSTANCE_ID:
            print(f"[FASTSYNC] {peer_url} IS this server (own instance_id) -- skipping")
            return False
    except Exception:
        pass
    try:
        ckpts = http_requests.get(f"{peer_url}/checkpoints",
                                  timeout=PEER_REQUEST_TIMEOUT_SECONDS).json()
    except Exception as e:
        print(f"[FASTSYNC] could not fetch checkpoints from {peer_url}: {e}")
        return False
    candidates = [c for c in ckpts.get("checkpoints", []) if c.get("state_hash")]
    if not candidates:
        print("[FASTSYNC] peer has no state snapshots yet -- falling back to full replay")
        return False
    best = max(candidates, key=lambda c: c["block_idx"])
    height, claimed_hash = best["block_idx"], best["state_hash"]
    try:
        resp = http_requests.get(f"{peer_url}/peer/snapshot/{height}",
                                 timeout=PEER_REQUEST_TIMEOUT_SECONDS * 4).json()
    except Exception as e:
        print(f"[FASTSYNC] could not fetch snapshot {height} from {peer_url}: {e}")
        return False
    if "error" in resp:
        print(f"[FASTSYNC] peer error for snapshot {height}: {resp['error']}")
        return False
    snapshot = resp["snapshot"]
    recomputed = canonical_state_hash(snapshot)
    if recomputed != claimed_hash:
        print(f"[FASTSYNC] HASH MISMATCH at height {height} -- "
              f"claimed={claimed_hash[:16]} recomputed={recomputed[:16]} -- "
              f"REJECTING snapshot entirely, falling back to full replay")
        return False
    try:
        with db.lock:
            db.conn.execute("BEGIN IMMEDIATE")
            for table in SNAPSHOT_TABLES:
                db.conn.execute(f"DELETE FROM {table}")
                rows = snapshot.get(table, [])
                if not rows:
                    continue
                cols = sorted(rows[0].keys())
                placeholders = ",".join("?" * len(cols))
                col_list = ",".join(cols)
                db.conn.executemany(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                    [tuple(r[c] for c in cols) for r in rows])
            db.conn.commit()
    except Exception as e:
        db.conn.rollback()
        print(f"[FASTSYNC] snapshot load failed, rolled back: {e}")
        return False
    print(f"[FASTSYNC] adopted verified state snapshot at height {height} "
          f"(hash={recomputed[:16]}) -- continuing sync from block {height+1}")
    db.log("FASTSYNC", f"adopted snapshot height={height} hash={recomputed[:16]} from {peer_url}")
    return True

def peer_sync_loop():
    """Periodically checks all configured peers and catches up on anything we're missing."""
    while True:
        for peer_url in PEER_URLS:
            try:
                sync_with_peer(peer_url)
            except Exception as e:
                print(f"[PEER] sync error with {peer_url}: {e}")
        time.sleep(PEER_SYNC_INTERVAL_SECONDS)

GOSSIP_INTERVAL_SECONDS  = 3600
CANDIDATE_PRUNE_INTERVAL_SECONDS = 86400

def promotion_threshold() -> int:
    """the number of DISTINCT trusted peers that must independently confirm a candidate before it's auto-promoted into PEER_URLS."""
    n = len(PEER_URLS)
    if n <= 0:
        return 1
    return n // 2 + 1

def try_promote_candidate(url: str, confirmations: int) -> bool:
    """the automatic, no-human-required promotion path."""
    if confirmations < promotion_threshold():
        return False
    if HTTP_OK:
        try:
            info = http_requests.get(f"{url}/peer/chain_info", timeout=PEER_REQUEST_TIMEOUT_SECONDS).json()
            if info.get("instance_id") == INSTANCE_ID:
                print(f"[DISCOVERY] refusing to promote {url} -- it IS this server "
                      f"(own instance_id), despite {confirmations} gossip confirmation(s)")
                return False
        except Exception:
            pass
    promoted = db.save_promoted_peer(url, confirmations)
    if promoted:
        PEER_URLS.append(url)
        db.log("PEER_AUTO_PROMOTED",
               f"{url} promoted to trusted peer -- {confirmations}/{len(PEER_URLS)-1} "
               f"existing peers confirmed it independently")
        print(f"[DISCOVERY] {url} auto-promoted to trusted peer "
              f"({confirmations} confirmations, threshold was {promotion_threshold()})")
    return promoted

def gossip_with_peers():
    """Asks each TRUSTED peer what other nodes it knows about."""
    if not HTTP_OK:
        return
    for peer_url in PEER_URLS:
        try:
            resp = http_requests.get(f"{peer_url}/peer/known_nodes",
                                     timeout=PEER_REQUEST_TIMEOUT_SECONDS).json()
        except Exception as e:
            print(f"[GOSSIP] could not reach {peer_url}: {e}")
            continue
        heard = set(resp.get("trusted_peers", [])) | \
                {c["url"] for c in resp.get("candidates", [])}
        heard -= set(PEER_URLS)
        heard.discard(peer_url)
        if SELF_URL:
            heard.discard(SELF_URL)
        for url in heard:
            try:
                db.note_node_candidate(url, reporter_url=peer_url)
            except Exception as e:
                print(f"[GOSSIP] failed to record candidate {url} from {peer_url}: {e}")
        if heard:
            print(f"[GOSSIP] {peer_url} mentioned {len(heard)} node(s) we don't already trust")

    try:
        for candidate in db.list_node_candidates(min_confirmations=1):
            try_promote_candidate(candidate["url"], candidate["confirmations"])
    except Exception as e:
        print(f"[GOSSIP] promotion pass error: {e}")

def gossip_loop():
    last_prune = 0.0
    while True:
        try:
            gossip_with_peers()
        except Exception as e:
            print(f"[GOSSIP] loop error: {e}")
        now = time.time()
        if now - last_prune > CANDIDATE_PRUNE_INTERVAL_SECONDS:
            try:
                db.prune_stale_candidates()
            except Exception as e:
                print(f"[GOSSIP] prune error: {e}")
            last_prune = now
        time.sleep(GOSSIP_INTERVAL_SECONDS)

if PEER_URLS:
    for _peer in PEER_URLS:
        try:
            if fast_sync_from_snapshot(_peer):
                break
        except Exception as e:
            print(f"[FASTSYNC] error trying {_peer}: {e}")
    threading.Thread(target=peer_sync_loop, daemon=True).start()
    threading.Thread(target=gossip_loop, daemon=True).start()
else:
    print("[PEER] no peers configured -- running standalone (edit PEER_URLS to enable sync)")

def _governance_tick(now: float):
    """A single pass over open proposals, using the chain's own time (not this server's wall clock -- see Network.chain_time)."""
    for row in db.get_open_proposals():
        pid    = row["id"]
        status = row["status"]
        total  = row["votes_for"] + row["votes_against"]
        pct_for = (row["votes_for"] / total) if total > 0 else 0.0

        if status == "ACTIVE" and now >= row["ends_at"]:
            if total >= GOVERNANCE_MIN_VOTES and pct_for >= GOVERNANCE_THRESHOLD:
                db.update_proposal_status(pid, "APPROVED")
                db.log("GOVERNANCE_APPROVED",
                       f"#{pid} {row['title']} ({pct_for*100:.1f}% for, {total} votes) -- 7-day timelock")
                print(f"[GOV] #{pid} '{row['title']}' approved ({pct_for*100:.1f}%, {total} votes) -- awaiting timelock")
            elif total < GOVERNANCE_MIN_VOTES:
                db.update_proposal_status(pid, "REJECTED")
                db.log("GOVERNANCE_REJECTED",
                       f"#{pid} {row['title']} (only {total} votes, needs >= {GOVERNANCE_MIN_VOTES})")
                print(f"[GOV] #{pid} '{row['title']}' rejected -- too few votes ({total}/{GOVERNANCE_MIN_VOTES} needed)")
            else:
                db.update_proposal_status(pid, "REJECTED")
                db.log("GOVERNANCE_REJECTED",
                       f"#{pid} {row['title']} ({pct_for*100:.1f}% for, needs 70%)")
                print(f"[GOV] #{pid} '{row['title']}' rejected ({pct_for*100:.1f}%)")

        elif status == "APPROVED" and now >= row["apply_at"]:
            ok, msg = apply_governance_param(row["param_key"], row["param_value"], pid)
            db.update_proposal_status(pid, "APPLIED" if ok else "FAILED")
            db.log("GOVERNANCE_APPLIED" if ok else "GOVERNANCE_FAILED", f"#{pid} {msg}")
            print(f"[GOV] #{pid} {'APPLIED' if ok else 'ERROR'}: {msg}")


LONGEVITY_6MO_DAYS       = 182.5
LONGEVITY_12MO_DAYS      = 365.0
LONGEVITY_6MO_REWARD     = 10  * SAT_PER_BIO
LONGEVITY_12MO_REWARD    = 100 * SAT_PER_BIO
LONGEVITY_MONTHLY_REWARD = 21.0
LONGEVITY_MONTH_DAYS     = 30.0
DEATH_SWEEP_DAYS         = 365.0

def _longevity_tick(now: float):
    """A single pass checking all nodes, using the chain's own time (not this server's wall clock -- see Network.chain_time)."""
    for n in net.nodes_snapshot():
        if n.alive:
            age_days = (now - n.born_at) / 86400

            if not n.longevity_6mo_paid and age_days >= LONGEVITY_6MO_DAYS:
                if net.emission.pools["ecosystem"] >= LONGEVITY_6MO_REWARD:
                    net.emission.pools["ecosystem"] -= LONGEVITY_6MO_REWARD
                    net.emission.minted              += LONGEVITY_6MO_REWARD
                    db.credit(n.address, LONGEVITY_6MO_REWARD)
                    n.balance = db.get_balance(n.address)
                    n.longevity_6mo_paid = True
                    db.save_node(n)
                    db.log("LONGEVITY_6MO", f"{n.address[:16]} +{sat_to_bio(LONGEVITY_6MO_REWARD)} BIO (half a year of life)")
                    print(f"[LONGEVITY] {n.address[:16]}... +{sat_to_bio(LONGEVITY_6MO_REWARD)} BIO -- half a year of active life")

            if not n.longevity_12mo_paid and age_days >= LONGEVITY_12MO_DAYS:
                if net.emission.pools["ecosystem"] >= LONGEVITY_12MO_REWARD:
                    net.emission.pools["ecosystem"] -= LONGEVITY_12MO_REWARD
                    net.emission.minted              += LONGEVITY_12MO_REWARD
                    db.credit(n.address, LONGEVITY_12MO_REWARD)
                    n.balance = db.get_balance(n.address)
                    n.longevity_12mo_paid = True
                    n.last_monthly_payout = now
                    db.save_node(n)
                    db.log("LONGEVITY_12MO", f"{n.address[:16]} +{sat_to_bio(LONGEVITY_12MO_REWARD)} BIO (one year of life)")
                    print(f"[LONGEVITY] {n.address[:16]}... +{sat_to_bio(LONGEVITY_12MO_REWARD)} BIO -- one year of active life")

            elif n.longevity_12mo_paid:
                days_since = (now - n.last_monthly_payout) / 86400
                if days_since >= LONGEVITY_MONTH_DAYS:
                    monthly_sat = bio_to_sat(LONGEVITY_MONTHLY_REWARD)
                    if net.emission.pools["ecosystem"] >= monthly_sat:
                        net.emission.pools["ecosystem"] -= monthly_sat
                        net.emission.minted              += monthly_sat
                        db.credit(n.address, monthly_sat)
                        n.balance = db.get_balance(n.address)
                        n.last_monthly_payout = now
                        db.save_node(n)
                        db.log("LONGEVITY_MONTHLY", f"{n.address[:16]} +{LONGEVITY_MONTHLY_REWARD} BIO")
                        print(f"[LONGEVITY] {n.address[:16]}... +{LONGEVITY_MONTHLY_REWARD} BIO (monthly)")
        else:
            if n.died_at > 0 and (now - n.died_at) / 86400 >= DEATH_SWEEP_DAYS:
                bal = db.get_balance(n.address)
                if bal > 0 and db.debit(n.address, bal):
                    net.emission.pools["ecosystem"] += bal
                    n.balance = 0
                    db.save_node(n)
                    db.log("SWEEP_TO_POOL",
                           f"{n.address[:16]} {sat_to_bio(bal):.2f} BIO -> ecosystem (one year without rebirth)")
                    print(f"[SWEEP] {n.address[:16]}... {sat_to_bio(bal):.2f} BIO swept into the ecosystem pool")

    db.save_economy(net.eco, net.emission)

def _unstake_tick(now: float):
    """A single pass over pending unstake requests, using chain time (not wall clock -- same reason as everywhere else here)."""
    for row in db.get_unclaimed_unstakes():
        if (now - row["requested_at"]) >= UNSTAKE_COOLDOWN:
            amount_sat = int(row["bio_amount"])
            db.credit(row["address"], amount_sat)
            db.mark_unstake_claimed(row["id"])
            if row["address"] in net.nodes:
                net.nodes[row["address"]].balance = db.get_balance(row["address"])
                db.save_node(net.nodes[row["address"]])
            db.log("UNSTAKE_CLAIMED", f"{row['address'][:16]} +{sat_to_bio(amount_sat):.2f} BIO (cooldown complete)")
            print(f"[UNSTAKE] {row['address'][:16]}... +{sat_to_bio(amount_sat):.2f} BIO -- cooldown complete")


class TXBody(BaseModel):
    sender:    str
    receiver:  str
    value:     float
    pubkey:    str
    signature: str
    timestamp: float
    nonce:     int = 0

class BalanceBody(BaseModel):
    address: str

class SwapOfferBody(BaseModel):
    address:     str
    give_bio:    float = 0
    want_asset:  str   = ""
    want_amount: int   = 0
    ext_address: str   = ""
    ttl:         int   = 0
    cancel_offer_id: str = ""
    pubkey:      str
    signature:   str
    timestamp:   float
    nonce:       int = 0

class SwapLockBody(BaseModel):
    address:    str
    receiver:   str
    bio_amount: float
    hash_lock:  str
    timeout:    int
    pubkey:     str
    signature:  str
    timestamp:  float
    nonce:      int = 0

class SwapSettleBody(BaseModel):
    address:   str
    lock_id:   str
    preimage:  str = ""
    pubkey:    str
    signature: str
    timestamp: float
    nonce:     int = 0

class StakeBody(BaseModel):
    address:    str
    bio_amount: float
    pubkey:     str
    signature:  str
    timestamp:  float
    nonce:      int = 0

class RegisterBody(BaseModel):
    address:    str
    pubkey:     str
    signature:  str
    timestamp:  float
    nonce:      int = 0

class ClaimServerRewardBody(BaseModel):
    address:    str = ""
    url:        str = ""
    pubkey:     str = ""
    signature:  str = ""
    timestamp:  float = 0.0
    nonce:      int = 0

class UnstakeBody(BaseModel):
    address:    str
    bio_amount: float
    pubkey:     str
    signature:  str
    timestamp:  float
    nonce:      int = 0

class VoteBody(BaseModel):
    proposal_id: int
    voter:       str
    vote:        str
    pubkey:      str
    signature:   str
    timestamp:   float
    nonce:       int = 0

class LoanRequestBody(BaseModel):
    address:           str
    collateral_type:   str
    collateral_amount: float
    bio_requested:     float
    pubkey:            str
    signature:         str
    timestamp:         float
    nonce:             int = 0

class ProposalBody(BaseModel):
    title:         str
    description:   str = ""
    proposer:      str
    duration_days: int = 7
    param_key:     str = ""
    param_value:   str = ""
    pubkey:        str = ""
    signature:     str = ""
    timestamp:     float = 0.0
    nonce:         int = 0


@app.post("/tx")
def tx(body: TXBody):
    """Send an impulse. An address exists on its own -- no registration needed. After EMERGE_THRESHOLD impulses -- the address automatically becomes a node."""
    if not body.sender.startswith("BIO1"):
        return {"error": "Invalid sender address (must start with BIO1)"}
    if not body.receiver.startswith("BIO1"):
        return {"error": "Invalid receiver address"}
    if body.value <= 0:
        return {"error": "Value must be positive"}
    if body.sender == body.receiver:
        return {"error": "Sender and receiver are the same"}

    value_sat = bio_to_sat(body.value)
    message = signed_message("TRANSFER", sender=body.sender, receiver=body.receiver,
                             value=value_sat, signed_ts=body.timestamp, nonce=body.nonce)
    ok, err = verify_signed_request(body.sender, body.pubkey, body.signature, message, body.timestamp)
    if not ok:
        return {"error": f"Unauthorized: {err}"}

    if not rate_limiter.check(body.sender):
        return {"error": f"Rate limit exceeded: max {RATE_LIMIT_PER_MIN} transactions per minute"}

    block, reason = net.send(body.sender, body.receiver, value_sat, body.pubkey, body.signature, body.timestamp, nonce=body.nonce)

    tx_count = db.get_tx_count(body.sender)
    to_emerge = max(0, EMERGE_THRESHOLD - tx_count)
    sender_is_node = body.sender in net.nodes

    if not block:
        return {
            "status":     "pending",
            "reason":     reason,
            "tx_count":   tx_count,
            "to_emerge":  to_emerge,
            "is_node":    sender_is_node,
        }
    return {
        "status": "ok",
        "block": {
            "index":     block.index,
            "hash":      block.hash[:16],
            "validator": block.validator,
            "reward":    round(sat_to_bio(block.reward), 4),
            "fee":       sat_to_bio(transfer_fee(value_sat)),
            "mode":      "bootstrap" if block.validator == "NETWORK" else "consensus",
        },
        "sender": {
            "tx_count":  tx_count,
            "to_emerge": to_emerge,
            "is_node":   sender_is_node,
            "balance":   round(sat_to_bio(db.get_balance(body.sender)), 4),
        },
    }

@app.post("/balance")
def balance(body: BalanceBody):
    address  = body.address.strip()
    db.ensure_wallet(address)
    bal      = db.get_balance(address)
    tx_count = db.get_tx_count(address)
    is_node  = address in net.nodes
    wallet   = db.get_wallet(address)
    return {
        "address":    address,
        "balance":    round(sat_to_bio(bal), 4),
        "tx_count":   tx_count,
        "to_emerge":  max(0, EMERGE_THRESHOLD - tx_count),
        "is_node":    is_node,
        "node":       net.nodes[address].to_dict(net.eco.liquidity, net.eco.risk) if is_node else None,
        "genesis_got":bool(wallet["genesis_got"] if wallet else 0),
    }

@app.get("/nonce/{address}")
def get_nonce(address: str):
    """Current highest nonce this address has spent."""
    current = db.peek_nonce(address)
    return {"address": address, "nonce": current, "next": current + 1}

@app.get("/state")
def state():
    return net.state()

@app.get("/nodes")
def nodes():
    alive = [n for n in net.nodes_snapshot() if n.alive]
    dead  = [n for n in net.nodes_snapshot() if not n.alive]
    return {
        "alive": [n.to_dict(net.eco.liquidity, net.eco.risk) for n in
                  sorted(alive, key=lambda x: -x.weight(net.eco.liquidity, net.eco.risk))],
        "dead":  [n.to_dict(net.eco.liquidity, net.eco.risk) for n in dead],
        "emerge_threshold": EMERGE_THRESHOLD,
        "energy_per_impulse": ENERGY_PER_IMPULSE,
        "energy_decay_rate":  ENERGY_DECAY_RATE,
    }

@app.get("/biofield")
def biofield():
    alive = [n for n in net.nodes_snapshot() if n.alive]
    eco   = net.eco
    return {
        "biofield":          round(sum(n.energy for n in alive) * eco.stability(), 2),
        "phi_bio":           round(net.phi_bio(), 6),
        "stability":         round(eco.stability(), 6),
        "nodes_alive":       len(alive),
        "nodes_total":       len(net.nodes),
        "wallets_total":     db.count_wallets(),
        "blocks":            len(net.chain),
        "genesis_remaining": Emission.GENESIS_MAX_COUNT - net.emission.genesis_granted,
        "phase": (
            "GENESIS"   if len(net.chain) < 10  else
            "EXPANSION" if len(net.chain) < 30  else
            "RESONANCE" if len(net.chain) < 60  else
            "BLOOM"
        ),
    }

@app.get("/emission")
def emission():
    e = net.emission.state()
    e["circulating"] = round(
        sat_to_bio(sum(db.get_balance(n.address) for n in net.nodes_snapshot())), 2
    )
    return e

@app.get("/chain")
def chain():
    return net.chain_view()

@app.get("/events")
def events():
    rows = db.recent_events(30)
    return [
        {"t": round(r["timestamp"]), "type": r["type"], "msg": r["message"]}
        for r in rows
    ]

@app.get("/vesting")
def vesting():
    """Team vesting status"""
    state = net.vesting.state(net.chain_time())
    state["balance"] = round(sat_to_bio(db.get_balance(TEAM_ADDRESS)), 2)
    return state


SNAPSHOT_TABLES = [
    "address_nonces", "economy", "loans", "nodes", "param_overrides",
    "pending_unstakes", "proposals", "recognized_pairs", "stakes",
    "swap_locks", "swap_offers", "vesting", "votes", "wallets",
]

def _canonical_row(row: sqlite3.Row) -> dict:
    """Alphabetical-by-column-name dict, NULL as an explicit marker, never relying on physical column order."""
    d = {}
    for k in sorted(row.keys()):
        v = row[k]
        d[k] = None if v is None else v
    return d

def _table_natural_key(table: str) -> str:
    """Primary/natural key used to ORDER BY -- never insertion order (spec 4)."""
    return {
        "wallets": "address", "nodes": "address", "stakes": "address",
        "vesting": "address", "pending_unstakes": "address",
        "address_nonces": "address", "economy": "id",
        "param_overrides": "key", "loans": "id", "recognized_pairs": "id",
        "proposals": "id", "votes": "id", "swap_locks": "id",
        "swap_offers": "id",
    }[table]

def build_state_snapshot() -> dict:
    """The full canonical state at the CURRENT tip."""
    snap = {}
    with db.lock:
        for table in SNAPSHOT_TABLES:
            key = _table_natural_key(table)
            rows = db.conn.execute(f"SELECT * FROM {table} ORDER BY {key}").fetchall()
            snap[table] = [_canonical_row(r) for r in rows]
    return snap

def canonical_state_hash(snapshot: dict) -> str:
    """SHA-256 of the canonical JSON form: sorted keys, compact separators, no whitespace -- the ONE place this project's entire consensus-determinism discipline."""
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def write_snapshot_file(height: int, snapshot: dict) -> str:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(SNAPSHOT_DIR, f"state_{height}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, sort_keys=True, separators=(",", ":"))
    return path

def prune_old_snapshots(keep: int = STATE_SNAPSHOT_KEEP):
    if not os.path.isdir(SNAPSHOT_DIR):
        return
    files = sorted(
        (f for f in os.listdir(SNAPSHOT_DIR) if f.startswith("state_") and f.endswith(".json")),
        key=lambda f: int(f[len("state_"):-len(".json")]))
    for old in files[:-keep] if keep > 0 else files:
        try:
            os.remove(os.path.join(SNAPSHOT_DIR, old))
        except OSError:
            pass

def maybe_create_state_snapshot(height: int):
    """Called after a checkpoint is recorded."""
    if height <= 0 or height % STATE_SNAPSHOT_EVERY != 0:
        return
    snapshot = build_state_snapshot()
    state_hash = canonical_state_hash(snapshot)
    write_snapshot_file(height, snapshot)
    db.set_checkpoint_state_hash(height, state_hash)
    prune_old_snapshots()
    db.log("STATE_SNAPSHOT", f"height={height} hash={state_hash[:16]}")
    print(f"[SNAPSHOT] state snapshot written at block {height} | hash={state_hash[:16]}")

@app.get("/peer/snapshot/{height}")
def peer_snapshot(height: int):
    """Serve a previously written snapshot file plus the state_hash recorded in the checkpoints table -- the requester verifies against ITS OWN recomputation, not."""
    ckpt = db.get_checkpoint(height)
    if not ckpt or not ckpt["state_hash"]:
        return {"error": f"no state snapshot at height {height}"}
    path = os.path.join(SNAPSHOT_DIR, f"state_{height}.json")
    if not os.path.isfile(path):
        return {"error": f"snapshot file missing on disk for height {height}"}
    with open(path, "r", encoding="utf-8") as f:
        snapshot = json.load(f)
    return {"height": height, "state_hash": ckpt["state_hash"], "snapshot": snapshot}

@app.get("/verify")
def verify():
    if not net.chain:
        return {"valid": True, "message": "chain is empty", "blocks": 0}
    start = max(net._verified_up_to_index + 1, 1)
    for i in range(start, len(net.chain)):
        if net.chain[i].prev_hash != net.chain[i-1].hash:
            return {"valid": False, "message": f"block {i}: broken link"}
    net._verified_up_to_index = len(net.chain) - 1
    wallets_total = int(db.conn.execute(
        "SELECT COALESCE(SUM(balance),0) s FROM wallets").fetchone()["s"])
    locked_total = db.locked_total()
    staked_total = db.staked_total()
    pending_unstakes_total = db.pending_unstakes_total()
    grand_total = (wallets_total + sum(int(v) for v in net.emission.pools.values())
                   + locked_total + staked_total + pending_unstakes_total)
    max_supply_sat = Emission.MAX_SUPPLY * SAT_PER_BIO - net.emission.total_destroyed
    if grand_total != max_supply_sat:
        diff = grand_total - max_supply_sat
        return {"valid": False,
                "message": f"SUPPLY INVARIANT VIOLATED: wallets+pools+locked+staked+pending_unstakes = "
                           f"{sat_to_bio(grand_total):,.8f} BIO, expected {sat_to_bio(max_supply_sat):,.8f} "
                           f"BIO ({Emission.MAX_SUPPLY:,} cap minus {sat_to_bio(net.emission.total_destroyed):,.8f} "
                           f"destroyed) (diff: {sat_to_bio(diff):+,.8f})"}
    return {
        "valid":   True,
        "message": f"chain is valid ({len(net.chain)} blocks)",
        "blocks":  len(net.chain),
        "supply_check": f"{sat_to_bio(grand_total):,.4f} / {sat_to_bio(max_supply_sat):,.4f} BIO exact "
                        f"({sat_to_bio(net.emission.total_destroyed):,.4f} BIO permanently destroyed via fee burning)",
    }


class PeerBlockBody(BaseModel):
    index:          int
    hash:           str
    prev_hash:      str
    validator:      str
    reward:         float = 0.0
    timestamp:      float
    imp_id:         str
    imp_sender:     str
    imp_receiver:   str
    imp_value:      float
    imp_energy:     float
    imp_phi_bio:    float
    imp_pubkey:     str = ""
    imp_signature:  str = ""
    imp_signed_ts:  float = 0.0
    imp_kind:       str = "TRANSFER"
    imp_payload:    str = ""
    imp_nonce:      int = 0

@app.get("/peer/chain_info")
def peer_chain_info():
    """Lightweight check so a peer can tell, without downloading anything, whether its own chain is shorter/longer/different from ours."""
    return {
        "chain_len":    len(net.chain),
        "latest_hash":  net.chain[-1].hash if net.chain else "0" * 64,
        "genesis_hash": net.chain[0].hash if net.chain else "",
        "instance_id":  INSTANCE_ID,
    }

@app.get("/peer/known_nodes")
def peer_known_nodes():
    """discovery spec  section 4.1."""
    candidates = db.list_node_candidates(min_confirmations=0)
    return {
        "trusted_peers": list(PEER_URLS),
        "candidates": [
            {"url": c["url"], "confirmations": c["confirmations"],
             "first_seen_at": c["first_seen_at"], "last_confirmed_at": c["last_confirmed_at"]}
            for c in candidates
        ],
    }

class AnnounceBody(BaseModel):
    url: str

@app.post("/peer/announce")
def peer_announce(body: AnnounceBody):
    """self-announcement -- the missing piece from Bitcoin's addr messages / Ethereum's FINDNODE (see discovery spec addendum): a brand-new node, not yet known to."""
    url = body.url.strip().rstrip("/")
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"error": "url must start with http:// or https://"}
    if url == SELF_URL or url in PEER_URLS:
        return {"error": "already known to this node"}

    if not HTTP_OK:
        return {"error": "this node cannot make outbound requests to verify liveness"}
    try:
        resp = http_requests.get(f"{url}/peer/chain_info",
                                 timeout=PEER_REQUEST_TIMEOUT_SECONDS).json()
        if "chain_len" not in resp or "latest_hash" not in resp:
            return {"error": "url did not respond like a BioChain node"}
    except Exception as e:
        return {"error": f"liveness check failed: {e}"}

    if resp.get("instance_id") == INSTANCE_ID:
        return {"error": "cannot announce this node's own URL to itself"}

    db.note_self_announcement(url)


    return {"status": "ok", "message": "recorded as a candidate -- promotion still "
                                        "requires independent confirmation from trusted peers"}

@app.get("/peer/chain")
def peer_chain(from_block: int = 0):
    """Full block data (including sender signatures) from `from_block` onwards -- everything another server needs to independently verify and replay these blocks."""
    out = []
    for b in net.chain[from_block:]:
        out.append(Network.block_to_peer_dict(b))
    return {"blocks": out, "chain_len": len(net.chain)}

@app.post("/peer/block")
def peer_block(body: PeerBlockBody):
    """Receives a new block from a peer."""
    ok, reason = net.apply_peer_block(body.model_dump())
    if ok:
        return {"status": "ok", "chain_len": len(net.chain)}
    return {"status": "rejected", "reason": reason}

@app.get("/db")
def db_status():
    return {
        "path":     DB_PATH,
        "size_kb":  db.size_kb(),
        "wallets":  db.count_wallets(),
        "nodes":    len(net.nodes),
        "events":   [{"t": round(e["timestamp"]), "type": e["type"], "msg": e["message"]}
                     for e in db.recent_events(10)],
    }


@app.post("/stake")
def stake(body: StakeBody):
    """Stake BIO to obtain a validator tier."""
    address = body.address.strip()
    if not address.startswith("BIO1"):
        return {"error": "Invalid address"}
    if body.bio_amount <= 0:
        return {"error": "Amount must be positive"}

    amount_sat = bio_to_sat(body.bio_amount)
    message = signed_message("STAKE", sender=address, value=amount_sat,
                             signed_ts=body.timestamp, nonce=body.nonce)
    ok, err = verify_signed_request(address, body.pubkey, body.signature, message, body.timestamp)
    if not ok:
        return {"error": f"Unauthorized: {err}"}


    block, reason = net.send(address, address, amount_sat, body.pubkey, body.signature, body.timestamp, kind="STAKE", nonce=body.nonce)
    if not block:
        return {"error": reason}

    row        = db.get_stake(address)
    total_staked = int(row["bio_amount"]) if row else amount_sat
    tier         = row["tier"] if row else get_tier(amount_sat)
    tier_info    = STAKE_TIERS[tier]
    return {
        "status":       "ok",
        "address":      address,
        "bio_staked":   sat_to_bio(amount_sat),
        "total_staked": sat_to_bio(total_staked),
        "tier":         tier,
        "tier_label":   tier_info["label"],
        "reward_mult":  tier_info["reward_mult"],
        "weight_mult":  tier_info["weight_mult"],
        "block_index":  block.index,
        "message":      f"Tier: {tier_info['label']} | Reward x{tier_info['reward_mult']}",
    }

@app.post("/register")
def register(body: RegisterBody):
    """one-time wallet-registration grant for the first WALLET_REGISTRATION_MAX_COUNT (100) addresses ever to call this."""
    address = body.address.strip()
    if not address.startswith("BIO1"):
        return {"error": "Invalid address"}

    message = signed_message("REGISTER", sender=address, value=0,
                             signed_ts=body.timestamp, nonce=body.nonce)
    ok, err = verify_signed_request(address, body.pubkey, body.signature, message, body.timestamp)
    if not ok:
        return {"error": f"Unauthorized: {err}"}

    block, reason = net.send(address, address, 0, body.pubkey, body.signature,
                             body.timestamp, kind="REGISTER", nonce=body.nonce)
    if not block:
        return {"error": reason}

    return {
        "status":  "ok",
        "address": address,
        "granted": sat_to_bio(WALLET_REGISTRATION_GRANT),
        "slot":    db.registration_granted_count(),
        "max":     WALLET_REGISTRATION_MAX_COUNT,
        "block_index": block.index,
    }

@app.post("/claim_server_reward")
def claim_server_reward(body: ClaimServerRewardBody):
    """No longer supported -- server rewards are paid through PROPOSAL/VOTE governance."""
    return {"error": "no longer supported -- submit a server_reward governance "
                      "PROPOSAL instead (see POST /proposal)"}

@app.post("/unstake")
def unstake(body: UnstakeBody):
    """Requests withdrawal of staked BIO."""
    address = body.address.strip()
    if not address.startswith("BIO1"):
        return {"error": "Invalid address"}
    if body.bio_amount <= 0:
        return {"error": "Amount must be positive"}

    amount_sat = bio_to_sat(body.bio_amount)
    message = signed_message("UNSTAKE", sender=address, value=amount_sat,
                             signed_ts=body.timestamp, nonce=body.nonce)
    ok, err = verify_signed_request(address, body.pubkey, body.signature, message, body.timestamp)
    if not ok:
        return {"error": f"Unauthorized: {err}"}


    block, reason = net.send(address, address, amount_sat, body.pubkey, body.signature, body.timestamp, kind="UNSTAKE", nonce=body.nonce)
    if not block:
        return {"error": reason}

    row       = db.get_stake(address)
    remaining = int(row["bio_amount"]) if row else 0
    new_tier  = row["tier"] if row else "NONE"
    return {
        "status":            "ok",
        "address":           address,
        "unstaked":          sat_to_bio(amount_sat),
        "remaining_staked":  sat_to_bio(remaining),
        "new_tier":          new_tier,
        "cooldown_days":     UNSTAKE_COOLDOWN // 86400,
        "block_index":       block.index,
        "message":           f"BIO will be spendable again in {UNSTAKE_COOLDOWN//86400:.0f} days",
    }

@app.post("/loan/request")
def loan_request(body: LoanRequestBody):
    """Scaffolding for credit against external collateral (BTC/ETH) -- deliberately NOT functional yet."""
    address = body.address.strip()
    coll_sat = bio_to_sat(body.collateral_amount)
    req_sat  = bio_to_sat(body.bio_requested)
    message = (f"LOAN|{address}|{body.collateral_type}|{sat_to_str8(coll_sat)}"
               f"|{sat_to_str8(req_sat)}|{body.timestamp:.6f}")
    ok, err = verify_signed_request(address, body.pubkey, body.signature, message, body.timestamp)
    if not ok:
        return {"error": f"Unauthorized: {err}"}

    if body.collateral_type not in ("BTC", "ETH"):
        return {"error": "Unsupported collateral_type -- only BTC/ETH are planned"}

    return {
        "status": "unavailable",
        "reason": ("Credit against external collateral is not active yet -- it needs "
                   "either custody of real BTC/ETH or a bridge, both deliberately "
                   "deferred. No loan has been created and no BIO has moved."),
    }

@app.get("/unstake")
def get_pending_unstakes(address: str = ""):
    """Pending unstake requests -- optionally filtered to one address."""
    rows = db.get_pending_unstakes_for(address) if address else db.get_unclaimed_unstakes()
    now = net.chain_time()
    return {
        "pending": [
            {
                "address":       r["address"],
                "bio_amount":    sat_to_bio(int(r["bio_amount"])),
                "requested_at":  round(r["requested_at"]),
                "days_left":     max(0, round(UNSTAKE_COOLDOWN/86400 - (now - r["requested_at"])/86400, 2)),
            }
            for r in rows
        ]
    }

@app.get("/stake")
def get_stakes():
    """All validators with BIO stakes"""
    rows = db.get_all_stakes()
    return {
        "stakes": [
            {
                "address":    r["address"],
                "bio_staked": sat_to_bio(int(r["bio_amount"])),
                "tier":       r["tier"],
                "label":      STAKE_TIERS.get(r["tier"], STAKE_TIERS["NONE"])["label"],
                "staked_at":  round(r["staked_at"]),
                "slashed":    sat_to_bio(int(r["slashed"])),
            }
            for r in rows
        ],
        "tiers": {k: {"min_bio": sat_to_bio(v["min_bio"]), "label": v["label"],
                      "reward_mult": v["reward_mult"]} for k, v in STAKE_TIERS.items()},
    }


@app.post("/swap/offer")
def swap_offer(body: SwapOfferBody):
    """Publish (or cancel) an order-board entry."""
    address = body.address.strip()
    if not address.startswith("BIO1"):
        return {"error": "Invalid address"}
    if body.cancel_offer_id:
        payload = json.dumps({"cancel_offer_id": body.cancel_offer_id})
    else:
        payload = json.dumps({
            "give_bio":    bio_to_sat(body.give_bio),
            "want_asset":  body.want_asset.strip(),
            "want_amount": int(body.want_amount),
            "ext_address": body.ext_address.strip(),
            "ttl":         int(body.ttl),
        })
    message = signed_message("SWAP_OFFER", sender=address,
                             signed_ts=body.timestamp, nonce=body.nonce, payload=payload)
    ok, err = verify_signed_request(address, body.pubkey, body.signature, message, body.timestamp)
    if not ok:
        return {"error": f"Unauthorized: {err}"}
    block, reason = net.send(address, address, 0, body.pubkey, body.signature,
                             body.timestamp, kind="SWAP_OFFER", payload=payload, nonce=body.nonce)
    if not block:
        return {"error": reason}
    return {"status": "ok", "offer_id": block.impulse.id if not body.cancel_offer_id else None,
            "cancelled": body.cancel_offer_id or None, "block": block.index}

@app.post("/swap/lock")
def swap_lock(body: SwapLockBody):
    """Lock BIO under a SHA-256 hash-lock for a specific counterparty."""
    address = body.address.strip()
    if not address.startswith("BIO1"):
        return {"error": "Invalid address"}
    value_sat = bio_to_sat(body.bio_amount)
    payload = json.dumps({"hash_lock": body.hash_lock.strip().lower(),
                          "timeout": int(body.timeout)})
    message = signed_message("SWAP_LOCK", sender=address, receiver=body.receiver.strip(),
                             value=value_sat, signed_ts=body.timestamp,
                             nonce=body.nonce, payload=payload)
    ok, err = verify_signed_request(address, body.pubkey, body.signature, message, body.timestamp)
    if not ok:
        return {"error": f"Unauthorized: {err}"}
    block, reason = net.send(address, body.receiver.strip(), value_sat, body.pubkey,
                             body.signature, body.timestamp, kind="SWAP_LOCK",
                             payload=payload, nonce=body.nonce)
    if not block:
        return {"error": reason}
    return {"status": "ok", "lock_id": block.impulse.id, "block": block.index,
            "locked_bio": sat_to_bio(value_sat),
            "fee": sat_to_bio(transfer_fee(value_sat)),
            "expires_at": block.t + int(body.timeout)}

def _swap_settle(body: SwapSettleBody, kind: str):
    address = body.address.strip()
    if not address.startswith("BIO1"):
        return {"error": "Invalid address"}
    d = {"lock_id": body.lock_id.strip()}
    if kind == "SWAP_CLAIM":
        d["preimage"] = body.preimage.strip().lower()
    payload = json.dumps(d)
    message = signed_message(kind, sender=address, signed_ts=body.timestamp,
                             nonce=body.nonce, payload=payload)
    ok, err = verify_signed_request(address, body.pubkey, body.signature, message, body.timestamp)
    if not ok:
        return {"error": f"Unauthorized: {err}"}
    block, reason = net.send(address, address, 0, body.pubkey, body.signature,
                             body.timestamp, kind=kind, payload=payload, nonce=body.nonce)
    if not block:
        return {"error": reason}
    lock = db.get_swap_lock(body.lock_id.strip())
    return {"status": "ok", "lock_id": lock["id"], "state": lock["state"],
            "amount_bio": sat_to_bio(int(lock["amount"])), "block": block.index}

@app.post("/swap/claim")
def swap_claim(body: SwapSettleBody):
    """Claim a lock by revealing the preimage -- the revelation IS the atomicity mechanism: once public, the counterparty uses it on the Bitcoin side."""
    return _swap_settle(body, "SWAP_CLAIM")

@app.post("/swap/refund")
def swap_refund(body: SwapSettleBody):
    """Return locked BIO to their owner after the timeout has passed (chain-time, deterministic)."""
    return _swap_settle(body, "SWAP_REFUND")

@app.get("/swaps/offers")
def swaps_offers():
    """The order board: ACTIVE, unexpired offers -- computed against chain time so every node answers identically."""
    now = net.chain_time()
    rows = db.active_swap_offers(now)
    return {"offers": [
        {"offer_id":    r["id"],
         "sender":      r["sender"],
         "give_bio":    sat_to_bio(int(r["give_amount"])),
         "want_asset":  r["want_asset"],
         "want_amount": int(r["want_amount"]),
         "ext_address": r["ext_address"],
         "expires_in":  max(0, int(r["created_t"] + r["ttl"] - now)),
        } for r in rows]}

@app.get("/swaps/locks")
def swaps_locks(address: str = ""):
    """Locks (optionally filtered by participant) with live states -- the wallet's MY DEALS view reads this."""
    now = net.chain_time()
    with db.lock:
        if address:
            rows = db.conn.execute(
                "SELECT * FROM swap_locks WHERE sender=? OR receiver=? ORDER BY created_t DESC",
                (address, address)).fetchall()
        else:
            rows = db.conn.execute("SELECT * FROM swap_locks ORDER BY created_t DESC LIMIT 100").fetchall()
    return {"locks": [
        {"lock_id":    r["id"],
         "sender":     r["sender"],
         "receiver":   r["receiver"],
         "amount_bio": sat_to_bio(int(r["amount"])),
         "hash_lock":  r["hash_lock"],
         "state":      r["state"],
         "preimage":   r["preimage"] or None,
         "expires_in": max(0, int(r["created_t"] + r["timeout"] - now)) if r["state"] == "LOCKED" else 0,
        } for r in rows]}


@app.post("/proposals")
def create_proposal(body: ProposalBody):
    """Create a proposal for voting."""
    address = body.proposer.strip()
    if not address.startswith("BIO1"):
        return {"error": "Invalid address"}

    payload = json.dumps({
        "title": body.title, "description": body.description,
        "param_key": body.param_key, "param_value": body.param_value,
        "duration_days": body.duration_days,
    })
    message = signed_message("PROPOSAL", sender=address, signed_ts=body.timestamp,
                             nonce=body.nonce, payload=payload)
    ok, err = verify_signed_request(address, body.pubkey, body.signature, message, body.timestamp)
    if not ok:
        return {"error": f"Unauthorized: {err}"}


    block, reason = net.send(address, address, 0.0, body.pubkey, body.signature, body.timestamp, kind="PROPOSAL", payload=payload, nonce=body.nonce)
    if not block:
        return {"error": reason}

    rows = [r for r in db.get_proposals() if r["proposer"] == address]
    pid  = max((r["id"] for r in rows), default=None)
    return {
        "status":        "ok",
        "proposal_id":   pid,
        "title":         body.title,
        "duration_days": body.duration_days,
        "block_index":   block.index,
    }

@app.get("/proposals")
def proposals():
    """List of all proposals."""
    rows = db.get_proposals()
    now = time.time()
    result = []
    for r in rows:
        votes_total = r["votes_for"] + r["votes_against"]
        pct_for     = round(r["votes_for"] / votes_total * 100, 1) if votes_total > 0 else 0.0
        result.append({
            "id":              r["id"],
            "title":           r["title"],
            "description":     r["description"],
            "proposer":        r["proposer"],
            "status":          r["status"],
            "votes_for":       round(r["votes_for"], 3),
            "votes_against":   round(r["votes_against"], 3),
            "pct_for":         pct_for,
            "ends_at":         round(r["ends_at"]),
            "apply_at":        round(r["apply_at"]),
            "timelock_days_left": round(max(0.0, (r["apply_at"] - now) / 86400), 1),
            "param_key":       r["param_key"],
            "param_value":     r["param_value"],
        })
    return result

@app.post("/vote")
def vote(body: VoteBody):
    """Vote on a proposal. Now a real chain event (kind "VOTE") -- signed, mined into a block, peer-verifiable -- the same way a transfer or a stake already is."""
    voter = body.voter.strip()
    if body.vote not in ("FOR", "AGAINST"):
        return {"error": "vote must be FOR or AGAINST"}

    payload = json.dumps({"proposal_id": body.proposal_id, "vote": body.vote})
    message = signed_message("VOTE", sender=voter, signed_ts=body.timestamp,
                             nonce=body.nonce, payload=payload)
    sig_ok, sig_err = verify_signed_request(voter, body.pubkey, body.signature, message, body.timestamp)
    if not sig_ok:
        return {"error": f"Unauthorized: {sig_err}"}


    block, reason = net.send(voter, voter, 0.0, body.pubkey, body.signature, body.timestamp, kind="VOTE", payload=payload, nonce=body.nonce)
    if not block:
        return {"error": reason}

    return {
        "status":      "ok",
        "voter":       voter,
        "vote":        body.vote,
        "weight":      1.0,
        "proposal_id": body.proposal_id,
        "block_index": block.index,
    }

@app.get("/recognized_pairs")
def recognized_pairs():
    """Exchanges/pairs the network has officially recognized via governance vote -- a trust signal, not a technical permission."""
    rows = db.get_recognized_pairs()
    return {
        "recognized": [
            {
                "exchange_name":   r["exchange_name"],
                "pair_identifier": r["pair_identifier"],
                "recognized_at":   round(r["recognized_at"]),
                "proposal_id":     r["proposal_id"],
            }
            for r in rows
        ]
    }

@app.get("/governance/params")
def governance_params():
    """Transparency: which parameters are governable by vote, their bounds and current values"""
    return {
        "governance_threshold":      GOVERNANCE_THRESHOLD,
        "governance_timelock_days":  GOVERNANCE_TIMELOCK / 86400,
        "immutable": ["max_supply", "governance_threshold", "governance_timelock"],
        "governable": {
            key: {
                "current": _current_param_value(key),
                "min":     spec["min"],
                "max":     spec["max"],
            }
            for key, spec in GOVERNABLE_PARAMS.items()
        },
        "actions": {
            "slash": {
                "description": "Slash a validator's BIO stake -- the only way, there is no direct endpoint",
                "param_value_format": '{"address":"BIO1...","amount":500,"reason":"..."}',
            },
        },
    }

@app.get("/longevity")
def longevity():
    """Transparency: longevity reward schedule and the current ecosystem pool level"""
    alive = [n for n in net.nodes_snapshot() if n.alive]
    dead_waiting = [
        n for n in net.nodes_snapshot()
        if not n.alive and n.died_at > 0 and db.get_balance(n.address) > 0
    ]
    return {
        "schedule": {
            "6_months":      f"{LONGEVITY_6MO_REWARD} BIO one-time",
            "12_months":      f"{LONGEVITY_12MO_REWARD} BIO one-time",
            "monthly_after_year": f"{LONGEVITY_MONTHLY_REWARD} BIO/month while alive and while the pool lasts",
            "death_sweep":    f"a dead node's balance flows into the pool after {DEATH_SWEEP_DAYS:.0f} days without rebirth",
        },
        "pool_ecosystem_remaining": round(sat_to_bio(net.emission.pools["ecosystem"]), 2),
        "nodes_earning": [
            {
                "address":     n.address,
                "days_alive":  round((time.time() - n.born_at) / 86400, 1),
                "6mo_paid":    n.longevity_6mo_paid,
                "12mo_paid":   n.longevity_12mo_paid,
            }
            for n in alive
        ],
        "dead_awaiting_sweep": [
            {
                "address":      n.address,
                "balance":      round(sat_to_bio(db.get_balance(n.address)), 2),
                "days_since_death": round((time.time() - n.died_at) / 86400, 1),
                "days_until_sweep": round(DEATH_SWEEP_DAYS - (time.time() - n.died_at) / 86400, 1),
            }
            for n in dead_waiting
        ],
    }


def _apply_slash(address: str, amount: float, reason: str = ""):
    """Actually slashes the stake."""
    stake_row = db.get_stake(address)
    if not stake_row:
        return False, f"{address[:16]} has no stake"
    old_bio = int(stake_row["bio_amount"])
    db.slash_stake(address, amount)
    new_stake = db.get_stake(address)
    new_bio   = int(new_stake["bio_amount"]) if new_stake else 0
    new_tier = get_tier(new_bio)
    db.update_stake_tier(address, new_tier)
    db.log("SLASH", f"{address[:16]} -{sat_to_bio(amount)} BIO | reason: {reason} | via governance")
    print(f"[SLASH] {address[:16]}... -{sat_to_bio(amount)} BIO ({sat_to_bio(old_bio):.2f}->{sat_to_bio(new_bio):.2f}) | {reason}")
    return True, f"{address[:16]} -{sat_to_bio(amount)} BIO (tier: {new_tier})"

def _apply_listing_reward(address: str, exchange_name: str = "", pair_identifier: str = "", proposal_id: int = 0, amount_sat: int = None):
    """Pays the VOTED amount (chosen per-proposal, clamped in apply_governance_param to 1..LISTING_REWARD BIO) from its own protected pool, AND records the."""
    if amount_sat is None:
        amount_sat = LISTING_REWARD
    amount_sat = int(amount_sat)
    if net.emission.pools["listing_reserve"] < amount_sat:
        return False, (f"listing_reserve exhausted ({sat_to_bio(net.emission.pools['listing_reserve']):.2f} BIO "
                        f"left, needs {sat_to_bio(amount_sat):.2f})")
    db.ensure_wallet(address)
    db.credit(address, amount_sat)
    net.emission.pools["listing_reserve"] -= amount_sat
    net.emission.minted                  += amount_sat
    if address in net.nodes:
        net.nodes[address].balance = db.get_balance(address)
        db.save_node(net.nodes[address])
    db.save_economy(net.eco, net.emission)
    db.add_recognized_pair(exchange_name, pair_identifier, net.chain_time(), proposal_id)
    db.log("LISTING_REWARD", f"{address[:16]} +{sat_to_bio(amount_sat)} BIO | exchange: {exchange_name} | pair: {pair_identifier} | via governance")
    print(f"[LISTING] {address[:16]}... +{sat_to_bio(amount_sat)} BIO -- listing confirmed: {exchange_name} ({pair_identifier})")
    return True, (f"{address[:16]} +{sat_to_bio(amount_sat)} BIO, pair recognized: {exchange_name} ({pair_identifier}) "
                  f"(listing_reserve left: {sat_to_bio(net.emission.pools['listing_reserve']):.2f})")

def _apply_developer_grant(address: str, project_name: str = "", project_description: str = "", proposal_id: int = 0, amount_sat: int = None):
    """Same pattern as _apply_listing_reward -- voted amount, governance-only, no HTTP access."""
    if amount_sat is None:
        amount_sat = DEVELOPER_GRANT_MAX
    amount_sat = int(amount_sat)
    if net.emission.pools["developer_grants"] < amount_sat:
        return False, (f"developer_grants pool exhausted ({sat_to_bio(net.emission.pools['developer_grants']):.2f} BIO "
                        f"left, needs {sat_to_bio(amount_sat):.2f})")
    db.ensure_wallet(address)
    db.credit(address, amount_sat)
    net.emission.pools["developer_grants"] -= amount_sat
    net.emission.minted                    += amount_sat
    if address in net.nodes:
        net.nodes[address].balance = db.get_balance(address)
        db.save_node(net.nodes[address])
    db.save_economy(net.eco, net.emission)
    db.add_developer_grant(address, project_name, project_description, amount_sat, net.chain_time(), proposal_id)
    db.log("DEVELOPER_GRANT", f"{address[:16]} +{sat_to_bio(amount_sat)} BIO | project: {project_name} | via governance")
    print(f"[DEV_GRANT] {address[:16]}... +{sat_to_bio(amount_sat)} BIO -- {project_name}")
    return True, (f"{address[:16]} +{sat_to_bio(amount_sat)} BIO, project: {project_name} "
                  f"(developer_grants left: {sat_to_bio(net.emission.pools['developer_grants']):.2f})")

def _apply_server_reward(address: str, url: str = "", proposal_id: int = 0, amount_sat: int = None):
    """same pattern as _apply_developer_grant -- voted amount, governance-only."""
    if amount_sat is None:
        amount_sat = SERVER_REWARD_MAX
    amount_sat = int(amount_sat)
    if url:
        already = db.conn.execute(
            "SELECT 1 FROM server_rewards_paid WHERE url=?", (url,)).fetchone()
        if already:
            return False, f"server reward already paid for url: {url}"
    if net.emission.pools.get("server_rewards", 0) < amount_sat:
        return False, (f"server_rewards pool exhausted ({sat_to_bio(net.emission.pools.get('server_rewards', 0)):.2f} BIO "
                        f"left, needs {sat_to_bio(amount_sat):.2f})")
    db.ensure_wallet(address)
    db.credit(address, amount_sat)
    net.emission.pools["server_rewards"] -= amount_sat
    net.emission.minted                  += amount_sat
    if address in net.nodes:
        net.nodes[address].balance = db.get_balance(address)
        db.save_node(net.nodes[address])
    db.save_economy(net.eco, net.emission)
    if url:
        db.conn.execute(
            "INSERT OR IGNORE INTO server_rewards_paid (url, address, amount, paid_at) VALUES (?,?,?,?)",
            (url, address, amount_sat, net.chain_time()))
        db.conn.commit()
    db.log("SERVER_REWARD_PAID", f"{address[:16]} +{sat_to_bio(amount_sat)} BIO -- {url} | via governance")
    print(f"[SERVER_REWARD] {address[:16]}... +{sat_to_bio(amount_sat)} BIO -- {url}")
    return True, (f"{address[:16]} +{sat_to_bio(amount_sat)} BIO, url: {url} "
                  f"(server_rewards left: {sat_to_bio(net.emission.pools['server_rewards']):.2f})")


@app.get("/supply")
def supply():
    """Full information on emission and circulation"""
    em = net.emission
    all_balances = sum(
        db.get_balance(n.address) for n in net.nodes_snapshot()
    )
    return {
        "max_supply":       em.MAX_SUPPLY,
        "minted":           round(sat_to_bio(em.minted), 2),
        "burned":           round(sat_to_bio(em.burned), 6),
        "circulating":      round(sat_to_bio(all_balances), 2),
        "in_pools":         round(sat_to_bio(sum(em.pools.values())), 2),
        "halvings":         em.halvings,
        "block_reward":     round(sat_to_bio(em.block_reward(time.time())), 4),
        "burn_rate":        f"{em.BURN_RATE * 100:.4f}%",
        "pools":            {k: round(sat_to_bio(v), 2) for k, v in em.pools.items()},
        "genesis_granted":  em.genesis_granted,
        "genesis_remaining":em.GENESIS_MAX_COUNT - em.genesis_granted,
    }


def _concentration(values: list) -> dict:
    """What share of the total is held by the top 1 / 5 / 10 addresses."""
    total = sum(values)
    if total <= 0 or not values:
        return {"top1_pct": 0.0, "top5_pct": 0.0, "top10_pct": 0.0}
    ordered = sorted(values, reverse=True)
    def pct(n):
        return round(100.0 * sum(ordered[:n]) / total, 2)
    return {"top1_pct": pct(1), "top5_pct": pct(5), "top10_pct": pct(10)}

def _synchronized_birth_clusters(nodes: list, window_seconds: int = 300, min_cluster: int = 3) -> list:
    """Groups of nodes born within the same short time window -- a weak, honest signal."""
    if not nodes:
        return []
    times = sorted(n.born_at for n in nodes)
    clusters, cur = [], [times[0]]
    for t in times[1:]:
        if t - cur[-1] <= window_seconds:
            cur.append(t)
        else:
            if len(cur) >= min_cluster:
                clusters.append({"count": len(cur), "start": round(cur[0]), "end": round(cur[-1])})
            cur = [t]
    if len(cur) >= min_cluster:
        clusters.append({"count": len(cur), "start": round(cur[0]), "end": round(cur[-1])})
    return clusters

@app.get("/dashboard")
def dashboard():
    """Public transparency metrics for the wallet's NETWORK screen and for anyone auditing decentralization health from the outside."""
    all_nodes = net.nodes_snapshot()
    alive = [n for n in all_nodes if n.alive]
    dead  = [n for n in all_nodes if not n.alive]

    tier_dist = {"NONE": 0, "VALIDATOR": 0, "SENIOR": 0, "ANCHOR": 0}
    role_dist = {}
    balances, stakes = [], []
    for n in alive:
        stake_row = db.get_stake(n.address)
        tier = stake_row["tier"] if stake_row else "NONE"
        tier_dist[tier] = tier_dist.get(tier, 0) + 1
        role_dist[n.role] = role_dist.get(n.role, 0) + 1
        balances.append(sat_to_bio(n.balance))
        stakes.append(sat_to_bio(int(stake_row["bio_amount"])) if stake_row else 0.0)

    return {
        "node_count": {"alive": len(alive), "dead": len(dead), "total": len(all_nodes)},
        "tier_distribution": tier_dist,
        "role_distribution": role_dist,
        "balance_concentration": _concentration(balances),
        "stake_concentration": _concentration(stakes),
        "synchronized_birth_clusters": _synchronized_birth_clusters(alive),
        "limitations": (
            "No IP-based farm detection: BioChain does not log requester "
            "IPs anywhere in its architecture. Concentration figures are "
            "an economic proxy (whale risk), not identity verification."
        ),
    }

@app.get("/validators")
def validators():
    """All validators with tiers and stakes"""
    alive = [n for n in net.nodes_snapshot() if n.alive]
    dead  = [n for n in net.nodes_snapshot() if not n.alive]
    result = []
    for n in sorted(alive, key=lambda x: -x.weight(net.eco.liquidity, net.eco.risk)):
        stake_row  = db.get_stake(n.address)
        tier       = stake_row["tier"] if stake_row else "NONE"
        bio_staked = int(stake_row["bio_amount"]) if stake_row else 0
        result.append({
            "address":    n.address,
            "role":       n.role,
            "tier":       tier,
            "tier_label": STAKE_TIERS.get(tier, STAKE_TIERS["NONE"])["label"],
            "bio_staked": sat_to_bio(bio_staked),
            "balance":    round(sat_to_bio(n.balance), 2),
            "weight":     round(n.weight(net.eco.liquidity, net.eco.risk), 2),
            "reputation": round(n.reputation, 3),
            "alive":      n.alive,
        })
    return {
        "alive":   result,
        "dead":    [n.address for n in dead],
        "total":   len(net.nodes),
        "tiers":   STAKE_TIERS,
    }


@app.get("/checkpoints")
def checkpoints():
    """List of all network checkpoints"""
    rows = db.get_all_checkpoints()
    last = db.get_last_checkpoint()
    return {
        "checkpoints": [
            {
                "block_idx":   r["block_idx"],
                "block_hash":  r["block_hash"][:16],
                "created_at":  round(r["created_at"]),
                "nodes_alive": r["nodes_alive"],
            }
            for r in rows
        ],
        "last_checkpoint": last["block_idx"] if last else 0,
        "next_checkpoint": ((last["block_idx"] // CHECKPOINT_EVERY) + 1) * CHECKPOINT_EVERY if last else CHECKPOINT_EVERY,
        "checkpoint_every": CHECKPOINT_EVERY,
    }

SNAPSHOT_COOLDOWN_SECONDS = 300
SNAPSHOT_MAX_FILES        = 20
_last_snapshot_time = 0.0

@app.post("/save")
def save_snapshot():
    """Save a snapshot of network state to a file."""
    global _last_snapshot_time
    import json, glob
    now = time.time()
    if now - _last_snapshot_time < SNAPSHOT_COOLDOWN_SECONDS:
        wait = SNAPSHOT_COOLDOWN_SECONDS - (now - _last_snapshot_time)
        return {"error": f"snapshot saved too recently, try again in {wait:.0f}s"}
    _last_snapshot_time = now

    snapshot = {
        "version":    "5.3",
        "timestamp":  now,
        "chain_len":  len(net.chain),
        "nodes":      len(net.nodes),
        "economy":    net.eco.state(),
        "emission":   net.emission.state(),
    }
    fname = f"snapshot_{int(now)}.json"
    with open(fname, "w") as f:
        json.dump(snapshot, f, indent=2)
    db.log("SNAPSHOT_SAVED", fname)

    existing = sorted(glob.glob("snapshot_*.json"))
    for old in existing[:-SNAPSHOT_MAX_FILES]:
        try:
            os.remove(old)
        except OSError:
            pass

    return {
        "status":   "ok",
        "file":     fname,
        "chain_len":len(net.chain),
        "nodes":    len(net.nodes),
    }

@app.post("/load")
def load_snapshot():
    """Restore from the latest snapshot (metadata only)"""
    import glob, json
    files = sorted(glob.glob("snapshot_*.json"), reverse=True)
    if not files:
        return {"error": "No snapshots found"}
    with open(files[0]) as f:
        data = json.load(f)
    return {
        "status":    "ok",
        "loaded":    files[0],
        "saved_at":  round(data.get("timestamp", 0)),
        "chain_len": data.get("chain_len", 0),
        "nodes":     data.get("nodes", 0),
        "note":      "The database already holds full state -- snapshot is for auditing",
    }

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        while True:
            msg   = await ws.receive_text()
            data  = json.loads(msg)
            block = None
            reason = "no action"

            if data.get("type") == "tx":
                sender    = data.get("from","")
                receiver  = data.get("to","")
                value     = bio_to_sat(data.get("value", 0))
                ts        = float(data.get("timestamp", 0))
                pubkey    = data.get("pubkey","")
                signature = data.get("signature","")
                nonce     = int(data.get("nonce", 0))
                ws_msg = signed_message("TRANSFER", sender=sender, receiver=receiver,
                                        value=value, signed_ts=ts, nonce=nonce)
                ok, err = verify_signed_request(sender, pubkey, signature, ws_msg, ts)
                if not ok:
                    reason = f"Unauthorized: {err}"
                else:
                    block, reason = net.send(sender, receiver, value, pubkey, signature, ts, nonce=nonce)

            alive = [n for n in net.nodes_snapshot() if n.alive]
            payload = {
                "state": {
                    "nodes_alive": len(alive),
                    "nodes_total": len(net.nodes),
                    "wallets":     db.count_wallets(),
                    "chain":       len(net.chain),
                    "mempool":     len(net.mempool),
                    "liquidity":   round(net.eco.liquidity, 2),
                    "risk":        round(net.eco.risk, 4),
                    "stability":   round(net.eco.stability(), 6),
                    "phi_bio":     round(net.phi_bio(), 6),
                    "minted":      round(sat_to_bio(net.emission.minted), 2),
                    "burned":      round(sat_to_bio(net.emission.burned), 4),
                    "genesis_left":Emission.GENESIS_MAX_COUNT - net.emission.genesis_granted,
                },
                "block": None,
                "reason": reason,
            }

            if block:
                payload["block"] = {
                    "index":     block.index,
                    "hash":      block.hash[:12],
                    "validator": block.validator,
                    "reward":    round(sat_to_bio(block.reward), 4),
                    "mode":      "bootstrap" if block.validator == "NETWORK" else "consensus",
                }

            for c in list(ws_clients):
                try:
                    await c.send_text(json.dumps(payload))
                except Exception:
                    ws_clients.discard(c)

    except Exception:
        ws_clients.discard(ws)

if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════════════╗
║              BIOCHAIN AAECN                       ║
║    Organic Node Emergence                         ║
╠══════════════════════════════════════════════════╣
║  Nodes are born from participant activity         ║
║  No registration required                         ║
╠══════════════════════════════════════════════════╣
║  Node emergence threshold: {EMERGE_THRESHOLD} impulses              ║
║  Energy per impulse:       {ENERGY_PER_IMPULSE} BIO                  ║
║  Decay per block:          {ENERGY_DECAY_RATE}                      ║
║  Death below energy:       {ENERGY_DEATH}                        ║
╠══════════════════════════════════════════════════╣
║  POST /tx           -- send an impulse            ║
║  POST /balance      -- check balance              ║
║  GET  /state        -- network state              ║
║  GET  /nodes        -- all nodes                  ║
║  GET  /validators   -- validators with tiers      ║
║  GET  /biofield     -- biofield                   ║
║  GET  /emission     -- 21M BIO emission           ║
║  GET  /supply       -- token circulation           ║
║  GET  /chain        -- block chain                 ║
║  GET  /longevity    -- longevity schedule          ║
║  GET  /events       -- network events             ║
║  GET  /verify       -- chain verification          ║
║  POST /stake        -- BIO stake                  ║
║  GET  /stake        -- stake status                ║
║  POST /proposals    -- create a proposal           ║
║  GET  /proposals    -- list proposals              ║
║  POST /vote         -- cast a vote                 ║
║  GET  /governance/params -- governance parameters  ║
║  (slash -- now only via /proposals,                ║
║   param_key="slash", no direct endpoint)           ║
║  POST /save         -- network snapshot            ║
║  POST /load         -- load snapshot               ║
║  WS   /ws           -- websocket                   ║
╠══════════════════════════════════════════════════╣
║  Genesis: 1-1,000 x100 / 1,001-6,000 x20 / 6,001-16,000 x10║
║  Max supply: 21,000,000 BIO                        ║
║  Fee: 0.01 BIO + 0.05% per transaction            ║
╚══════════════════════════════════════════════════╝
    """)
    print("[SECURITY] Binding 0.0.0.0:8000 -- this is the INTERNAL port only.")
    print("           Production traffic must reach it via nginx reverse proxy")
    print("           on 443 (TLS). Do NOT add 'ufw allow 8000' on a public server.")
    uvicorn.run(app, host="0.0.0.0", port=8000)
