Welcome. A few honest facts before anything else, since that's the tone of this whole project.

**What's actually running right now:**
- Two independently-operated production nodes (different countries, different data centers), synchronizing automatically, including correct fork resolution after a real (once accidental, once deliberate) network partition.
- 194-test regression suite, genuine ML-DSA-44 signatures throughout, not mocked crypto.
- Supply invariant holds to the exact satoshi across five buckets (wallets, pools, locked swaps, staked BIO, pending unstakes).
- No token sale has ever occurred. No BIO has been sold for money by anyone, at any point.

**What we're honestly missing:** more independent nodes. Two is enough to prove the peer protocol works, not enough to properly exercise majority-based governance or discovery at any real scale.

**If you want to run a node:** `backend/install.sh` handles the setup (liboqs build, systemd service, firewall, backups). Email biochainnetwork@gmail.com for the current trusted-peer address to sync against -- see `MATH_SPEC.md` section 12a for why that one manual step exists and how trust spreads automatically afterward. There's also a dedicated 254,500 BIO server-operator grants pool (see `TOKENOMICS.md` section 6b) for anyone running a genuinely independent, publicly reachable node -- released via governance vote, up to 2,000 BIO per grant.

**If you want to build something on top:** there's a 254,500 BIO developer-grants pool (see `TOKENOMICS.md` section 6a) funding wallets, explorers, SDKs, and other tooling, released via governance vote, up to 5,000 BIO per grant.

Questions, criticism, and "this specific thing is wrong" reports are all welcome here. We've documented real production bugs as we found them (see the whitepaper's changelog) rather than pretending everything worked the first time -- happy to keep doing that in the open.
