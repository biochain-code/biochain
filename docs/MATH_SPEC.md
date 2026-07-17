# BioChain — Mathematical Specification

This document collects every formula, threshold, and invariant used in consensus, in one place, with exact notation — separate from the narrative whitepaper, for anyone who wants to verify the math directly rather than read it as prose.

All monetary values are integers in **satoshis** (`sat`). `1 BIO = 10^8 sat`. There is no floating-point arithmetic anywhere on the consensus-critical path.

---

## 0. Signature Scheme and Address Derivation

```
address(pk) = "BIO1" + SHA3-256(pk)[:16].upper()
```

Every address that has ever existed on BioChain is ML-DSA-44 (CRYSTALS-Dilithium3, FIPS 204) — no ECDSA fallback, no hybrid mode. This is a deliberate choice, not a temporary one: ML-DSA is already fully NIST-standardized (unlike newer, still-evolving candidates such as FAEST or HAWK), and matches the actual production posture of the largest, best-resourced blockchain PQ migration effort in the industry (Ethereum's own accounts remain on classical ECDSA as of this writing — see the Whitepaper §3.2e for the full comparison).

**Cryptographic agility foundation (v5.40):**

```
address(pk, scheme_id="MLDSA44") = "BIO1" + SHA3-256(pk)[:16].upper()          -- unchanged formula
address(pk, scheme_id=X≠"MLDSA44") = "BIO1" + SHA3-256(X + pk)[:16].upper()    -- any future scheme
```

`scheme_id` defaults to `"MLDSA44"` everywhere it is not explicitly passed, so every address ever created continues to resolve identically — this is proven by direct byte-for-byte comparison against the original formula in the regression suite, not merely assumed. A future scheme's `scheme_id` is folded into the hash, guaranteeing no address collision with an ML-DSA-44 address is possible even given identical raw key bytes.

This lays groundwork only — no wallet, API endpoint, or verification path currently passes anything other than the default. Adding a second scheme later (a hash-based candidate such as SLH-DSA/FIPS 205 is the most likely candidate, chosen specifically for its security assumption being mathematically independent of ML-DSA's lattice-based one — see Whitepaper §3.2e for the full reasoning) would require wiring `scheme_id` through the relevant endpoints and a new `wallets.sig_scheme` column value for opted-in wallets, but never a new genesis and never breaking a single existing address.

---

## 1. Supply Invariant

At every block, the following must hold as **exact integer equality**:

```
wallets_total + Σ(pools) + locked_total + staked_total + pending_unstakes_total = 21,000,000 × 10^8 sat
```

Where:

- `wallets_total = Σ balance(a)` over all wallet addresses `a`
- `Σ(pools) = pool_validators + pool_ecosystem + pool_reserve + pool_team + pool_genesis + pool_listing_reserve + pool_wallet_registration`
- `locked_total = Σ amount(l)` over all `swap_locks` with `state = LOCKED`
- `staked_total = Σ bio_amount(s)` over all rows in `stakes`
- `pending_unstakes_total = Σ bio_amount(u)` over all rows in `pending_unstakes` with `claimed = 0` — BIO mid-way through the 7-day `UNSTAKE_COOLDOWN` (see §6), no longer counted in `staked_total`, not yet credited back to `wallets_total`

**Design note:** the check is `!=`, not `>`. A one-directional check would miss a shortfall as readily as it misses an excess. Both directions are checked because both are equally serious for an invariant whose entire purpose is proving no code path created or discarded money.

## 2. Genesis Pool Distribution

Fixed at genesis, immutable:

| Pool | Share | Amount (BIO) |
|---|---|---|
| Validators | 40.00% | 8,400,000 |
| Ecosystem | 30.00% | 6,300,000 |
| Reserve | 20.00% | 4,200,000 |
| Team (vesting) | 5.00% | 1,050,000 |
| Genesis grants | 3.90% | 820,000 |
| Listing reserve | 1.10% | 230,000 |
| **Total** | **100.00%** | **21,000,000** |

The six categories above sum to exactly `21,000,000 BIO` — no unassigned remainder. (An earlier version of this document incorrectly claimed 510,000 BIO was held back; that was a documentation arithmetic error, corrected here — the actual pool constants in code always summed to the full cap.)

**What happens to the 820,000 BIO genesis-grants pool after genesis (v5.40):** it is not one flat, single-purpose bucket. At the network's first boot, three separate carve-outs happen automatically, once each:

| Use | Amount (BIO) | Timing |
|---|---|---|
| Founder's starting operating balance | 10,000 | Once, at genesis |
| Wallet-registration grant pool (§3a) | 1,000 | Once, carved from the founder's own balance |
| Developer + server-operator grants pool (§3b/§3c) | 509,000 | Once, moved out of `pool_genesis` directly; split 254,500/254,500 in v5.41 |
| Remaining for tiered genesis grants (§3) | up to 300,000 | Ongoing, as new addresses qualify |

`10,000 + 1,000 + 509,000 + 300,000 = 820,000` — exactly the genesis pool's full allocation, none of it unaccounted for.

## 2a. Founder Vesting Schedule

```
TEAM_POOL_TOTAL = 1,050,000 BIO
CLIFF           = 6 months from genesis -- zero payout before this, no exceptions
VESTING_MONTHS  = 114                   -- v5.40: extended from 18; payout months 7-120

monthly_payout       = TEAM_POOL_TOTAL / VESTING_MONTHS  ≈ 9,210.53 BIO/month
final_month_payout   = TEAM_POOL_TOTAL - monthly_payout × (VESTING_MONTHS - 1)   -- absorbs the integer remainder
```

`6 + 114 = 120` months = exactly 10 years from genesis to fully vested, cliff included.

**Paused during a network crisis** (see §2b for the crisis formula):

```
if stability < CRISIS_THRESHOLD:
    this month's vesting payout is deferred, not forfeited — resumes once
    stability recovers, from wherever it left off
```

The founder's own payout schedule is subordinate to network health — a crisis pauses vesting before it pauses anything paid to ordinary users elsewhere in the system.

## 2b. Network Stability and Crisis Payout

```
ALPHA = 0.1;  BETA = 2.0;  GAMMA = 0.90;  DELTA = 0.01
CRISIS_THRESHOLD = 0.15

liquidity(t+1) = clamp( liquidity(t) − ALPHA×I(t) + BETA, 10, 100 )
risk(t+1)      = clamp( GAMMA×risk(t) + DELTA×I(t), 0.1, 10 )

stability(t) = 1 / (1 + risk(t))
```

`I(t)` is recent transaction intensity. Every block, if `stability(t) < CRISIS_THRESHOLD`:

```
per_node_payout = min( 50 BIO, pool_reserve / alive_node_count )
every alive node receives per_node_payout
pool_reserve -= per_node_payout × alive_node_count
risk *= 0.8   — the payout itself dampens the risk metric
```

This is the network's only automatic stabilizer — no proposal, vote, or founder action can invoke it outside these exact conditions, and none can prevent it from firing when they're met. `pool_reserve` is never replenished; once exhausted, this stabilizer stops functioning.

## 3. Genesis Grants (per-address, by registration order)

```
grant(n) =
    100 BIO   if  1 ≤ n ≤ 1,000
    20  BIO   if  1,001 ≤ n ≤ 6,000
    10  BIO   if  6,001 ≤ n ≤ 16,000
    0         if  n > 16,000
```

`n` = the address's genesis registration index (first-come, first-served up to 16,000 addresses).

**Sybil-resistance:** this grant is paid only at the moment of node emergence (see §6) — it is not a standalone, separately-triggerable payout. It therefore automatically inherits the same MIN_EMERGENCE_SPAN_SECONDS timing requirement as node birth itself: an address cannot claim a genesis grant any faster than it can become a live node, and the maximum possible drain from this specific mechanism — even with zero timing protection — is hard-capped at 300,000 BIO (1,000×100 + 5,000×20 + 10,000×10), the fixed size of the tiered allocation, never more.

## 3a. Wallet Registration Grant

A separate, smaller, one-time grant for the first wallets ever created (not tied to node emergence or genesis registration order):

```
grant_registration(k) = 10 BIO   if  1 ≤ k ≤ 100
                        0        if  k > 100
```

`k` = the address's rank by order of successful `REGISTER` impulses. Funded from a dedicated pool (`pool_wallet_registration`), pre-loaded with exactly `100 × 10 = 1,000 BIO` carved from the founder's own starting balance at the network's first boot — not a new allocation on top of the 21,000,000 cap.

Requires a real signed impulse (`kind = REGISTER`), not a passive balance check — an address that has never signed anything cannot consume a slot.

## 3b. Developer Grants Pool

```
DEVELOPER_GRANTS_POOL_SIZE_V41 = 254,500 BIO  -- half of the original
                                               -- 509,000 BIO pool; the
                                               -- other half became
                                               -- server_rewards (§3c),
                                               -- split once at first
                                               -- boot, v5.41
DEVELOPER_GRANT_MAX             = 5,000 BIO   -- ceiling per single grant
```

Funds real-world builders (wallets, block explorers, SDKs, integrations) from the genesis pool's remainder, which had no spending path in earlier versions of this document. Released only via governance proposal — same voted-amount pattern as the listing reward in §9, clamped to `[1 .. DEVELOPER_GRANT_MAX]` BIO, never a flat rate:

```
grant_developer(proposed_amount) = clamp(proposed_amount, 1 BIO, 5,000 BIO)
```

## 3c. Server-Operator Grants Pool (split from developer grants, v5.41; governance-only payout, v5.42)

```
SERVER_REWARDS_POOL_SIZE = 254,500 BIO  -- exactly half of the original
                                         -- 509,000 BIO developer-grants
                                         -- pool, moved out of it once
                                         -- at first boot (a migration,
                                         -- not a fresh genesis carve)
SERVER_REWARD_MAX        = 2,000 BIO    -- ceiling per single grant
```

Funds grants to independent server operators — anyone standing up and maintaining a genuinely separate, publicly reachable BioChain node. Released only via governance proposal, same voted-amount pattern as §3b, clamped to `[1 .. SERVER_REWARD_MAX]` BIO:

```
grant_server(proposed_amount) = clamp(proposed_amount, 1 BIO, 2,000 BIO)
```

**Why governance-only, not automatic (v5.42 redesign):** an earlier design paid this reward automatically once a server had been continuously confirmed as a trusted peer by other nodes for 365 days, tracked in a node-local table (`promoted_peers`). That design was replaced before reaching production: two independently-operated nodes can legitimately disagree about exactly when a given peer was first trusted, so the identical payout claim could be valid on one server and rejected by another — a genuine chain-split hazard, not a hypothetical one. A node's validity must depend only on the chain itself, never on any one node's private bookkeeping about who it happens to trust. Governance tally is built only from votes recorded on the chain, identical on every node by construction, which removes the hazard entirely.

Idempotency (no double-payout for the same server) is enforced by a dedicated ledger keyed on server URL, populated only by successful governance-approved grants — itself chain-derived, not node-local state.

Every grant is recorded in a dedicated ledger table (address, project name, description, amount, proposal ID) for public auditability — anyone can verify exactly what every BIO from this pool funded and which governance proposal authorized it.

## 4. Emission Schedule (Block Reward)

```
halvings(t) = floor( (t − t_genesis) / 365 days )

reward_formula(t) = max( 10 BIO / 2^halvings(t), 0.001 BIO )
```

`t_genesis` is the genesis block's chain-time anchor. Halving occurs every 365 days of **chain time** (derived from block timestamps), not wall-clock calendar time. The floor of `0.001 BIO` is permanent — the formula never reaches exactly zero.

**Smooth taper (v5.40):** the ACTUAL paid reward is not always `reward_formula(t)` — below a floor balance in `pool_validators`, it scales down linearly with the pool's remaining balance instead of paying the full formula amount right up until the pool is empty:

```
VALIDATORS_POOL_GENESIS = 8,400,000 BIO   (fixed reference: the pool's size at genesis, never re-read live)
VALIDATORS_TAPER_FLOOR  = 10% × VALIDATORS_POOL_GENESIS = 840,000 BIO

reward_paid(t) =
    reward_formula(t)                                                   if pool_validators ≥ VALIDATORS_TAPER_FLOOR
    reward_formula(t) × ( pool_validators / VALIDATORS_TAPER_FLOOR )    if 0 < pool_validators < VALIDATORS_TAPER_FLOOR
    0                                                                    if pool_validators ≤ 0
```

This replaces a hard cliff (full reward until the exact instant the pool empties, then zero) with a gradual signal — addressing the "death spiral" risk of a sudden validator exodus the instant rewards vanish.

Fees flow into `pool_validators`, minus whatever fraction is destroyed (see §5 below):

```
pool_validators(t+1) = pool_validators(t) + fee_collected(t) × (1 − FEE_BURN_PERCENT/100)
```

## 5. Fee Formula

```
fee(value) = 0.01 BIO + 0.0005 × value        (0.05% = 500 ppm)
```

`ppm` (parts per million) is the governable unit: `500 ppm = 0.0005`. The flat component and the ppm rate are both governable parameters, bounded by hard min/max limits that cannot themselves be voted outside a safe range.

Stake fee: flat `1.0 BIO`. Unstake, proposal creation, and voting: free (`0 BIO`).

**Partial fee burning (v5.40):**

```
FEE_BURN_PERCENT = 0   at launch (governable, range 0–50%)

destroyed(fee) = fee × FEE_BURN_PERCENT / 100     -- permanently removed from supply
to_pool(fee)    = fee − destroyed(fee)            -- flows into pool_validators, as above
```

`total_destroyed` (cumulative, persisted) directly reduces the supply-invariant target in §1: `21,000,000 BIO − total_destroyed`, not a fixed number once `FEE_BURN_PERCENT > 0`. Launched at `0%` deliberately — the mechanism is fully built and tested, but real deflationary pressure is deferred until the network has matured. Raising it later requires only a governance vote, no code deployment.


## 6. Organic Node Emergence

```
EMERGE_THRESHOLD = 21 impulses

MIN_EMERGENCE_SPAN_SECONDS = 7 × 86,400   (7 days; governable, floor 1 day)

energy_per_impulse = 8.0 × role_bonus(address)

energy_decay_per_block = 0.02
```

A node is born the moment BOTH hold:

```
tx_count(address) ≥ EMERGE_THRESHOLD
    AND
now − first_seen(address) ≥ MIN_EMERGENCE_SPAN_SECONDS
```

`first_seen` is set once, immutably, the first time an address's wallet row is created — never reset. The time condition is Sybil-resistance: 21 low-fee impulses alone used to be sufficient for a node (and one governance vote) to be born, letting anyone script mass node creation for the cost of gas alone. Requiring real elapsed wall-clock time between an address's first activity and its 21st impulse makes mass creation cost real time, not just a script — without tying voting weight to stake or requiring any identity check (see §9: `vote_weight = 1` for every live node regardless of capital, by design).

Rebirth of an address that has already been born once is **not** re-gated by this timing condition — the time cost was already paid at first birth.

After birth:

```
energy(t+1) = max( energy(t) - 0.02 + Σ(new impulses at t) × 8.0 × role_bonus, 0 )
```

```
alive(t) = energy(t) > 0
```

A node with `alive(t) = false` for `365` consecutive days has its balance swept:

```
balance(a) → pool_ecosystem,   if dead for 365 days with no rebirth
```

## 7. Longevity Rewards

```
reward(months_alive) =
    +10  BIO    once,  at months_alive = 6
    +100 BIO    once,  at months_alive = 12
    +21  BIO    per subsequent month  (governable rate)
```

These are one-time bonuses at the 6- and 12-month marks, then a recurring monthly rate thereafter — not compounding, not retroactive.

## 8. Stake Tiers

Tier is a deterministic function of `bio_amount` staked, with governable thresholds:

```
tier(bio_amount) =
    NONE        if  bio_amount < tier_validator_min
    VALIDATOR   if  tier_validator_min ≤ bio_amount < tier_senior_min
    SENIOR      if  tier_senior_min ≤ bio_amount < tier_anchor_min
    ANCHOR      if  bio_amount ≥ tier_anchor_min
```

Tier affects block reward weighting and validator selection probability. It does **not** affect governance vote weight — see §9.

## 9. Governance

```
vote_weight(node) = 1,   for every live node, regardless of stake tier or balance
```

Proposal passes if and only if:

```
total_votes ≥ 21   AND   votes_for / total_votes ≥ 0.70
```

Then a `7-day` timelock applies before the change takes effect. `21` (the quorum floor), `0.70` (the pass threshold), the `7-day` timelock, and `MAX_SUPPLY = 21,000,000` are **constitutional** — excluded from the set of governable parameters, un-votable by design.

## 10. HTLC Atomic Swaps

```
SWAP_OFFER:   sender publishes {give_bio, want_asset, want_amount, ext_address, ttl}
SWAP_LOCK:    sender locks give_bio under hash_lock = SHA-256(preimage)
SWAP_CLAIM:   receiver reveals preimage such that SHA-256(preimage) = hash_lock
              → receiver receives give_bio, preimage becomes public on-chain
SWAP_REFUND:  sender reclaims give_bio if chain_time > lock_time + timeout,
              and no valid claim has occurred
```

Constraints checked at consensus, not trusted from any peer:

```
preimage ∈ {0,1}^256          (exactly 64 hex characters, strictly enforced)
hash_lock = SHA-256(preimage) (recomputed and compared, never assumed)
SWAP_MIN_LOCK = 1 BIO
SWAP_LOCK_TIMEOUT_MIN = 3,600 s   (1 hour)
SWAP_LOCK_TIMEOUT_MAX = 604,800 s (7 days)
SWAP_MAX_ACTIVE_LOCKS = 10   per address
```

`want_asset` is unconstrained free text (`1 ≤ len ≤ 32` characters, non-empty) — deliberately not tied to any hardcoded external-chain whitelist.

## 11. State Checkpoint Hash (Canonical Form)

For a snapshot of the 14 state-bearing tables (excluding `blocks`, `events`, `used_signatures`, and `checkpoints` itself — see whitepaper §3.8 for the exclusion rationale):

```
canonical(table) = [ sorted(row, key=column_name) for row in table
                      ordered by table's natural primary key ]

canonical_json = JSON.dumps( { table: canonical(table) for table in SNAPSHOT_TABLES },
                              sort_keys=True, separators=(",", ":") )

state_hash = SHA-256( canonical_json.encode("utf-8") )
```

A receiving node **never** trusts a peer's claimed `state_hash` — it always recomputes independently from the received snapshot content and compares. Any mismatch, in either direction, triggers full rejection and fallback to full chain replay from genesis.

```
STATE_SNAPSHOT_EVERY = 5,000 blocks    (must be a multiple of CHECKPOINT_EVERY)
CHECKPOINT_EVERY     = 1,000 blocks
STATE_SNAPSHOT_KEEP  = 3                (rolling retention on disk)
```

## 12. Fork Resolution

Given a local chain of length `L_local` and a peer-offered chain of length `L_peer` diverging at height `d`:

```
adopt peer's chain  ⟺  L_peer > L_local
                        AND every block in peer's chain[d:] independently
                            re-verifies (signature, reward recomputation,
                            validator selection) under this node's own rules
```

No block is ever adopted on the strength of "the peer said so" — every block, including ones already accepted by the peer, is re-verified from scratch against this node's own consensus rules before being written to local storage. This was confirmed empirically between two independently-operated production nodes deliberately partitioned and reconnected — see the Production Deployment Report for the full trace.

## 12a. Node Discovery and Automatic Peer Promotion

Every node maintains two distinct lists, never merged:

- **`PEER_URLS`** — the operator-curated set of peers this node actually syncs its chain against (trusted)
- **candidates** — URLs mentioned by trusted peers during gossip, tracked with a per-URL count of *distinct* trusted peers that have mentioned them

```
confirmations(url) = |{ p ∈ PEER_URLS : p has reported url }|
```

Each trusted peer reporting the same URL more than once counts as one confirmation, not one per report — confirmation counts distinct sources, not repeated mentions.

**Promotion threshold**, recomputed continuously against the *current* size of `PEER_URLS`, not a fixed constant:

```
promotion_threshold = ⌊ |PEER_URLS| / 2 ⌋ + 1
```

A candidate is automatically, durably promoted into `PEER_URLS` — surviving restarts — the moment:

```
confirmations(url) ≥ promotion_threshold   AND   url ≠ SELF_URL
```

The `SELF_URL ≠ url` condition excludes this node's own configured public address from ever being treated as a candidate: any peer that trusts this node back will naturally list this node's own address among its trusted peers during gossip, which would otherwise be indistinguishable from a genuine third-party recommendation.

*(v5.41: `SELF_URL` and the initial trusted-peer set are read from environment variables (`BIOCHAIN_SELF_URL`, `BIOCHAIN_PEER_URLS`) at process start, not hardcoded in `biochain.py` — see `DEFAULT_BOOTSTRAP_PEERS` for the fallback used when `BIOCHAIN_PEER_URLS` is unset. The formulas above are unaffected by this — only where the values originate from changed.)*

Because the threshold is a strict majority of the *current* trust set, the cost of forging enough confirming peers to force a false promotion grows with the network — at 2 trusted peers both must independently confirm; at 10 peers, 6 must. Stale candidates unconfirmed for `7` days are pruned automatically.

**Self-announcement** (`POST /peer/announce`) is how a brand-new, previously-unknown node becomes visible in the first place — matching Bitcoin's `addr` messages and Ethereum's `FINDNODE` self-identification. A node announces its own URL to an existing node; a basic liveness check runs (the URL must respond like a real BioChain node); if it passes, the URL is recorded as a candidate:

```
confirmations(self-announced url) = 0   at the moment of announcement, always
```

Self-announcement can never itself satisfy `confirmations ≥ promotion_threshold` — it writes only to the visibility record, never to the per-peer confirmation ledger. Confirmation still requires the normal gossip mechanism above: an *already-trusted* peer independently mentioning the URL to this node. Without this separation, an attacker could announce the same URL directly to every trusted peer and manufacture as many "confirmations" as peers reachable, which is exactly the failure mode this design avoids.

## 13. Dashboard Concentration Metric

```
concentration(values, n) = 100 × Σ(top n of sorted(values, descending)) / Σ(values)
```

Computed separately for wallet balances and staked amounts, among live nodes only. This measures economic concentration (whale risk), explicitly **not** Sybil/identity detection — the architecture does not log requester IPs anywhere, and the dashboard states this limitation directly rather than implying a protection that doesn't exist.

---

*BioChain AAECN — Mathematical Specification, corresponding to code v5.40*
