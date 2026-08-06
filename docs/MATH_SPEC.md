# BioChain — Mathematical Specification

This document collects every formula, threshold, and invariant used in consensus, in one place, with exact notation — separate from the narrative whitepaper, for anyone who wants to verify the math directly rather than read it as prose.

All monetary values are integers in **satoshis** (`sat`). `1 BIO = 10^8 sat`. There is no floating-point arithmetic anywhere on the consensus-critical path.

---

## 0. Signature Scheme and Address Derivation

```
address(pk) = "BIO1" + SHA3-256(pk)[:32].upper()
```

**Address length changed 16 → 32 hex chars (64 → 128 bits), 2026-07-28,
coordinated with a full network genesis reset — not a backward-compatible
change.** The 64-bit version was a real, if impractical-today, weakness:
a targeted second-preimage attack against one specific existing address
needed ~2^64 attempts (not the ~2^32 birthday bound, which only finds
*some* collision among an attacker's own freshly-generated keys and
grants no access to anyone else's funds — the two are frequently
conflated and are not the same threat model). At 128 bits the same
targeted attack needs ~2^128 — including Grover's quadratic quantum
speedup, which brings the *effective* difficulty down to ~2^64, this
stays firmly in "not achievable by any realistic adversary, today or
for a long horizon" territory, unlike the 64-bit original.

This intentionally did NOT go through the scheme_id agility mechanism
described below (which exists precisely for changes like this) — the
`"MLDSA44"` scheme's own formula was changed directly, wallet and
backend updated in lockstep, and the chain reset to genesis rather than
carrying old-format addresses forward. That was the pragmatic call
specifically because the network was still small enough (two nodes, no
third-party wallets, no real economic activity yet) to reset cleanly;
it should not be read as the template for a *future* format change,
which should use a new scheme_id instead and avoid a reset.

Every address that has existed on BioChain since this reset is ML-DSA-44
(CRYSTALS-Dilithium2, FIPS 204) — no ECDSA fallback, no hybrid mode.
This is a deliberate choice, not a temporary one: ML-DSA is already
fully NIST-standardized, unlike newer, still-evolving candidates such as
FAEST or HAWK.

**Cryptographic agility foundation (v5.40):**

```
address(pk, scheme_id="MLDSA44") = "BIO1" + SHA3-256(pk)[:32].upper()          -- the current default formula
address(pk, scheme_id=X≠"MLDSA44") = "BIO1" + SHA3-256(X + pk)[:32].upper()    -- any future scheme
```

`scheme_id` defaults to `"MLDSA44"` everywhere it is not explicitly
passed. As of the 2026-07-28 reset above, this default formula is no
longer a historical constant carried unchanged since launch — see the
note above for why this one instance changed the default directly
instead of adding a new scheme_id. A future scheme's `scheme_id` is
folded into the hash, guaranteeing no address collision with an
ML-DSA-44 address is possible even given identical raw key bytes.

