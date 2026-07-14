# BioChain — Tokenomics

A complete account of where every BIO comes from and where it goes — verified directly against the running code, not the whitepaper's prose description of it. All amounts in BIO; internal storage is always integer satoshis (`1 BIO = 10^8 sat`).

No token sale has ever occurred. No BIO has been sold for money by anyone, at any point.

---

## 1. Genesis Distribution

Fixed at genesis, immutable — cannot be changed by governance vote, code upgrade, or anyone:

| Pool | Share | Amount (BIO) | Spent on |
|---|---|---|---|
| `validators` | 40.00% | 8,400,000 | Block rewards over time |
| `ecosystem` | 30.00% | 6,300,000 | Longevity rewards for long-lived nodes |
| `reserve` | 20.00% | 4,200,000 | Emergency crisis payouts |
| `team` | 5.00% | 1,050,000 | Founder vesting, 114 monthly payments over 10 years |
| `genesis` | 3.90% | 820,000 | Genesis grants + founder's starting balance + wallet-registration carve-out |
| `listing_reserve` | 1.10% | 230,000 | Exchange/DEX listing rewards |
| **Total** | **100.00%** | **21,000,000** | |

Every pool below is spent (or, in two cases, replenished) by hard-coded rules only. None of it is discretionary or manually moved by anyone, including the founder.

---

## 2. `validators` — 8,400,000 BIO (40%)

**Spends on:** block rewards, paid to whichever node validates each block.

```
reward_formula(t) = max( 10 BIO / 2^halvings(t), 0.001 BIO )
halvings(t) = floor( (t − t_genesis) / 365 days of chain time )
```

**Smooth taper (v5.40):** the reward actually paid no longer cliffs straight to zero the instant the pool empties. Below 10% of the pool's original genesis size (840,000 BIO), the paid reward scales down linearly with whatever balance remains, reaching exactly zero only when the pool itself reaches exactly zero. This replaces a hard, sudden cutoff with a gradual signal — addressing a "death spiral" risk where a sudden drop to zero rewards could trigger a validator exodus that further starves the pool.

**Replenished by:** every transaction fee, minus whatever fraction is currently being destroyed (see partial fee burning below):

```
pools["validators"] += fee_collected × (1 − FEE_BURN_PERCENT/100)
```

This is deliberate: the network funds its own future block rewards from real usage, rather than only ever drawing down a fixed pool that eventually hits zero. As long as the network has real transaction volume, this pool is at least partially self-sustaining — though nothing prevents it reaching zero if usage is low relative to reward payouts, in which case rewards simply taper toward zero rather than cutting off abruptly.

**Partial fee burning (v5.40):** `FEE_BURN_PERCENT` of every fee is permanently destroyed — removed from circulating supply for good — rather than added to this pool. Set to **0% at launch**: the mechanism is fully built, tested, and persisted, but the founder chose to hold off on real deflationary pressure until the network has matured. Governable (0–50%) — raising it later needs only a governance vote, not a new deployment. Once any amount is destroyed, the network's total-supply target (used by `/verify`) becomes `21,000,000 BIO − total ever destroyed`, not a fixed number.

---

## 3. `ecosystem` — 6,300,000 BIO (30%)

**Spends on:** longevity rewards — a bonus for a node staying alive over time, paid automatically, no application or claim required:

```
+10 BIO    once, at 6 months of continuous life
+100 BIO   once, at 12 months of continuous life
+X BIO     every month thereafter (X is governable, default up to 21 BIO/month, min 0.1)
```

Each payout checks the pool has enough balance first; if not, that specific payout is simply skipped (not queued or retried).

**Replenished by:** dead nodes. If a node dies (energy reaches 0) and stays dead for 365 days with no rebirth, its entire remaining balance sweeps into this pool:

```
pools["ecosystem"] += balance(dead_address)
balance(dead_address) → 0
```

This is the only pool that both pays out and receives real inflows from user activity (the others only ever decrease, except `validators`' fee inflow above).

---

## 4. `reserve` — 4,200,000 BIO (20%)

**Spends on:** emergency crisis payouts, triggered automatically — not by any human decision — when the network's own computed stability drops below a threshold:

```
stability = 1 / (1 + risk)
risk(t+1) = 0.90 × risk(t) + 0.01 × I(t)     (I = recent transaction intensity)

if stability < 0.15:
    per_node_payout = min(50 BIO, reserve_pool / alive_node_count)
    every alive node receives per_node_payout
    reserve_pool −= per_node_payout × alive_node_count
```

This is the network's only automatic stabilizer — designed to counteract a sudden spike in systemic risk by directly increasing every active participant's balance. It has no manual trigger; no proposal, vote, or founder action can invoke it outside these exact conditions, and none can prevent it from firing when they're met.

**Never replenished.** This pool only ever decreases. Once exhausted, this stabilizer stops functioning — a known, accepted limit, not a bug.

---

## 5. `team` — 1,050,000 BIO (5%) — Founder Vesting

**Spends on:** the founder's vesting schedule, and nothing else. This is the project's only allocation tied to a specific, named individual.

```
CLIFF = 6 months from genesis — zero payout before this point, no exceptions
VESTING_MONTHS = 114 (v5.40: extended from 18; covers months 7 through 120 from genesis)

monthly_payout = 1,050,000 / 114 BIO   ≈ 9,210.53 BIO/month
final_month_payout = remainder, so total across all 114 months == 1,050,000 exactly
```

`6 + 114 = 120` months = exactly **10 years** from genesis to fully vested, cliff included. Extended from the original 2-year schedule at the founder's request, after external review — smaller, more numerous payments over a much longer horizon, same total pool.

**Automatically paused during a network crisis:**

```
if stability < 0.15:
    this month's vesting payout is deferred, not forfeited — it resumes
    once stability recovers, from wherever it left off
```

This means the founder's own payout schedule is *subordinate* to network health — a crisis pauses vesting before it pauses anything paid to ordinary users elsewhere in the system.

**Separately, at genesis only:** the founder's starting operating balance (10,000 BIO) was drawn from the `genesis` pool below, not from this vesting pool — see §6. The `team` pool exists purely for the 114-month vesting schedule above.

---

## 6. `genesis` — 820,000 BIO (3.9%)

Four separate uses -- three one-time carve-outs at first boot, plus the ongoing tiered grants:

**a) Genesis grants**, tiered by registration order, first-come-first-served up to the first 16,000 addresses:

