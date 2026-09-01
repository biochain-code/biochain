# BioChain — AAECN

**Autonomous Adaptive Emergent Consensus Network**

A post-quantum Layer-1 blockchain where nodes are not registered or deployed — they emerge organically from participant activity. An address that sends 21 impulses (transactions) automatically becomes a live network node; without continued activity its energy decays and it dies; new impulses revive it.

> **Status: early, working, production-deployed on two independent nodes. Publicly available since July 2026 — see [Access](#access) below for what that does and doesn't mean yet.**

---

## What makes this different

- **Post-quantum from genesis.** Every signature uses ML-DSA-44 (CRYSTALS-Dilithium2, NIST FIPS 204) — not retrofitted onto an ECDSA chain, built with it from block zero.
- **Organic node emergence.** No staking-to-register, no permissioned validator set at launch. Activity itself creates nodes.
- **Integer money, no exceptions.** All monetary values are satoshi-scale integers. The supply invariant (`wallets + pools + locked + staked + pending_unstakes == 21,000,000 BIO`) is checked with exact equality, not tolerance — any drift of even one satoshi is a bug, not a rounding error. As of July 28, 2026, this check also runs automatically after every block, not only on demand.
- **Custody-free atomic swaps.** HTLC-based swaps (`SWAP_OFFER` / `SWAP_LOCK` / `SWAP_CLAIM` / `SWAP_REFUND`) between BIO and any external asset, with `want_asset` as free text — no hardcoded whitelist tying the protocol to any single external chain.
- **State checkpoints.** New nodes can adopt a hash-verified snapshot instead of replaying the full chain from genesis, with automatic fallback to full replay on any hash mismatch.
- **liboqs (C) signature backend.** ~228x faster ML-DSA-44 verification than the pure-Python reference implementation, measured on real production hardware — ~44,365 verifies/sec.

## Real-world validation, not just a test suite

This isn't a whitepaper-only design. As of the current release:

- **261-test regression suite** passes with genuine ML-DSA-44 cryptography (not mocked), on-device and in production. Covers consensus, the supply invariant across all five money-holding buckets, the wallet-registration grant, node-discovery/auto-promotion logic, Sybil-resistant node emergence, cryptographic peer self-recognition, transaction and reward atomicity under partial failure, fee-model consensus safety between the two independent block-application paths, and the developer and server-operator grants pools.
- **Two independently-hosted production nodes** (different countries, different data centers) run live peer synchronization, including automatic fork resolution after a real network partition — the longer valid chain wins, with no operator intervention.
- **Automatic node discovery, once introduced -- genuinely no manual step at the current network size.** A brand-new node announces itself (`POST /peer/announce`, matching Bitcoin's `addr` messages / Ethereum's `FINDNODE`, and requiring a real signed request as of July 28, 2026 -- not a bare URL) to become visible on whichever existing node it's pointed at -- this alone grants no trust. Promotion into a trusted peer requires independent confirmation from a strict majority of the *existing* trust set through normal gossip between peers that already trust each other, the same as any other candidate -- and with each of today's two production nodes trusting exactly one peer, that majority is just one confirmation, met automatically on the very next gossip cycle. Traced end to end against the running code: no operator, on either side, needs to add anything manually for a new node to be pulled in this way. At a larger trust set the arithmetic is the same, just requiring more independent confirmations -- still automatic, just needing more of the network to agree first.
- **Sybil-resistant node emergence.** Becoming a live, voting node has always required 21 impulses; it now additionally requires at least 7 real days between an address's first activity and that 21st impulse — turning mass node creation into something that costs real time, not just a script and spare change.
- **Developer grants.** 254,500 BIO fund real-world builders (wallets, explorers, SDKs, integrations), released only via governance vote, capped at 5,000 BIO per grant.
- **Server-operator grants.** 254,500 BIO fund independent node operators, released only via governance vote, capped at 2,000 BIO per grant — the other half of the original 509,000 BIO pool, split in v5.41.
- **Cryptographic agility foundation.** Address derivation and signature verification accept an optional scheme identifier, defaulting to ML-DSA-44. Nothing uses anything but the default yet — this only ensures a second scheme could be added later without a new genesis or breaking a single existing address. The July 28, 2026 address-length change deliberately did *not* go through this mechanism — it changed the default formula directly, alongside a full genesis reset, disclosed in full in `MATH_SPEC.md` §0.
- **Performance rework for scale (v5.43).** Eight targeted fixes -- validator-selection caching, event-driven node death via a heap instead of a per-block sweep, lazy energy decay, `/verify` integrity caching, batched balance rollback, lazy cold storage for blocks outside a 50,000-block hot window, periodic WAL checkpointing, and a newly-governable flat transfer fee -- deployed to both production nodes July 21, 2026. Seven of the eight change no formula, only computation strategy. Full detail and an honest per-fix confirmation status (production-hardware-tested vs. sandbox-only) in `MATH_SPEC.md` §14.
- **Security hardening and address-length extension (July 28, 2026).** Several rounds of independent code review, each re-checked directly against the running code rather than accepted at face value, led to real fixes: per-block supply-invariant enforcement, governance-parameter rollback on failure, database-level atomicity for reward payouts, peer-block validator re-verification against the same finalization bar a local block must clear, signed (not bare-URL) self-announcement, DNS-rebinding protection for peer promotion, and a halt-not-warn response to detected local database corruption. Shipped together with extending the address hash from 16 to 32 hex characters and a full network genesis reset, timed while the network was still small enough to reset cleanly. Full detail, including which findings turned out to already be correctly handled, in `MATH_SPEC.md` §15.
- **Crypto agility facade + real fast state-sync (August 31, 2026).** A `SignatureVerifier` interface now sits between the rest of the codebase and the actual cryptographic implementation -- today that implementation is the same liboqs-backed logic as always, byte-for-byte cross-verified against the pre-facade code on both production nodes, but the switch point for a future implementation (e.g. a Rust service) is now a single import line, not a codebase-wide search-and-replace. Separately, state-snapshot fast-sync -- disabled since July 26 over a real, specific gap (a node that adopted a snapshot had no way to correctly resume ordinary block-by-block sync afterward) -- is fixed and re-enabled: the snapshot-adopting node now seeds its chain with a real anchor block, and the ~30 places in the codebase that had conflated "current network height" with "how many block objects happen to be loaded in memory" (safe only because the chain has never actually been sparse before) were individually audited and corrected for the same reason. Full detail in `MATH_SPEC.md` §16.

We document what actually broke and how it got fixed, not just what works when nothing goes wrong.

## Architecture at a glance

| Component | Choice |
|---|---|
| Signature scheme | ML-DSA-44 (CRYSTALS-Dilithium2, NIST PQC standard) |
| Crypto backend | liboqs (C) via liboqs-python, with dilithium_py as a loud, required fallback — never silent |
| Backend | Python 3.14, FastAPI + Uvicorn |
| Database | SQLite (WAL mode, atomic transactions under RLock) |
| Wallet | React + Vite PWA, liboqs-js for in-browser ML-DSA-44 signing |
| Max supply | 21,000,000 BIO, hard cap, immutable |
| Governance | 1 live node = 1 vote, regardless of stake tier — capital affects rewards, never voting weight |

Full technical detail is in `BioChain_Whitepaper_v5_43.docx` in this repository.

## Tokenomics — genesis distribution

Fixed at genesis, immutable, summing to the full 21,000,000 BIO cap:

| Pool | Share | Amount (BIO) |
|---|---|---|
| Validators (block rewards over time) | 40.00% | 8,400,000 |
| Ecosystem | 30.00% | 6,300,000 |
| Reserve | 20.00% | 4,200,000 |
| **Team / founder (vesting)** | **5.00%** | **1,050,000** |
| Genesis grants (first 16,000 addresses, tiered) | 3.90% | 820,000 |
| Listing reserve | 1.10% | 230,000 |

The founder's own starting balance (10,000 BIO, drawn from the genesis pool's remainder — not a separate top-level allocation) funded the first live node and, in turn, a dedicated 1,000 BIO pool granting 10 BIO each to the first 100 wallets ever registered. The same genesis pool also funds a 509,000 BIO grants pool, split evenly in v5.41 into **developer grants** (254,500 BIO, up to 5,000 BIO per grant) and **server-operator grants** (254,500 BIO, up to 2,000 BIO per grant) — both released only via governance vote. No token sale has occurred; no BIO has been sold for money by anyone at any point.