This lays groundwork only — no wallet, API endpoint, or verification path currently passes anything other than the default. Adding a second scheme later (a hash-based candidate such as SLH-DSA/FIPS 205 would be the most independent choice, chosen specifically for its security assumption being mathematically unrelated to ML-DSA's lattice-based one) would require wiring `scheme_id` through the relevant endpoints and a new `wallets.sig_scheme` column value for opted-in wallets, but never a new genesis and never breaking a single existing address.

**ML-DSA-65 and ML-DSA-87 explored (July 2026):** the foundation above was exercised end to end against both ML-DSA-65 and ML-DSA-87 (same lattice family as ML-DSA-44, one and two security levels up respectively) as isolated, throwaway test forks in a Ubuntu environment -- real liboqs backend, freshly built from source, not a simulation. Both forks confirmed full correctness (tamper rejection, wrong-key rejection, address derivation all behave correctly) and real hardware figures were measured directly, all three levels in one run for a fair comparison:

```
                    ML-DSA-44      ML-DSA-65      ML-DSA-87
public key          1,312 B        1,952 B        2,592 B
signature           2,420 B        3,309 B        4,627 B
verify/sec          10,469         6,845          4,240

relative to ML-DSA-44:
  ML-DSA-65:  key 1.49x, signature 1.37x, verify 0.65x speed
  ML-DSA-87:  key 1.98x, signature 1.91x, verify 0.40x speed
```

See Whitepaper §3.2e for the full write-up, including why the security-margin gain is exponential even though this cost is linear. No production code was changed and no address format was altered for either exercise — both were deliberately kept same-family security-margin exercises, not the cross-family diversification the paragraph above describes, and remain unactivated pending an actual governance decision rather than mere technical readiness.

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

ENERGY_DEATH = 5.0
```

**Fixed-point since v5.43 (BC-001, July 2026):** `energy`, `reputation`,
`recent_activity`, and `risk` are stored and computed as exact integers
scaled by `CONSENSUS_SCALE = 1,000,000` internally (e.g. `energy = 8.0`
is stored as `8,000,000`), not as `float`. The values and formulas below
are given in their real-world (unscaled) meaning; the scaling is a pure
implementation detail chosen specifically so two independent peers can
never compute even a single-bit-different result for the same inputs,
which `float` does not strictly guarantee across CPU architectures. The
one formula that does NOT reduce to simple integer arithmetic is the
multiplicative `recent_activity` decay below (`0.95^elapsed`, see note).

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
alive(t) = energy(t) > ENERGY_DEATH   (i.e. energy(t) > 5.0, not > 0)
```

This threshold was already `5.0` in the running code before this
revision; this document previously (incorrectly) stated `> 0` here —
corrected to match, not a behavior change.

**`recent_activity` decay** (used in `weight()`, §6a) is multiplicative,
not additive: `recent_activity(t+1) = recent_activity(t) × 0.95^elapsed`.
Implemented as an exact integer ratio (`0.95 = 19⁄20` precisely, so
`× 19^elapsed ÷ 20^elapsed`, integer division, no rounding drift), with
one bounded exception: for `elapsed ≥ 512` blocks since last touched,
the result is set directly to `0` rather than computed — at
`CONSENSUS_SCALE` precision, `0.95^512` is already smaller than the
smallest representable unit, so this is exact, not an approximation,
and avoids computing a needlessly enormous exact integer power for a
node that hasn't been touched in a long time.

A node with `alive(t) = false` for `365` consecutive days has its balance swept:

```
balance(a) → pool_ecosystem,   if dead for 365 days with no rebirth
```

## 6a. Node Roles

```
ROLES = {VALIDATOR, KEEPER, ROUTER}
role(a) = random choice from ROLES, assigned once at emergence
```

**Naming collision, stated explicitly to prevent confusion:** `VALIDATOR` is also the name of a stake tier (§8). A node's role and its stake tier are two completely independent axes, assigned by entirely different mechanisms (role: random at birth; tier: deterministic function of staked BIO) -- a node with role `ROUTER` and stake tier `ANCHOR_VALIDATOR` is a normal, valid combination. The shared word is coincidental, not a relationship. Confirmed live on the production network: the founder's own node currently holds role `ROUTER` and stake tier `NONE` simultaneously (`GET /validators`).

**Rebirth:** if a previously-dead address is reborn, it has a deterministic ~30% chance of inheriting its prior role instead of drawing a new one:

```
seed = SHA-256(address + births)
inherit_role ⟺ (seed mod 100) < 30
```

Deterministic, not `random.random()` -- this runs inside consensus-critical code, and every node on the network must compute the identical outcome independently, the same principle behind every other decision in this document.

```
ROLE_BONUS = {
    VALIDATOR: {energy: 1.0, reputation: 0.02},
    KEEPER:    {energy: 2.0, reputation: 0.01},
    ROUTER:    {energy: 0.5, reputation: 0.01},
}
```

On every impulse sent:

```
energy(t+1)     = energy(t) + ENERGY_PER_IMPULSE × ROLE_BONUS[role].energy + 0.1 × value_bio
reputation(t+1) = min( reputation(t) + ROLE_BONUS[role].reputation, 10.0 )
```

**Role's only downstream effect** is through this energy/reputation growth rate. Role does not appear directly in validator selection (§3.2b of the Whitepaper), block reward size, or governance vote weight (§9). It reaches exactly one further place -- the block-finalization weight check:

```
weight(node) = (recent_activity × 1.0 + reputation × 2.0 + energy × 3.0)
               × (liquidity / (1 + risk)) × stake_tier_weight_mult

can_finalize = stability > THETA_S  AND  weight(validator) > THETA_W  AND  impulse.energy < THETA_I

THETA_S = 0.15   THETA_W = 5.0 (default, governable)   THETA_I = 80.0
```

Since `reputation` and `energy` both feed `weight` directly, and both grow at a role-dependent rate, a node's role indirectly affects how quickly it clears `THETA_W` at a given level of activity -- `KEEPER` fastest on energy (2x growth), `VALIDATOR` fastest on reputation specifically, `ROUTER` slowest on both. This is the only chain by which role reaches consensus-relevant code.

**If the selected validator fails this check**, the block is not rejected -- the impulse falls back to bootstrap-mode processing (the same path used before any node has emerged) rather than being hard-rejected. Deterministic validator selection (§3.2b of the Whitepaper) is unaffected by role; this check only gates whether the selected validator's block clears normally or falls back.

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

Then a `7-day` timelock applies before the change takes effect. `0.70` (the pass threshold), the `7-day` timelock, and `MAX_SUPPLY = 21,000,000` are **constitutional** — excluded from the set of governable parameters, un-votable by design.

`21` is the default quorum and its own hard floor, not constitutional -- `governance_min_votes` is itself a governable parameter (bounds `[21, 10_000]`), deliberately allowing the network to raise (never lower below 21) its own quorum requirement as it grows, without a code deployment.

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

## 14. Scale Testing and Performance Work (July 2026) — DEPLOYED July 21, 2026

Everything below describes work originally done against an isolated
fork of the production code, tested in sandbox conditions and, for
most of it, directly on production hardware via throwaway instances
with their own isolated database -- never a live database, never live
funds, during development. **As of July 21, 2026, this code is running
on both production servers (server1, server2).** See Whitepaper
section 3.12 for the full write-up, including the specific findings
that prompted this work (real, measured timeouts at 12,000 live nodes;
a single `/verify` call taking over four minutes at 258,938 blocks)
and an honest accounting of which of the eight fixes were confirmed on
production hardware specifically versus sandbox-only.

**Seven of the eight fixes below change no formula in this document --
same math, same outputs, verified deterministic/byte-identical against
the unmodified code in every case; only the internal computation
strategy changed, for performance.** The one exception is noted
explicitly:

- Validator-selection formula (section on organic node emergence /
  role weight): unchanged. A cached, sorted alive-address list replaces
  re-sorting every alive node on every impulse; invalidated exactly on
  birth, death, or rebirth. 313 -> up to 425,386 selections/sec measured
  at 12,000 alive nodes on production hardware.
- Energy decay (`energy(t+1) = energy(t) - ENERGY_DECAY_RATE + ...`):
  unchanged as a formula. Previously recomputed for every alive node on
  every block; now computed lazily and exactly for a specific node only
  when that node is actually touched (`Node.materialize()`), with death
  timing precomputed once via `ceil((energy - ENERGY_DEATH) /
  ENERGY_DECAY_RATE)` and revised only when that node's energy actually
  changes. Verified against the original per-block formula over a
  400-block simulation with intermixed touches: 49/50 test nodes
  produced bit-identical energy and alive/dead status; the 50th differed
  by exactly one block due to floating-point accumulation from ~400
  sequential subtractions versus one batched multiplication (~1e-13
  magnitude) -- a one-time migration-boundary artifact, not an ongoing
  cross-peer divergence risk, since every peer running the same new code
  computes the identical closed-form result.
- State-checkpoint hash / chain integrity check (`state_hash`,
  section 11's canonical-form check, and the separate prev_hash
  link-check `/verify` performs): unchanged as a check. Previously
  rescanned the full chain on every `/verify` call; now caches how far
  the chain has already been confirmed intact, since a previously-valid
  link can never become invalid later (blocks are immutable once
  appended). 12.53ms -> 0.04ms at 100,000 blocks (335x); 28.00ms ->
  0.04ms at 500,000 blocks (667x).
- In-memory rollback on a failed transaction: unchanged in what it
  guarantees (full restoration of every balance a failed transaction
  touched). Previously snapshotted every alive node's balance before
  every transaction; now records only the specific nodes actually
  touched, via a property on `Node.balance`, regardless of which of the
  many code paths that can change a balance is responsible. Verified on
  a three-node scenario (sender, receiver, and an unrelated third node)
  that all three balances restore exactly on a simulated failure.
- Chain storage: no consensus-relevant formula involved. Blocks older
  than a configurable hot window (currently the most recent 50,000)
  are held as lightweight references (hash, prev_hash, index, validator,
  reward, timestamp only) instead of full objects; full impulse detail
  for an old block loads from the database on the rare access that
  actually needs it, and is verified byte-for-byte identical to the
  original after that reload. This is the fix for the four-minute
  `/verify` finding: at 258,938 blocks without it, block-generation
  throughput fell from ~1,900 blocks/sec to ~90 blocks/sec under
  uncontrolled memory growth; at 500,000 blocks with it, generation
  speed and available memory both stayed flat throughout, tested
  directly on production hardware -- confirmed via a throwaway instance
  on server1's actual hardware, synthetically generating 60,000 blocks
  to exercise the hot/cold boundary, not sandbox-only. **Not yet
  exercised against the real, live chain as of this writing** -- the
  live chain is at block 191, far short of the 50,000-block hot window
  boundary, so the cold-storage path has been confirmed on production
  hardware but never yet on production data.
- SQLite WAL checkpointing: not a chain-consensus matter at all --
  pure local disk housekeeping, explicitly safe for different peers to
  perform at completely different times or frequencies with zero
  cross-peer effect. Found necessary after a real incident: SQLite's own
  automatic checkpointing fell behind under sustained write load in
  these same tests, letting the WAL file grow to 1.9GB, at which point a
  single-row primary-key lookup (exactly what the chain-storage fix
  above depends on) took 89.5 seconds instead of under a millisecond. An
  explicit checkpoint every 5,000 blocks, issued only after each
  transaction's own commit completes, keeps the WAL file bounded --
  verified to return to 0 bytes at each interval. **Confirmed live in
  production** on both servers July 21, 2026: manual `PRAGMA
  wal_checkpoint` returned `0|656|656` (server1) and `0|599|599`
  (server2) -- not busy, every outstanding frame checkpointed.

**The one exception -- an actual parameter-governability change:**

```
transfer_fee_flat: governable, bounds [0, 1] BIO, default unchanged at 0.01 BIO
```

Previously only the percentage component of the transfer fee (formula
in section 5 above) could be adjusted by governance vote; the flat
0.01 BIO component was a hardcoded constant, contrary to this
document's own §5 description of both components as governable.
Bitcoin shipped an almost identical hardcoded flat minimum fee (0.01
BTC) in 2010 and had to remove it entirely within about a year once
BTC's price rose enough to make it disproportionate -- a disruptive,
coordinated protocol change of exactly the kind a governance parameter
exists to avoid needing later. This closes that same gap here, before
it has ever mattered in practice. Default behavior is unchanged as of
deployment; this only takes effect once a future governance vote
actually changes it. **Not yet exercised as of this writing** -- no
proposal targeting `transfer_fee_flat` has been created on the live
network.

All eight fixes pass the full 194/195-check regression suite (195 with
liboqs present) individually and together, both pre-deployment and
against the exact file now running in production (hash confirmed
identical on both servers). Deployed to server1 and server2 on July 21,
2026; a real-traffic observation period is in progress on both before
this is considered fully closed out.

---


## 15. Security Hardening (July 2026) — DEPLOYED July 28, 2026

Prompted by several independent rounds of automated code review between
July 26 and July 28, 2026 — each round re-analyzed against the actual
current file, not a cached earlier version, since several findings
across the rounds turned out to describe a version already superseded
by an earlier fix in the same review cycle. A meaningful fraction of
findings across all rounds were investigated and found to already be
correctly handled, or to describe a materially different, less severe
issue than first stated once checked directly against the running code
and, where practical, verified with a real reproduction rather than
accepted on the finding's own description — that verification discipline
is itself part of what's being reported here, not a footnote to it.
Findings confirmed real are grouped below by what they actually protect
against, not by which review round raised them.

**Per-block supply invariant enforcement.** The five-bucket check in §1
was previously only ever run on demand, via `/verify`. It now also runs
automatically after every single block, in production, using the same
`_check_supply_invariant()` code `/verify` itself calls — not a
duplicate. If it fails, the entire block is rolled back (database and
in-memory state together) before it is ever persisted or relayed to a
peer, via the same rollback path a rejected transaction already used.
Cheap enough to run unconditionally at current and near-future network
size (one `SUM()` per money-holding table, not a function of chain
length). Test fixtures that deliberately inject unbalanced test-only
funds via direct database writes (a long-standing, narrow testing
shortcut, not a code path reachable from any API) explicitly disable
this check for their own run only; production default is always
enforced.

**Governance-parameter rollback.** A governance vote's effect on
in-process global parameters (`EMERGE_THRESHOLD`, fee rates, stake-tier
thresholds, and similarly governed values) is now snapshotted before
application and restored if anything fails partway through applying it
— including a failure originating inside the application logic itself,
not only failures elsewhere in the same block. Verified directly: forcing
a fault mid-application and confirming the affected parameter reverts to
its exact pre-vote value, not merely close to it.

**Reward-payout atomicity via SQLite SAVEPOINT.** Longevity rewards (§7)
and unstake payouts each touch two different tables — crediting a
wallet's balance and marking that specific reward as paid are now
wrapped in a single SQLite `SAVEPOINT`, so a fault between the two
undoes both together, at the database level, rather than risking a
credited-but-unmarked state that a later block's tick could pay out a
second time. This is narrower and more surgical than wrapping the
entire multi-node reward pass in one transaction, deliberately: a
persistent fault specific to one node's data must not be able to make
every future block fail the same way and stall the chain indefinitely
— each node's own reward attempt is now atomic on its own, while a
fault on one node still leaves every other node's payout that block
unaffected, and the chain continues producing blocks. Founder-vesting
payout (§2a), by contrast, is deliberately *not* wrapped this way: it
touches only one recipient per attempt (not a loop over many nodes, so
the stall risk above does not apply the same way), and a fault there
is now allowed to propagate to the full block-level rollback instead
— the safer choice specifically because vesting's credit step happens
before its own "already paid" bookkeeping step, so only a full rollback
of both together, not a locally-scoped save, correctly prevents a
double payout there.

**Peer-block validator re-verification.** Receiving a block from a peer
already re-verified that its claimed validator was the legitimate,
deterministically-selected one (§3.2b of the Whitepaper). It now also
re-verifies that the same validator would have cleared the
finalization-weight threshold (`can_finalize`, §6a) — the same bar a
locally-originated block must clear before it can finalize instead of
falling back to bootstrap-mode processing. Previously, a correctly-
selected validator that did not clear this bar could still have its
peer-relayed block accepted, since only selection legitimacy was
checked on receipt, not the finalization bar itself.

**Sybil-resistant self-announcement.** `POST /peer/announce` (§12a)
previously accepted a bare URL with no proof of anything. It now
requires the same kind of signed request every fund-affecting endpoint
already requires — address, public key, ML-DSA-44 signature, timestamp,
and a strictly-increasing nonce, verified exactly as any other signed
request is (§0). This doesn't change what self-announcement itself
grants (still zero trust on its own — see §12a), but it does mean
producing even one announcement now costs a real ML-DSA-44 keypair and
signature, not just an HTTP request with a string in it — raising the
cost of flooding the candidate list, independent of and in addition to
the majority-confirmation requirement that already gates actual
promotion.

**DNS-rebinding protection for automatic HTTP peer promotion.** Before
this fix, verifying a candidate URL's safety (rejecting loopback,
private, and link-local targets, including the cloud-metadata address)
and then making the actual liveness-check request were two separate
steps, each independently resolving the hostname — a malicious DNS
server could answer safely for the first resolution and differently for
the second, bypassing the check entirely. The liveness-check request
now connects directly to the exact IP already verified safe (with a
`Host` header preserving correct routing on the peer's side), closing
that window completely for `http://` candidates. `https://` candidates
are a deliberate, disclosed exception: substituting a verified IP into
an HTTPS request breaks TLS certificate-hostname validation, and
disabling certificate verification to work around that would be worse
than the narrow window it would close. `https://` candidates therefore
still resolve the hostname once at the connection itself, same as
before this fix — an accepted, narrower residual, not an oversight.
This applies specifically to the *promotion-verification* HTTP request;
it does not weaken or bypass the cryptographic verification every piece
of actual chain data still undergoes regardless of which peer sent it
(§12) — a peer being wrongly promoted could at most cause one
liveness-check request to reach an unintended target, never accepted or
propagated bad chain data.

**Fork-resolution rate limiting.** A peer claiming a fork now triggers
at most one deep fork-resolution attempt (full paginated fetch plus
isolated-database replay) per peer per five-minute window. A single
misbehaving or merely flaky peer can no longer force this comparatively
expensive path on every sync cycle; a genuine fork is still resolved,
just not re-attempted against the same peer faster than once per
window.

**Public-key length validation, made consistent.** Every signature
re-verification path now explicitly checks the submitted public key is
exactly 1,312 bytes (the fixed ML-DSA-44 public key size) before using
it, rather than relying on the underlying cryptographic library to
reject a malformed-length key downstream. This was already true for the
main HTTP-facing signature check; a second, independent re-verification
path used specifically when applying a peer's block was found missing
the same check and has been brought in line.

**Halt, don't warn, on detected local database corruption.** If a
node's own restart-time chain reload finds a broken hash link between
two adjacent local blocks — meaning the local database itself has been
corrupted, not a peer-sync disagreement — the node now refuses to start
at all, with a clear message pointing at the most recent automated
backup (§ "Backups" in the Whitepaper's Implementation Stack table), and
exits immediately. Previously it logged a warning and continued running
on the corrupted chain regardless.

**Thread-safe single-address state lookups.** Two read endpoints
(`/balance`, and `/tx`'s response formatting) previously read node state
directly from a shared in-memory structure without taking the lock that
already protects it elsewhere — safe under ordinary load, but capable
of observing a transient empty-or-partially-rebuilt view specifically
during the narrow window a fork-resolution adoption is actively
replacing that same structure. A new, single-purpose thread-safe lookup
closes this for both endpoints without the cost of copying the entire
node set the way the existing bulk-read path already safely does for
list-returning endpoints (`/nodes`, `/dashboard`, `/validators`,
`/longevity`), which were already correctly using it and needed no
change.

**Graceful shutdown and stalled-checkpoint visibility.** The three
long-running background loops (peer sync, gossip, signature pruning)
now check a shared shutdown flag instead of running an unconditional
`while True`, and the process now installs `SIGTERM`/`SIGINT` handlers
that set this flag and trigger one final WAL checkpoint before actually
exiting — verified directly: killed and restarted five times in a row
under real `SIGTERM`, real `SIGKILL` (immediately after startup, mid
genesis-pool writes), and a real, unrelated concurrent read transaction
forcing a checkpoint to report itself busy, all on the actual production
hardware, with zero chain-integrity warnings on any subsequent restart.
The periodic WAL checkpoint (§14) now also explicitly logs when SQLite
reports it as only partially completed (`busy`), rather than silently
discarding that status — the next scheduled checkpoint still completes
it; this only makes an already-recovering condition visible rather than
invisible.

**Fast state-sync left disabled, not merely unfinished.** State-snapshot
fast sync (§11's mechanism, intended to let a new node skip full chain
replay) has a real, identified gap: the block table is deliberately
excluded from the snapshot for size reasons, but nothing yet
re-establishes the local chain's *length* after adopting a snapshot,
so a subsequent ordinary sync would currently re-fetch and re-apply the
entire real chain from block zero on top of already-current snapshot
balances. This was found, understood precisely, and is disabled outright
(`FAST_SYNC_ENABLED = False`) rather than shipped in a half-working
state — ordinary block-by-block sync, which does not have this gap, is
unaffected and remains the only sync path a new node uses today. A
correct fix needs the local chain populated with placeholder entries
carrying the real hash at the snapshot boundary specifically, so a
block received immediately afterward can still validate its own
`prev_hash` correctly — scoped, understood, and deliberately deferred
rather than rushed.

**Regression coverage.** The suite grew from 194 checks (§14) to 253,
covering every fix above individually, each confirmed both against a
lightweight crypto stub (fast iteration during development) and,
separately, against the real ML-DSA-44 backend on actual production
hardware — the full 253-check run completing with zero failures on
server1's real liboqs backend is what is now confirmed, not merely the
stub-backed version. A structural class of bug the rest of the suite
cannot catch by construction — an HTTP route decorator silently bound to
the wrong function, since every other check calls Python functions
directly rather than through FastAPI's own routing layer — is now
separately checked by directly inspecting the live route table itself;
this exact bug was caught by writing that check, not found first and
tested after the fact.

**A full network genesis reset accompanied this work** (§0), timed to
land together with the address-length change above while the network
was still small enough to reset cleanly. Both production servers were
stopped, backed up, updated, reset to a fresh genesis under the new
address format, and restarted; peer-to-peer synchronization between the
two independently-operated nodes (§8 of the Whitepaper) was re-confirmed
immediately afterward on live production infrastructure — a real
transaction submitted on server1 was independently observed, correctly
credited, on server2 through ordinary peer sync alone, with no
intervention on server2's side.

---

*BioChain AAECN — Mathematical Specification, corresponding to code v5.43*