```
grant(n) = 100 BIO   if  1 ≤ n ≤ 1,000
           20 BIO    if  1,001 ≤ n ≤ 6,000
           10 BIO    if  6,001 ≤ n ≤ 16,000
```

**This grant is Sybil-resistant, not because of a separate defense, but because it can only fire at the exact moment a node is born.** Node birth itself requires both 21 impulses AND at least 7 real days elapsed since the address's first activity (see the "organic node emergence" mechanism) — an attacker creating thousands of addresses cannot claim thousands of genesis grants any faster than they could make those same addresses into live nodes, one real 7-day wait at a time. The absolute maximum this specific pool could ever pay out, even with zero timing protection, is hard-capped at 300,000 BIO (1,000×100 + 5,000×20 + 10,000×10) — the fixed size of the tiered table itself, never more, regardless of how many addresses attempt to claim it.

**b) Founder's starting balance** — 10,000 BIO, credited once, at the network's very first boot, so there was an operating balance to fund the first live node before any real activity existed:

```
founder_grant = min(10,000 BIO, remaining genesis pool balance)
```

**c) Wallet-registration pool carve-out** — see §8 below; 1,000 BIO of the founder's own starting balance (from item b) is immediately moved out into a separate pool at first boot, not drawn from `genesis` directly a second time.

**d) Developer-grants pool carve-out (v5.40)** — see §6a below; 509,000 BIO moved directly out of `genesis` into a dedicated pool at first boot, funding real-world builders instead of sitting as an undocumented, unspent remainder.

Adding up all four: `300,000 (max genesis grants) + 10,000 (founder) + 1,000 (wallet registration) + 509,000 (developer grants) = 820,000` — the genesis pool's full allocation, exactly, none of it unaccounted for.

---

## 6a. `developer_grants` — 509,000 BIO (funded from the genesis pool's remainder, not a new top-level allocation)

**Spends on:** grants for real-world builders — wallets, block explorers, SDKs, integrations — anything extending the network beyond the core protocol. Added in v5.40 after external review flagged this remainder as undocumented and unusable in earlier versions of this document.

```
grant = clamp(proposed_amount, 1 BIO, 5,000 BIO), voted per grant via governance proposal
```

Same voted-amount pattern as `listing_reserve` below — requires a full governance proposal and vote, amount decided case by case, never a flat automatic rate. Every grant is recorded in a dedicated table (recipient address, project name, description, amount, proposal ID) for public auditability.

---

## 7. `listing_reserve` — 230,000 BIO (1.1%)

**Spends on:** rewards for confirmed exchange or DEX listings — the only pool whose payout amount is decided by a governance vote each time, not a fixed formula:

```
amount_sat, clamped to [1 BIO .. 1,000 BIO], voted per listing via governance proposal
```

Requires an actual governance proposal and vote to release any of it — no automatic trigger exists for this pool.

---

## 8. `wallet_registration` — 1,000 BIO (funded from the founder's own balance, not a new top-level allocation)

**Spends on:** a one-time 10 BIO grant for each of the first 100 wallets ever registered on the network:

```
grant = 10 BIO,  first 100 successful REGISTER impulses only, then 0
```

Requires a real signed transaction (`REGISTER`) from the receiving address — a passive balance check can never trigger this, only a genuine cryptographic signature can.

**Funded once**, automatically, the moment the founder's own balance is first established (whether at genesis or, for an already-running chain, the first time this feature's code runs) — 1,000 BIO moves out of the founder's wallet into this dedicated pool, durably recorded so the deduction can never happen twice even across restarts.

---

## Summary: what's automatic vs. what's voted

| Mechanism | Trigger | Human decision involved? |
|---|---|---|
| Block rewards | Every block | No — pure formula |
| Fees → validators pool | Every transaction | No — pure formula |
| Longevity rewards | Node age thresholds | No — pure formula |
| Dead-node sweep | 365 days dead | No — pure formula |
| Crisis payout | Stability < 0.15 | No — pure formula |
| Founder vesting | Monthly, post-cliff | No — pure formula (only pausing is automatic too) |
| Genesis grants | First 16,000 addresses | No — pure formula |
| Wallet-registration grant | First 100 real registrations | No — pure formula |
| Listing reward | Confirmed exchange listing | **Yes** — amount decided per governance vote |
| Developer grant | Governance proposal | **Yes** — amount decided per governance vote, always |
| Partial fee burning | Every transaction fee | No — pure formula, but rate is **0% at launch** by founder decision |

Of ten active spending/burning mechanisms, eight are pure, hard-coded formulas with no human discretion at all. Two require a full governance proposal and vote, not a unilateral decision by anyone, including the founder: listing rewards and developer grants.