Founder vesting spans **10 years** (6-month cliff, then 114 monthly payments of ≈9,210 BIO each) — extended from an original 2-year schedule after external review. A **partial fee-burning** mechanism exists and is fully tested but launched at **0%** (governable up to 50%) — the founder chose to hold off on deflationary pressure until the network has matured. Full formulas for every mechanism are in `MATH_SPEC.md`; a plain-language walkthrough of every pool is in `TOKENOMICS.md`.

## Running a node

```bash
./install.sh
```

Handles system dependencies, builds liboqs (version-pinned for confirmed compatibility), sets up a supervised systemd service, configures the firewall (without exposing the raw API port to the public internet — a mistake we made once on our own first server and don't intend to repeat), and schedules automated database backups.

`install.sh` will tell you exactly what to do if `biochain.py` isn't in place yet — this project isn't on a public package index. See [Access](#access).

## Joining the network

The new node's own operator makes one deliberate choice -- which existing node(s) to point at -- the same pattern Bitcoin (DNS seeds, then `addr` messages) and Ethereum (bootnodes, then Kademlia) both use. Nothing beyond that requires action from anyone already running a node: see `MATH_SPEC.md` §12a for the exact formulas, and the bullet point above for why today's specific two-node network makes this end-to-end automatic in practice, not just in principle.

As of v5.41, `install.sh` asks for peer addresses interactively and stores them as systemd environment variables (`BIOCHAIN_PEER_URLS`, `BIOCHAIN_SELF_URL`) — `biochain.py` itself is never edited. Leaving the peer prompt blank falls back to `DEFAULT_BOOTSTRAP_PEERS`, a small, known-good seed list baked into the code (currently both production nodes) — the same role Bitcoin's DNS seeds and Ethereum's bootnodes play, and, like those, a list that may need updating in a future release as the network's real, trusted membership changes.

1. Run `install.sh` — for the two current production nodes, leaving the peer prompt blank is enough; it uses `DEFAULT_BOOTSTRAP_PEERS` automatically. To point at a different node instead (or if the defaults are ever retired), email us (see below) for a current address.
2. Your new node immediately starts syncing the chain from whichever peer(s) it's pointed at.
3. Your node calls `POST /peer/announce` on that existing node with a signed request (ML-DSA-44, as of July 28, 2026) — a basic liveness check runs, then it becomes a visible candidate. This alone grants no trust.
4. Once a strict majority of the existing trust set independently confirms your node through their own gossip with each other (not through anything you say about yourself), it's durably promoted into their trusted-peer list — automatically, no further manual step, and it survives restarts.

Node emergence itself (an address becoming a live, voting participant after sending impulses) has always been fully automatic and requires no introduction from anyone — see §6 in `MATH_SPEC.md` for the Sybil-resistance timing requirement added to that specific mechanism.

## Access

This repository is public. Two things from the paragraph that used to be here are still true, and still worth saying plainly rather than dropping quietly: real decentralization (multiple independently-operated nodes) and a legal review of the project's regulatory position are both still ahead, not behind. Going public now is a deliberate choice to get real engineering feedback and find independent node operators — not a claim that either of those is finished.

**Try the wallet:** [biochainnetwork.com](https://biochainnetwork.com) or [node2.biochainnetwork.com](https://node2.biochainnetwork.com) — both production nodes serve the same wallet, backed by the same live chain.

If you're interested in:
- running a node
- reviewing the cryptography or consensus logic
- post-quantum blockchain research generally

Reach out: **biochainnetwork@gmail.com**

## License

- Backend: AGPL-3.0-or-later
- Wallet: Apache-2.0
