// SPDX-License-Identifier: Apache-2.0
// Licensed separately from the BioChain server (which is AGPL-3.0-or-later) --
// see the LICENSE file in this wallet's own directory for the full text and reasoning.
import { useState, useEffect, useCallback, useRef } from "react";

const VERSION   = "2.2.5";
// v2.2.5: automatic, one-time wallet-registration grant (10 BIO,
// first 100 wallets ever created). Fires silently right after a new
// wallet is confirmed and saved locally -- see tryWalletRegistration()
// and its call site in handleSaveAndOpen(). Failure (slots exhausted,
// network issue) is deliberately silent -- this is a bonus, not
// something the wallet's core function depends on.
// v2.2.4: readability pass -- all UI font sizes bumped up (7->9, 8->10,
// 9->11, 10->12, 11->13, 12->14, 13->14) for legibility on phone screens.
// Large display numbers (balance, headers -- 16 and above) left untouched,
// they were already prominent. Single-pass regex mapping avoided cascading
// double-increments; SVG logo decoration (fontSize="N" attribute form)
// is untouched by design, matched pattern only targets JS object syntax.
// v2.2.3: biometric registration errors now show the real
// WebAuthn exception (name + message) instead of a fixed generic
// string -- needed to diagnose a real "Biometric registration
// failed" report where the underlying cause was invisible.
// v2.2.2: REAL bug found -- both the NETWORK and SWAP background-poll
// effects checked screen!=="wallet", but the screen value after login
// is actually "main" (see line ~1282, done correctly there). NETWORK
// never had a duplicate direct call like the swap tab-switch buttons
// do, so its effect never fired at all -- explains the permanent
// "Loading..." with no error, since fetchDashboard was never called.
// v2.2.1: dashboard fetch errors surfaced on screen instead of silently
// swallowed -- needed to diagnose a real "stuck on Loading" report.
// v2.2.0: NETWORK tab -- transparency dashboard reading GET /dashboard
// (node counts, tier/role distribution, balance & stake concentration
// among live nodes, synchronized-birth clusters as a weak Sybil-burst
// signal). Honest limitation shown as-is from the server: no IP-based
// farm detection exists anywhere in this architecture.
// v2.1.1: removed the hardcoded want_asset="BTC" default entirely --
// the offer form now has a real "what do you want" text field the
// person fills in themselves. No asset name is assumed or suggested;
// Bitcoin is not referenced anywhere in the wallet.
// v2.1.0: swap UI decoupled from Bitcoin specifics. The Bitcoin/Taproot
// path (spec v0.4) is shelved, not deleted -- priority moved to swaps
// with future ML-DSA-compatible networks (spec v0.5). want_asset stays
// a free-text field (server default "BTC" is harmless, unenforced by
// consensus); wallet copy no longer hardcodes Bitcoin terminology so
// the same screens work for whatever external asset comes first.
// v2.0.1: fixed pubkey format in all five SWAP calls -- they sent
// wallet.pubkeyB64 (base64) while every other signed endpoint (TX,
// STAKE, etc.) sends bytesToHex(keyRef.current.publicKey). Two
// different encodings of the same key would have failed every swap
// call at the server's pubkey<->address check. Caught by a second,
// targeted review pass -- not by the first build.
// v2.0.0: HTLC atomic-swap UI (server v5.37). New SWAP tab with three
// panels -- Order board / My deals / History -- backed by the in-chain
// order board (SWAP_OFFER) and hash-locked escrow (SWAP_LOCK/CLAIM/
// REFUND). The wallet handles ONLY the BioChain side; the external
// asset's own wallet handles its own side -- the preimage is the only
// thing that ever crosses between the two. One-time money-until-
// revealed, kept only in memory during an active deal, never in the
// seed backup.
const EXT_UNIT = 100_000_000;   // 8-decimal display scale for external-asset amounts

// SHA-256 hex of a hex-string preimage (mirrors server: bytes.fromhex -> sha256)
async function sha256HexOfHex(hexStr){
  const bytes = new Uint8Array(hexStr.match(/.{2}/g).map(b=>parseInt(b,16)));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,"0")).join("");
}
// Fresh 32-byte preimage as 64 hex chars
function newPreimage(){
  const a = new Uint8Array(32); crypto.getRandomValues(a);
  return [...a].map(x=>x.toString(16).padStart(2,"0")).join("");
}
// v1.9.7: mobile viewport bounce -- Chrome on Android rubber-bands
// (small vertical shift) on any touch/scroll when the page has no
// explicit overscroll containment. Fixed at the same body/html paint
// point introduced in v1.9.6, no layout logic touched.
// v1.9.6: full-screen fix -- the page (html/body) behind the app kept
// the browser's default white background and 8px margin, so after
// viewport re-layout (e.g. returning from Settings) white bars showed
// around the app. The page background/margins are now synced with the
// active theme, and the root container gets explicit width:100%.
// v1.9.5: int-money alignment -- q8() quantizes amounts to 8 decimals
// BEFORE signing, so the wallet's canonical string can never disagree
// with the server's bio_to_sat() on half-way rounding (JS toFixed
// rounds half-up, Python :.8f rounds half-even). Fee display fixed to
// the real protocol: 0.01 BIO flat + 0.05%.
const q8 = (x) => (Math.round(parseFloat(x) * 1e8) / 1e8).toFixed(8);
const SETTINGS_KEY = "biochain_settings_v1";

function loadSettings(){
  try{
    const s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null");
    return {
      theme: s?.theme === "light" ? "light" : "dark",
      notifEnabled:   s?.notifEnabled   ?? false,
      notifIncoming:  s?.notifIncoming  ?? true,
      notifValidated: s?.notifValidated ?? true,
    };
  }catch{
    return { theme:"dark", notifEnabled:false, notifIncoming:true, notifValidated:true };
  }
}
function saveSettings(s){ localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); }

// ── PWA: всё в одном файле — манифест собирается в JS и подключается как Blob ──
// Никаких отдельных manifest.json / иконок / service-worker — один App.jsx.
const PWA_ICON_SVG_B64 = "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyMDAgMjAwIj4KICA8cmVjdCB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgZmlsbD0iIzA4MTYwOCIvPgogIDxjaXJjbGUgY3g9IjEwMCIgY3k9IjEwMCIgcj0iOTIiIGZpbGw9Im5vbmUiIHN0cm9rZT0iIzAwQzlCMSIgc3Ryb2tlLXdpZHRoPSI2Ii8+CiAgPHBhdGggZD0iTSA3OCAzNiBDIDU4IDU2LDU4IDc2LDc4IDk2IEMgOTggMTE2LDk4IDEzNiw3OCAxNTYgQyA3MCAxNjMsNzAgMTcwLDgwIDE3NiIKICAgIGZpbGw9Im5vbmUiIHN0cm9rZT0iIzAwQzlCMSIgc3Ryb2tlLXdpZHRoPSI5IiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KICA8cGF0aCBkPSJNIDEyMiAzNiBDIDE0MiA1NiwxNDIgNzYsMTIyIDk2IEMgMTAyIDExNiwxMDIgMTM2LDEyMiAxNTYgQyAxMzAgMTYzLDEzMCAxNzAsMTIwIDE3NiIKICAgIGZpbGw9Im5vbmUiIHN0cm9rZT0iIzJFQ0M3MSIgc3Ryb2tlLXdpZHRoPSI5IiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KICA8bGluZSB4MT0iODAiIHkxPSI2MCIgeDI9IjEyMCIgeTI9IjYwIiBzdHJva2U9IiMwMEM5QjEiIHN0cm9rZS13aWR0aD0iNCIvPgogIDxsaW5lIHgxPSI3NiIgeTE9Ijk2IiB4Mj0iMTI0IiB5Mj0iOTYiIHN0cm9rZT0iIzJFQ0M3MSIgc3Ryb2tlLXdpZHRoPSI1Ii8+CiAgPGxpbmUgeDE9IjgwIiB5MT0iMTMyIiB4Mj0iMTIwIiB5Mj0iMTMyIiBzdHJva2U9IiMwMEM5QjEiIHN0cm9rZS13aWR0aD0iNCIvPgogIDxjaXJjbGUgY3g9IjEwMCIgY3k9Ijk2IiByPSIxNiIgZmlsbD0iIzA4MTYwOCIgc3Ryb2tlPSIjMkVDQzcxIiBzdHJva2Utd2lkdGg9IjMiLz4KICA8cG9seWdvbiBwb2ludHM9IjEwMCw4NyAxMDksOTYgMTAwLDEwNSA5MSw5NiIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjMDBDOUIxIiBzdHJva2Utd2lkdGg9IjMiLz4KPC9zdmc+Cg==";

function setupPWA(){
  if (typeof document === "undefined") return;
  if (document.querySelector('link[rel="manifest"]')) return; // уже подключено

  const iconDataUri = `data:image/svg+xml;base64,${PWA_ICON_SVG_B64}`;
  const manifest = {
    name: "BioChain Wallet",
    short_name: "BioChain",
    description: "Post-quantum self-evolving wallet for the BioChain network",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#030810",
    theme_color: "#00C9B1",
    icons: [
      { src: iconDataUri, sizes: "192x192", type: "image/svg+xml", purpose: "any" },
      { src: iconDataUri, sizes: "512x512", type: "image/svg+xml", purpose: "any" },
      { src: iconDataUri, sizes: "192x192", type: "image/svg+xml", purpose: "maskable" },
      { src: iconDataUri, sizes: "512x512", type: "image/svg+xml", purpose: "maskable" },
    ],
  };

  const blob = new Blob([JSON.stringify(manifest)], { type: "application/manifest+json" });
  const manifestUrl = URL.createObjectURL(blob);

  const link = document.createElement("link");
  link.rel = "manifest";
  link.href = manifestUrl;
  document.head.appendChild(link);

  if (!document.querySelector('meta[name="theme-color"]')) {
    const meta = document.createElement("meta");
    meta.name = "theme-color";
    meta.content = "#00C9B1";
    document.head.appendChild(meta);
  }
  // Service worker не используется — офлайн-кэш не критичен для кошелька,
  // который и так работает только при наличии связи с бэкендом.
}
const API       = "https://node2.biochainnetwork.com/api";
const STORE_KEY = "biochain_wallet_v1";
const BIO_KEY   = "biochain_biometric_v1";

// ── WORDLIST ──────────────────────────────────────────────────────────────
const WORDLIST = [
  // Full official BIP39 English wordlist -- 2048 words, cryptographically
  // required for real seed-phrase entropy. The previous list here was
  // truncated at 224 words (only A/B words), silently cutting the real
  // entropy of every seed phrase this wallet ever generated by orders of
  // magnitude, no matter which word-count option (12/24/etc) was chosen --
  // n % WORDLIST.length simply could never land past "burger". Verified
  // against the official BIP39 specification: exactly 2048 unique words,
  // alphabetically sorted, first="abandon" last="zoo".
  "abandon","ability","able","about","above","absent","absorb","abstract",
  "absurd","abuse","access","accident","account","accuse","achieve","acid",
  "acoustic","acquire","across","act","action","actor","actress","actual",
  "adapt","add","addict","address","adjust","admit","adult","advance",
  "advice","aerobic","affair","afford","afraid","again","age","agent",
  "agree","ahead","aim","air","airport","aisle","alarm","album",
  "alcohol","alert","alien","all","alley","allow","almost","alone",
  "alpha","already","also","alter","always","amateur","amazing","among",
  "amount","amused","analyst","anchor","ancient","anger","angle","angry",
  "animal","ankle","announce","annual","another","answer","antenna","antique",
  "anxiety","any","apart","apology","appear","apple","approve","april",
  "arch","arctic","area","arena","argue","arm","armed","armor",
  "army","around","arrange","arrest","arrive","arrow","art","artefact",
  "artist","artwork","ask","aspect","assault","asset","assist","assume",
  "asthma","athlete","atom","attack","attend","attitude","attract","auction",
  "audit","august","aunt","author","auto","autumn","average","avocado",
  "avoid","awake","aware","away","awesome","awful","awkward","axis",
  "baby","bachelor","bacon","badge","bag","balance","balcony","ball",
  "bamboo","banana","banner","bar","barely","bargain","barrel","base",
  "basic","basket","battle","beach","bean","beauty","because","become",
  "beef","before","begin","behave","behind","believe","below","belt",
  "bench","benefit","best","betray","better","between","beyond","bicycle",
  "bid","bike","bind","biology","bird","birth","bitter","black",
  "blade","blame","blanket","blast","bleak","bless","blind","blood",
  "blossom","blouse","blue","blur","blush","board","boat","body",
  "boil","bomb","bone","bonus","book","boost","border","boring",
  "borrow","boss","bottom","bounce","box","boy","bracket","brain",
  "brand","brass","brave","bread","breeze","brick","bridge","brief",
  "bright","bring","brisk","broccoli","broken","bronze","broom","brother",
  "brown","brush","bubble","buddy","budget","buffalo","build","bulb",
  "bulk","bullet","bundle","bunker","burden","burger","burst","bus",
  "business","busy","butter","buyer","buzz","cabbage","cabin","cable",
  "cactus","cage","cake","call","calm","camera","camp","can",
  "canal","cancel","candy","cannon","canoe","canvas","canyon","capable",
  "capital","captain","car","carbon","card","cargo","carpet","carry",
  "cart","case","cash","casino","castle","casual","cat","catalog",
  "catch","category","cattle","caught","cause","caution","cave","ceiling",
  "celery","cement","census","century","cereal","certain","chair","chalk",
  "champion","change","chaos","chapter","charge","chase","chat","cheap",
  "check","cheese","chef","cherry","chest","chicken","chief","child",
  "chimney","choice","choose","chronic","chuckle","chunk","churn","cigar",
  "cinnamon","circle","citizen","city","civil","claim","clap","clarify",
  "claw","clay","clean","clerk","clever","click","client","cliff",
  "climb","clinic","clip","clock","clog","close","cloth","cloud",
  "clown","club","clump","cluster","clutch","coach","coast","coconut",
  "code","coffee","coil","coin","collect","color","column","combine",
  "come","comfort","comic","common","company","concert","conduct","confirm",
  "congress","connect","consider","control","convince","cook","cool","copper",
  "copy","coral","core","corn","correct","cost","cotton","couch",
  "country","couple","course","cousin","cover","coyote","crack","cradle",
  "craft","cram","crane","crash","crater","crawl","crazy","cream",
  "credit","creek","crew","cricket","crime","crisp","critic","crop",
  "cross","crouch","crowd","crucial","cruel","cruise","crumble","crunch",
  "crush","cry","crystal","cube","culture","cup","cupboard","curious",
  "current","curtain","curve","cushion","custom","cute","cycle","dad",
  "damage","damp","dance","danger","daring","dash","daughter","dawn",
  "day","deal","debate","debris","decade","december","decide","decline",
  "decorate","decrease","deer","defense","define","defy","degree","delay",
  "deliver","demand","demise","denial","dentist","deny","depart","depend",
  "deposit","depth","deputy","derive","describe","desert","design","desk",
  "despair","destroy","detail","detect","develop","device","devote","diagram",
  "dial","diamond","diary","dice","diesel","diet","differ","digital",
  "dignity","dilemma","dinner","dinosaur","direct","dirt","disagree","discover",
  "disease","dish","dismiss","disorder","display","distance","divert","divide",
  "divorce","dizzy","doctor","document","dog","doll","dolphin","domain",
  "donate","donkey","donor","door","dose","double","dove","draft",
  "dragon","drama","drastic","draw","dream","dress","drift","drill",
  "drink","drip","drive","drop","drum","dry","duck","dumb",
  "dune","during","dust","dutch","duty","dwarf","dynamic","eager",
  "eagle","early","earn","earth","easily","east","easy","echo",
  "ecology","economy","edge","edit","educate","effort","egg","eight",
  "either","elbow","elder","electric","elegant","element","elephant","elevator",
  "elite","else","embark","embody","embrace","emerge","emotion","employ",
  "empower","empty","enable","enact","end","endless","endorse","enemy",
  "energy","enforce","engage","engine","enhance","enjoy","enlist","enough",
  "enrich","enroll","ensure","enter","entire","entry","envelope","episode",
  "equal","equip","era","erase","erode","erosion","error","erupt",
  "escape","essay","essence","estate","eternal","ethics","evidence","evil",
  "evoke","evolve","exact","example","excess","exchange","excite","exclude",
  "excuse","execute","exercise","exhaust","exhibit","exile","exist","exit",
  "exotic","expand","expect","expire","explain","expose","express","extend",
  "extra","eye","eyebrow","fabric","face","faculty","fade","faint",
  "faith","fall","false","fame","family","famous","fan","fancy",
  "fantasy","farm","fashion","fat","fatal","father","fatigue","fault",
  "favorite","feature","february","federal","fee","feed","feel","female",
  "fence","festival","fetch","fever","few","fiber","fiction","field",
  "figure","file","film","filter","final","find","fine","finger",
  "finish","fire","firm","first","fiscal","fish","fit","fitness",
  "fix","flag","flame","flash","flat","flavor","flee","flight",
  "flip","float","flock","floor","flower","fluid","flush","fly",
  "foam","focus","fog","foil","fold","follow","food","foot",
  "force","forest","forget","fork","fortune","forum","forward","fossil",
  "foster","found","fox","fragile","frame","frequent","fresh","friend",
  "fringe","frog","front","frost","frown","frozen","fruit","fuel",
  "fun","funny","furnace","fury","future","gadget","gain","galaxy",
  "gallery","game","gap","garage","garbage","garden","garlic","garment",
  "gas","gasp","gate","gather","gauge","gaze","general","genius",
  "genre","gentle","genuine","gesture","ghost","giant","gift","giggle",
  "ginger","giraffe","girl","give","glad","glance","glare","glass",
  "glide","glimpse","globe","gloom","glory","glove","glow","glue",
  "goat","goddess","gold","good","goose","gorilla","gospel","gossip",
  "govern","gown","grab","grace","grain","grant","grape","grass",
  "gravity","great","green","grid","grief","grit","grocery","group",
  "grow","grunt","guard","guess","guide","guilt","guitar","gun",
  "gym","habit","hair","half","hammer","hamster","hand","happy",
  "harbor","hard","harsh","harvest","hat","have","hawk","hazard",
  "head","health","heart","heavy","hedgehog","height","hello","helmet",
  "help","hen","hero","hidden","high","hill","hint","hip",
  "hire","history","hobby","hockey","hold","hole","holiday","hollow",
  "home","honey","hood","hope","horn","horror","horse","hospital",
  "host","hotel","hour","hover","hub","huge","human","humble",
  "humor","hundred","hungry","hunt","hurdle","hurry","hurt","husband",
  "hybrid","ice","icon","idea","identify","idle","ignore","ill",
  "illegal","illness","image","imitate","immense","immune","impact","impose",
  "improve","impulse","inch","include","income","increase","index","indicate",
  "indoor","industry","infant","inflict","inform","inhale","inherit","initial",
  "inject","injury","inmate","inner","innocent","input","inquiry","insane",
  "insect","inside","inspire","install","intact","interest","into","invest",
  "invite","involve","iron","island","isolate","issue","item","ivory",
  "jacket","jaguar","jar","jazz","jealous","jeans","jelly","jewel",
  "job","join","joke","journey","joy","judge","juice","jump",
  "jungle","junior","junk","just","kangaroo","keen","keep","ketchup",
  "key","kick","kid","kidney","kind","kingdom","kiss","kit",
  "kitchen","kite","kitten","kiwi","knee","knife","knock","know",
  "lab","label","labor","ladder","lady","lake","lamp","language",
  "laptop","large","later","latin","laugh","laundry","lava","law",
  "lawn","lawsuit","layer","lazy","leader","leaf","learn","leave",
  "lecture","left","leg","legal","legend","leisure","lemon","lend",
  "length","lens","leopard","lesson","letter","level","liar","liberty",
  "library","license","life","lift","light","like","limb","limit",
  "link","lion","liquid","list","little","live","lizard","load",
  "loan","lobster","local","lock","logic","lonely","long","loop",
  "lottery","loud","lounge","love","loyal","lucky","luggage","lumber",
  "lunar","lunch","luxury","lyrics","machine","mad","magic","magnet",
  "maid","mail","main","major","make","mammal","man","manage",
  "mandate","mango","mansion","manual","maple","marble","march","margin",
  "marine","market","marriage","mask","mass","master","match","material",
  "math","matrix","matter","maximum","maze","meadow","mean","measure",
  "meat","mechanic","medal","media","melody","melt","member","memory",
  "mention","menu","mercy","merge","merit","merry","mesh","message",
  "metal","method","middle","midnight","milk","million","mimic","mind",
  "minimum","minor","minute","miracle","mirror","misery","miss","mistake",
  "mix","mixed","mixture","mobile","model","modify","mom","moment",
  "monitor","monkey","monster","month","moon","moral","more","morning",
  "mosquito","mother","motion","motor","mountain","mouse","move","movie",
  "much","muffin","mule","multiply","muscle","museum","mushroom","music",
  "must","mutual","myself","mystery","myth","naive","name","napkin",
  "narrow","nasty","nation","nature","near","neck","need","negative",
  "neglect","neither","nephew","nerve","nest","net","network","neutral",
  "never","news","next","nice","night","noble","noise","nominee",
  "noodle","normal","north","nose","notable","note","nothing","notice",
  "novel","now","nuclear","number","nurse","nut","oak","obey",
  "object","oblige","obscure","observe","obtain","obvious","occur","ocean",
  "october","odor","off","offer","office","often","oil","okay",
  "old","olive","olympic","omit","once","one","onion","online",
  "only","open","opera","opinion","oppose","option","orange","orbit",
  "orchard","order","ordinary","organ","orient","original","orphan","ostrich",
  "other","outdoor","outer","output","outside","oval","oven","over",
  "own","owner","oxygen","oyster","ozone","pact","paddle","page",
  "pair","palace","palm","panda","panel","panic","panther","paper",
  "parade","parent","park","parrot","party","pass","patch","path",
  "patient","patrol","pattern","pause","pave","payment","peace","peanut",
  "pear","peasant","pelican","pen","penalty","pencil","people","pepper",
  "perfect","permit","person","pet","phone","photo","phrase","physical",
  "piano","picnic","picture","piece","pig","pigeon","pill","pilot",
  "pink","pioneer","pipe","pistol","pitch","pizza","place","planet",
  "plastic","plate","play","please","pledge","pluck","plug","plunge",
  "poem","poet","point","polar","pole","police","pond","pony",
  "pool","popular","portion","position","possible","post","potato","pottery",
  "poverty","powder","power","practice","praise","predict","prefer","prepare",
  "present","pretty","prevent","price","pride","primary","print","priority",
  "prison","private","prize","problem","process","produce","profit","program",
  "project","promote","proof","property","prosper","protect","proud","provide",
  "public","pudding","pull","pulp","pulse","pumpkin","punch","pupil",
  "puppy","purchase","purity","purpose","purse","push","put","puzzle",
  "pyramid","quality","quantum","quarter","question","quick","quit","quiz",
  "quote","rabbit","raccoon","race","rack","radar","radio","rail",
  "rain","raise","rally","ramp","ranch","random","range","rapid",
  "rare","rate","rather","raven","raw","razor","ready","real",
  "reason","rebel","rebuild","recall","receive","recipe","record","recycle",
  "reduce","reflect","reform","refuse","region","regret","regular","reject",
  "relax","release","relief","rely","remain","remember","remind","remove",
  "render","renew","rent","reopen","repair","repeat","replace","report",
  "require","rescue","resemble","resist","resource","response","result","retire",
  "retreat","return","reunion","reveal","review","reward","rhythm","rib",
  "ribbon","rice","rich","ride","ridge","rifle","right","rigid",
  "ring","riot","ripple","risk","ritual","rival","river","road",
  "roast","robot","robust","rocket","romance","roof","rookie","room",
  "rose","rotate","rough","round","route","royal","rubber","rude",
  "rug","rule","run","runway","rural","sad","saddle","sadness",
  "safe","sail","salad","salmon","salon","salt","salute","same",
  "sample","sand","satisfy","satoshi","sauce","sausage","save","say",
  "scale","scan","scare","scatter","scene","scheme","school","science",
  "scissors","scorpion","scout","scrap","screen","script","scrub","sea",
  "search","season","seat","second","secret","section","security","seed",
  "seek","segment","select","sell","seminar","senior","sense","sentence",
  "series","service","session","settle","setup","seven","shadow","shaft",
  "shallow","share","shed","shell","sheriff","shield","shift","shine",
  "ship","shiver","shock","shoe","shoot","shop","short","shoulder",
  "shove","shrimp","shrug","shuffle","shy","sibling","sick","side",
  "siege","sight","sign","silent","silk","silly","silver","similar",
  "simple","since","sing","siren","sister","situate","six","size",
  "skate","sketch","ski","skill","skin","skirt","skull","slab",
  "slam","sleep","slender","slice","slide","slight","slim","slogan",
  "slot","slow","slush","small","smart","smile","smoke","smooth",
  "snack","snake","snap","sniff","snow","soap","soccer","social",
  "sock","soda","soft","solar","soldier","solid","solution","solve",
  "someone","song","soon","sorry","sort","soul","sound","soup",
  "source","south","space","spare","spatial","spawn","speak","special",
  "speed","spell","spend","sphere","spice","spider","spike","spin",
  "spirit","split","spoil","sponsor","spoon","sport","spot","spray",
  "spread","spring","spy","square","squeeze","squirrel","stable","stadium",
  "staff","stage","stairs","stamp","stand","start","state","stay",
  "steak","steel","stem","step","stereo","stick","still","sting",
  "stock","stomach","stone","stool","story","stove","strategy","street",
  "strike","strong","struggle","student","stuff","stumble","style","subject",
  "submit","subway","success","such","sudden","suffer","sugar","suggest",
  "suit","summer","sun","sunny","sunset","super","supply","supreme",
  "sure","surface","surge","surprise","surround","survey","suspect","sustain",
  "swallow","swamp","swap","swarm","swear","sweet","swift","swim",
  "swing","switch","sword","symbol","symptom","syrup","system","table",
  "tackle","tag","tail","talent","talk","tank","tape","target",
  "task","taste","tattoo","taxi","teach","team","tell","ten",
  "tenant","tennis","tent","term","test","text","thank","that",
  "theme","then","theory","there","they","thing","this","thought",
  "three","thrive","throw","thumb","thunder","ticket","tide","tiger",
  "tilt","timber","time","tiny","tip","tired","tissue","title",
  "toast","tobacco","today","toddler","toe","together","toilet","token",
  "tomato","tomorrow","tone","tongue","tonight","tool","tooth","top",
  "topic","topple","torch","tornado","tortoise","toss","total","tourist",
  "toward","tower","town","toy","track","trade","traffic","tragic",
  "train","transfer","trap","trash","travel","tray","treat","tree",
  "trend","trial","tribe","trick","trigger","trim","trip","trophy",
  "trouble","truck","true","truly","trumpet","trust","truth","try",
  "tube","tuition","tumble","tuna","tunnel","turkey","turn","turtle",
  "twelve","twenty","twice","twin","twist","two","type","typical",
  "ugly","umbrella","unable","unaware","uncle","uncover","under","undo",
  "unfair","unfold","unhappy","uniform","unique","unit","universe","unknown",
  "unlock","until","unusual","unveil","update","upgrade","uphold","upon",
  "upper","upset","urban","urge","usage","use","used","useful",
  "useless","usual","utility","vacant","vacuum","vague","valid","valley",
  "valve","van","vanish","vapor","various","vast","vault","vehicle",
  "velvet","vendor","venture","venue","verb","verify","version","very",
  "vessel","veteran","viable","vibrant","vicious","victory","video","view",
  "village","vintage","violin","virtual","virus","visa","visit","visual",
  "vital","vivid","vocal","voice","void","volcano","volume","vote",
  "voyage","wage","wagon","wait","walk","wall","walnut","want",
  "warfare","warm","warrior","wash","wasp","waste","water","wave",
  "way","wealth","weapon","wear","weasel","weather","web","wedding",
  "weekend","weird","welcome","west","wet","whale","what","wheat",
  "wheel","when","where","whip","whisper","wide","width","wife",
  "wild","will","win","window","wine","wing","wink","winner",
  "winter","wire","wisdom","wise","wish","witness","wolf","woman",
  "wonder","wood","wool","word","work","world","worry","worth",
  "wrap","wreck","wrestle","wrist","write","wrong","yard","year",
  "yellow","you","young","youth","zebra","zero","zone","zoo",
];

const SEED_OPTIONS = [
  { words:12, bits:128, desc:"Standard" },
  { words:15, bits:160, desc:"Enhanced" },
  { words:18, bits:192, desc:"Strong" },
  { words:21, bits:224, desc:"Very strong" },
  { words:24, bits:256, desc:"Maximum ★", star:true },
];

// ── CRYPTO ────────────────────────────────────────────────────────────────
function generateSeed(count=24){
  const arr=new Uint32Array(count);
  crypto.getRandomValues(arr);
  return Array.from(arr).map(n=>WORDLIST[n%WORDLIST.length]);
}

async function deriveKey(password, salt){
  const keyMat=await crypto.subtle.importKey("raw",new TextEncoder().encode(password),"PBKDF2",false,["deriveKey"]);
  return crypto.subtle.deriveKey(
    {name:"PBKDF2",salt,iterations:200000,hash:"SHA-256"},
    keyMat,{name:"AES-GCM",length:256},false,["encrypt","decrypt"]
  );
}

async function encryptData(data, password){
  const salt=crypto.getRandomValues(new Uint8Array(16));
  const iv=crypto.getRandomValues(new Uint8Array(12));
  const key=await deriveKey(password,salt);
  const cipher=await crypto.subtle.encrypt({name:"AES-GCM",iv},key,new TextEncoder().encode(data));
  return{ salt:Array.from(salt), iv:Array.from(iv), cipher:Array.from(new Uint8Array(cipher)) };
}

async function decryptData(stored, password){
  const key=await deriveKey(password,new Uint8Array(stored.salt));
  const plain=await crypto.subtle.decrypt(
    {name:"AES-GCM",iv:new Uint8Array(stored.iv)},
    key, new Uint8Array(stored.cipher)
  );
  return new TextDecoder().decode(plain);
}

// ── REAL POST-QUANTUM KEYS (ML-DSA-44 via liboqs-js) ────────────────────
// Verified working end-to-end on a real device before this code was written:
// liboqs-js (browser/Vite) signs -> Python dilithium_py (server) verifies True.
// generateKeyPair() takes NO seed (liboqs has no deterministic keygen for
// signatures, only for KEM) -- so the 24-word phrase below is a PASSWORD
// that encrypts the real key for backup, not a source the key is derived from.
let _sigInstance = null;
async function getSig(){
  if(!_sigInstance){
    const liboqsPath = window.location.origin + "/liboqs/src/index.js";
    const oqs = await import(/* @vite-ignore */ liboqsPath);
    _sigInstance = await oqs.createMLDSA44();
  }
  return _sigInstance;
}
async function getSha3_256(){
  const pkg = await import("js-sha3");
  return (pkg.default ?? pkg).sha3_256;
}
function bytesToHex(bytes){ return Array.from(bytes).map(b=>b.toString(16).padStart(2,"0")).join(""); }
function hexToBytes(hex){ const a=new Uint8Array(hex.length/2); for(let i=0;i<a.length;i++) a[i]=parseInt(hex.substr(i*2,2),16); return a; }
function bytesToBase64(bytes){ let s=""; for(const b of bytes) s+=String.fromCharCode(b); return btoa(s); }
function base64ToBytes(b64){ const s=atob(b64); const a=new Uint8Array(s.length); for(let i=0;i<s.length;i++) a[i]=s.charCodeAt(i); return a; }

async function generateRealKeypair(){
  const sig = await getSig();
  const kp = sig.generateKeyPair();
  return { publicKey: kp.publicKey, secretKey: kp.secretKey };
}

// Must match the backend EXACTLY: "BIO1" + sha3_256(pubkey_bytes)[:16].upper()
// (see pq.address() in biochain.py) -- verified byte-identical on real device.
async function addressFromPubkey(pubkeyBytes){
  const sha3_256 = await getSha3_256();
  const hashHex = sha3_256(pubkeyBytes);
  return "BIO1" + hashHex.slice(0,16).toUpperCase();
}

// Signs `messageStr` exactly as the backend expects it (see verify_signed_request
// in biochain.py) and returns hex. Used for every fund/governance-affecting call.
async function signMessage(secretKeyBytes, messageStr){
  const sig = await getSig();
  const message = new TextEncoder().encode(messageStr);
  const signature = sig.sign(message, secretKeyBytes);
  return bytesToHex(signature);
}

// Builds the {pubkey, signature, timestamp} trio every signed endpoint needs.
async function buildSignedFields(keypair, messageStr){
  const timestamp = Date.now()/1000;
  const signature = await signMessage(keypair.secretKey, messageStr);
  return { pubkey: bytesToHex(keypair.publicKey), signature, timestamp };
}

// Fetches this address's next valid nonce from the server. Called right
// before signing any fund/governance action -- the signed message must
// include this exact number (see signed_message in biochain.py), or the
// server rejects it as a bad nonce even with an otherwise-valid signature.
async function getNextNonce(address){
  try{
    const r = await fetch(`${API}/nonce/${address}`);
    const d = await r.json();
    return d.next;
  }catch{
    return 1;   // server unreachable -- best-effort fallback, the
                // request will simply fail its own way if this is wrong
  }
}

// v5.40/wallet v2.2.5: one-time wallet-registration grant. Signs and
// sends a REGISTER impulse for a brand-new address -- see the server's
// own docstring on POST /register for why this has to be a real signed
// chain event (not a passive side effect of any read), so a made-up or
// merely-looked-up address can never consume one of the first 100 slots.
// Deliberately silent on failure: this is a bonus for early wallets,
// not something the wallet's core functionality depends on -- once the
// 100 slots are gone, every later wallet creation will get a rejection
// from this call and that's expected, not an error worth surfacing.
async function tryWalletRegistration(keypair, address){
  const nonce = await getNextNonce(address);
  const timestamp = Date.now()/1000;
  const msgStr = `REGISTER|${address}|${timestamp.toFixed(6)}|${nonce}`;
  const signature = await signMessage(keypair.secretKey, msgStr);
  const res = await fetch(`${API}/register`,{
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({
      address, pubkey: bytesToHex(keypair.publicKey), signature, timestamp, nonce
    }),
    signal: AbortSignal.timeout(5000),
  });
  return res.json();
}

// ── KEYSTORE -- backup/export of the real secret key, encrypted with the
// 24-word phrase (used as a PBKDF2 password, see deriveKey above). Recovery
// on a new device needs BOTH this file AND the 24 words -- the words alone
// cannot regenerate the key, since liboqs-js keygen isn't seedable.
async function exportKeystore(keypair, seedPhraseStr, address){
  const skB64 = bytesToBase64(keypair.secretKey);
  const pkB64 = bytesToBase64(keypair.publicKey);
  const encryptedSk = await encryptData(skB64, seedPhraseStr);
  // No pretty-printing (no `null, 2`) -- the ML-DSA-44 keys inside this
  // JSON are inherently large (~5400+ characters total, post-quantum
  // signatures cost real space), so every byte of formatting whitespace
  // directly adds to how many parts a length-limited channel (like plain
  // SMS, 160 chars/segment) has to split this into. This alone can't make
  // it fit in ONE SMS -- that's a hard protocol limit, not something this
  // app can remove -- but it does meaningfully cut the part count on any
  // channel with a real (if larger) limit.
  return JSON.stringify({
    version: 1, address, pubkeyB64: pkB64,
    encryptedSecretKey: encryptedSk, created: Date.now(),
  });
}

async function importKeystore(fileText, seedPhraseStr){
  const data = JSON.parse(fileText);
  const skB64 = await decryptData(data.encryptedSecretKey, seedPhraseStr);
  return {
    publicKey: base64ToBytes(data.pubkeyB64),
    secretKey: base64ToBytes(skB64),
    address: data.address,
  };
}

function downloadTextFile(filename, text){
  const blob = new Blob([text], {type:"application/json"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// Opens the native Android "Share" sheet (Bluetooth, Nearby Share, WhatsApp,
// email, Drive, etc. all show up there) -- this is what actually gets a file
// from one phone to another, not the Web Bluetooth API (that's for BLE
// sensors/IoT, not OBEX file transfer; there is no way for a website to
// drive classic Bluetooth file sharing directly).
async function shareText(text, title){
  // Sharing as FILES (application/json) depends on the device having at
  // least one app registered to receive that specific MIME type via the
  // share sheet -- confirmed on a real device to fail outright
  // (NotAllowedError, consistently, not a timing fluke) when nothing is
  // registered for it. Sharing as plain TEXT instead is handled by
  // virtually every share-sheet target on Android (Bluetooth, email, any
  // messaging app, "Save to Drive") because text/plain is the one format
  // almost nothing refuses. The tradeoff: the person on the receiving end
  // gets raw text, not a ready .json file -- see the "paste keystore
  // text" option added to Restore/import for that other half of the loop.
  if(!navigator.share) return "unsupported";
  try{
    await navigator.share({text, title: title||"BioChain Keystore"});
    return "shared";
  }catch(e){
    if(e.name==="AbortError") return "cancelled"; // user backed out of the share sheet
    throw e;
  }
}

// ── BIOMETRIC (WebAuthn) ──────────────────────────────────────────────────
const isBiometricSupported = () =>
  window.PublicKeyCredential &&
  typeof PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable === "function";

async function checkBiometricAvailable(){
  if(!isBiometricSupported()) return false;
  try{
    // isUserVerifyingPlatformAuthenticatorAvailable() has no built-in
    // timeout, and on some Android devices (especially after an OS/Google
    // Play Services update) it can hang indefinitely instead of resolving
    // or rejecting -- which previously left the splash screen stuck
    // forever, since this call is awaited before the app ever decides
    // which screen to show. Race it against a timeout so a broken
    // platform authenticator degrades to "biometric unavailable" instead
    // of blocking the whole app from starting.
    const result = await Promise.race([
      PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable(),
      new Promise(resolve => setTimeout(() => resolve(false), 3000)),
    ]);
    return result;
  }catch{ return false; }
}

function base64url(buffer){
  const bytes=new Uint8Array(buffer);
  let str="";
  for(const b of bytes) str+=String.fromCharCode(b);
  return btoa(str).replace(/\+/g,"-").replace(/\//g,"_").replace(/=/g,"");
}

function fromBase64url(str){
  str=str.replace(/-/g,"+").replace(/_/g,"/");
  while(str.length%4) str+="=";
  const bin=atob(str);
  const buf=new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++) buf[i]=bin.charCodeAt(i);
  return buf.buffer;
}

// Register biometric — stores credential ID + encrypted seed key
async function registerBiometric(seedStr){
  const challenge=crypto.getRandomValues(new Uint8Array(32));
  const userId=crypto.getRandomValues(new Uint8Array(16));

  const credential=await navigator.credentials.create({
    publicKey:{
      challenge,
      rp:{ name:"BIOCHAIN Wallet", id:window.location.hostname },
      user:{ id:userId, name:"biochain_user", displayName:"BioChain User" },
      pubKeyCredParams:[
        {alg:-7,  type:"public-key"},  // ES256
        {alg:-257,type:"public-key"},  // RS256
      ],
      authenticatorSelection:{
        authenticatorAttachment:"platform",
        userVerification:"required",
        residentKey:"preferred",
      },
      timeout:60000,
    }
  });

  if(!credential) throw new Error("Biometric registration failed");

  // Encrypt seed with a random key, store key encrypted under WebAuthn
  // Simplified: we store credential ID and use password hash as fallback
  const credId=base64url(credential.rawId);
  const bioData={ credId, userId:base64url(userId.buffer), registered:Date.now() };
  localStorage.setItem(BIO_KEY, JSON.stringify(bioData));
  return credId;
}

async function authenticateBiometric(){
  const bioData=JSON.parse(localStorage.getItem(BIO_KEY)||"null");
  if(!bioData) throw new Error("No biometric registered");

  const challenge=crypto.getRandomValues(new Uint8Array(32));

  const assertion=await navigator.credentials.get({
    publicKey:{
      challenge,
      rpId:window.location.hostname,
      allowCredentials:[{
        id:fromBase64url(bioData.credId),
        type:"public-key",
        transports:["internal"],
      }],
      userVerification:"required",
      timeout:60000,
    }
  });

  if(!assertion) throw new Error("Biometric authentication failed");
  return true;
}

function hasBiometric(){ return !!localStorage.getItem(BIO_KEY); }

// ── STORAGE (multi-wallet) ──────────────────────────────────────────────
// Wallets are keyed by address in one map, so several can live on the
// same device side by side. ACTIVE_KEY remembers which one to show on
// next launch. The old single-wallet key (STORE_KEY) is migrated in
// automatically, once, the first time this runs after the update --
// nobody's existing wallet disappears because of this change.
const WALLETS_KEY = "biochain_wallets_v1";
const ACTIVE_KEY  = "biochain_active_wallet_v1";

function loadAllWallets(){
  const d = localStorage.getItem(WALLETS_KEY);
  return d ? JSON.parse(d) : {};
}
function saveAllWallets(map){ localStorage.setItem(WALLETS_KEY, JSON.stringify(map)); }

// ── RECENT RECIPIENTS ─────────────────────────────────────────────────────
// Per-wallet list of addresses this wallet has successfully sent to, newest
// first, deduplicated, capped. Stored locally only -- this is a convenience
// address book, not chain data, so it deliberately lives in localStorage
// next to the wallet itself. Keyed by sender address so switching wallets
// doesn't leak one wallet's contacts into another's send screen.
const RECENTS_MAX = 8;
function loadRecents(addr){
  try{ return JSON.parse(localStorage.getItem(`biochain_recents_${addr}`)||"[]"); }
  catch{ return []; }
}
function saveRecent(addr, recipient){
  if(!recipient || recipient===addr) return; // self-sends aren't a contact
  const cur = loadRecents(addr).filter(a=>a!==recipient);
  cur.unshift(recipient);
  localStorage.setItem(`biochain_recents_${addr}`, JSON.stringify(cur.slice(0,RECENTS_MAX)));
}
function getActiveAddress(){ return localStorage.getItem(ACTIVE_KEY); }
function setActiveAddress(addr){
  if(addr) localStorage.setItem(ACTIVE_KEY, addr);
  else localStorage.removeItem(ACTIVE_KEY);
}
function migrateLegacyWallet(){
  const old = localStorage.getItem(STORE_KEY);
  if(!old) return;
  try{
    const data = JSON.parse(old);
    const wallets = loadAllWallets();
    if(data.address && !wallets[data.address]){
      wallets[data.address] = data;
      saveAllWallets(wallets);
      if(!getActiveAddress()) setActiveAddress(data.address);
    }
  }catch{}
  localStorage.removeItem(STORE_KEY);   // migrated -- old key no longer used
}
function saveWallet(data){
  migrateLegacyWallet();
  const wallets = loadAllWallets();
  wallets[data.address] = data;
  saveAllWallets(wallets);
  setActiveAddress(data.address);
}
function loadWallet(){
  migrateLegacyWallet();
  const addr = getActiveAddress();
  if(!addr) return null;
  const wallets = loadAllWallets();
  return wallets[addr] || null;
}
function listWallets(){
  migrateLegacyWallet();
  return Object.values(loadAllWallets());
}
// Removes only the currently active wallet from the list -- other saved
// wallets on this device are untouched. Switches to another saved
// wallet automatically if one remains, otherwise back to a clean slate.
function clearWallet(){
  const addr = getActiveAddress();
  const wallets = loadAllWallets();
  if(addr) delete wallets[addr];
  saveAllWallets(wallets);
  localStorage.removeItem(BIO_KEY);
  const remaining = Object.keys(wallets);
  setActiveAddress(remaining.length ? remaining[0] : null);
}
function switchWallet(addr){ setActiveAddress(addr); }
function walletExists(){
  migrateLegacyWallet();
  return Object.keys(loadAllWallets()).length > 0;
}

// ── LOGO ──────────────────────────────────────────────────────────────────
function BiochainLogo({size=220}){
  return(
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" width={size} height={size}>
      <defs>
        <radialGradient id="bgG" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#0A1F0A"/><stop offset="60%" stopColor="#050F05"/>
          <stop offset="100%" stopColor="#020802"/>
        </radialGradient>
        <linearGradient id="ringG" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#00C9B1"/><stop offset="50%" stopColor="#2ECC71"/>
          <stop offset="100%" stopColor="#00C9B1"/>
        </linearGradient>
        <linearGradient id="hL" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#00C9B1" stopOpacity="0.9"/>
          <stop offset="50%" stopColor="#2ECC71"/>
          <stop offset="100%" stopColor="#00C9B1" stopOpacity="0.9"/>
        </linearGradient>
        <linearGradient id="hR" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#2ECC71" stopOpacity="0.9"/>
          <stop offset="50%" stopColor="#00C9B1"/>
          <stop offset="100%" stopColor="#2ECC71" stopOpacity="0.9"/>
        </linearGradient>
        <filter id="glow" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="3" result="blur"/>
          <feMerge><feMergeNode in="blur"/><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        <filter id="sg" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="6" result="blur"/>
          <feMerge><feMergeNode in="blur"/><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        <filter id="sf">
          <feGaussianBlur stdDeviation="2" result="blur"/>
          <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>
      <circle cx="200" cy="200" r="200" fill="url(#bgG)"/>
      <circle cx="200" cy="200" r="190" fill="none" stroke="#00C9B1" strokeWidth="1" strokeOpacity="0.15"/>
      <circle cx="200" cy="200" r="172" fill="none" stroke="url(#ringG)" strokeWidth="3" filter="url(#glow)"/>
      <g stroke="#00C9B1" strokeOpacity="0.4" strokeWidth="1">
        <line x1="200" y1="26" x2="200" y2="34"/><line x1="200" y1="366" x2="200" y2="374"/>
        <line x1="26" y1="200" x2="34" y2="200"/><line x1="366" y1="200" x2="374" y2="200"/>
      </g>
      <path d="M 170 75 C 155 95,145 115,155 135 C 165 155,175 175,165 195 C 155 215,145 235,155 255 C 165 275,175 295,165 315 C 155 335,145 350,170 325"
        fill="none" stroke="url(#hL)" strokeWidth="4.5" strokeLinecap="round" filter="url(#glow)"/>
      <path d="M 230 75 C 245 95,255 115,245 135 C 235 155,225 175,235 195 C 245 215,255 235,245 255 C 235 275,225 295,235 315 C 245 335,255 350,230 325"
        fill="none" stroke="url(#hR)" strokeWidth="4.5" strokeLinecap="round" filter="url(#glow)"/>
      {[[163,100,237,100,"#00C9B1","#2ECC71"],[155,130,245,130,"#2ECC71","#00C9B1"],
        [163,160,237,160,"#00C9B1","#2ECC71"],[158,195,242,195,"#2ECC71","#00C9B1"],
        [163,230,237,230,"#00C9B1","#2ECC71"],[155,265,245,265,"#2ECC71","#00C9B1"],
        [163,296,237,296,"#00C9B1","#2ECC71"]].map(([x1,y1,x2,y2,c1,c2],i)=>(
        <g key={i}>
          <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={c1} strokeWidth={i===3?3:2} strokeOpacity={i===3?0.9:0.7}/>
          <circle cx={x1} cy={y1} r={i===3?7:i>=2&&i<=4?6:5} fill={c1} filter={i===3?"url(#sg)":i>=2&&i<=4?"url(#glow)":"url(#sf)"}/>
          <circle cx={x2} cy={y2} r={i===3?7:i>=2&&i<=4?6:5} fill={c2} filter={i===3?"url(#sg)":i>=2&&i<=4?"url(#glow)":"url(#sf)"}/>
        </g>
      ))}
      <circle cx="200" cy="197" r="22" fill="#0A1F0A"/>
      <circle cx="200" cy="197" r="20" fill="none" stroke="#2ECC71" strokeWidth="1" strokeOpacity="0.4"/>
      <polygon points="200,178 216,197 200,216 184,197" fill="none" stroke="url(#ringG)" strokeWidth="2.5" filter="url(#sg)"/>
      <polygon points="200,183 211,197 200,211 189,197" fill="#2ECC71" fillOpacity="0.15"/>
      <circle cx="200" cy="197" r="3.5" fill="#00C9B1" filter="url(#sg)"/>
      <text x="200" y="356" textAnchor="middle" fontFamily="'Courier New',monospace"
        fontSize="18" fontWeight="bold" fill="url(#ringG)" letterSpacing="6" filter="url(#glow)">BIOCHAIN</text>
      <text x="200" y="373" textAnchor="middle" fontFamily="'Courier New',monospace"
        fontSize="7.5" fill="#2ECC71" fillOpacity="0.6" letterSpacing="3">POST-QUANTUM WALLET</text>
      <text x="200" y="52" textAnchor="middle" fontFamily="'Courier New',monospace"
        fontSize="7" fill="#00C9B1" fillOpacity="0.5" letterSpacing="3">ML-DSA-44 · NIST FIPS 204</text>
    </svg>
  );
}

// ── FINGERPRINT ICON ──────────────────────────────────────────────────────
function FingerprintIcon({size=64,color="#00C9B1",scanning=false}){
  return(
    <svg width={size} height={size} viewBox="0 0 64 64" style={{
      filter:`drop-shadow(0 0 ${scanning?"12px":"6px"} ${color})`,
      animation:scanning?"fpScan 1.5s ease-in-out infinite":undefined,
    }}>
      <circle cx="32" cy="32" r="30" fill="none" stroke={color} strokeWidth="1.5" strokeOpacity="0.2"/>
      <path d="M 20 42 C 18 36, 18 28, 24 23 C 28 19, 36 19, 40 23 C 44 27, 44 35, 42 42"
        fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeOpacity="0.5"/>
      <path d="M 24 42 C 23 37, 23 30, 27 26 C 30 23, 34 23, 37 26 C 40 29, 40 36, 39 42"
        fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeOpacity="0.7"/>
      <path d="M 28 42 C 27 38, 27 32, 30 29 C 32 27, 34 28, 35 31 C 36 34, 36 38, 36 42"
        fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeOpacity="0.9"/>
      <path d="M 32 42 C 32 38, 32 32, 32 28"
        fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round"/>
      <circle cx="32" cy="26" r="2" fill={color}/>
    </svg>
  );
}

// ── QR CODE ───────────────────────────────────────────────────────────────
function QRCode({value,size=130,color="#00C9B1"}){
  const N=13; let s=0;
  for(const ch of value) s=((s<<5)-s+ch.charCodeAt(0))&0xffffff;
  const cell=size/N; const cells=[];
  for(let i=0;i<N;i++) for(let j=0;j<N;j++){
    s=(s*1664525+1013904223)&0xffffffff;
    const c=(i<3&&j<3)||(i<3&&j>N-4)||(i>N-4&&j<3);
    if((s&1)&&!c) cells.push([j,i]);
  }
  return(
    <svg width={size} height={size}>
      <rect width={size} height={size} fill="#030810" rx={4}/>
      {cells.map(([x,y],i)=><rect key={i} x={x*cell+1} y={y*cell+1} width={cell-1} height={cell-1} fill={color} opacity={0.85}/>)}
      {[[0,0],[0,N-3],[N-3,0]].map(([r,c],i)=>(
        <g key={i}>
          <rect x={c*cell} y={r*cell} width={3*cell} height={3*cell} fill="none" stroke={color} strokeWidth={1.5}/>
          <rect x={c*cell+cell*0.9} y={r*cell+cell*0.9} width={1.2*cell} height={1.2*cell} fill={color}/>
        </g>
      ))}
    </svg>
  );
}

function StrengthBar({bits}){
  const pct=((bits-128)/(256-128))*100;
  const color=bits>=256?"#2ECC71":bits>=192?"#F39C12":"#E74C3C";
  return(
    <div style={{marginTop:5}}>
      <div style={{display:"flex",justifyContent:"space-between",fontSize:10,color:"#4A5568",marginBottom:2}}>
        <span>SECURITY</span><span style={{color}}>{bits}-bit</span>
      </div>
      <div style={{height:3,background:"#0D2040",borderRadius:2}}>
        <div style={{height:"100%",width:`${pct}%`,background:color,borderRadius:2,transition:"width .4s"}}/>
      </div>
    </div>
  );
}

// ── THEMES ────────────────────────────────────────────────────────────────
// "light" ключ = основной цвет текста (на тёмной теме — светлый текст,
// на светлой — тёмный текст). Остальные ключи — акценты бренда,
// затемнены в светлой версии для читаемости на белом фоне.
const THEME_DARK = {
  bg:"#030810", panel:"#070F1C", border:"#0D2040",
  cyan:"#00C9B1", green:"#2ECC71", gold:"#F39C12",
  red:"#E74C3C", purple:"#9B59B6", grey:"#4A5568", light:"#B0C4D8",
};
const THEME_LIGHT = {
  bg:"#F0F4F8", panel:"#FFFFFF", border:"#D2DCE5",
  cyan:"#00897B", green:"#1F8A4C", gold:"#B8740A",
  red:"#C0392B", purple:"#7D3C98", grey:"#6B7785", light:"#16202C",
};
const THEMES = { dark: THEME_DARK, light: THEME_LIGHT };

// C — мутируемый объект палитры. Его свойства читаются заново при каждом
// рендере (стрелочные функции sx.btn(...) и инлайн-стили), поэтому простое
// переприсваивание полей через Object.assign(C, THEMES[theme]) в начале
// рендера компонента мгновенно перекрашивает весь интерфейс без рефакторинга
// сотен мест, где используется C.xxx.
const C = { ...THEME_DARK };
const font="'Courier New',monospace";
// Все ключи sx — ФУНКЦИИ, а не статичные объекты. Это критично: объект
// {background:C.bg,...} вычисляется один раз в момент создания (то есть
// читает C.bg КАК ОНО БЫЛО на момент загрузки модуля) и больше никогда не
// обновится, даже если C.bg потом изменить через Object.assign при
// переключении темы. Функция же вызывается заново при каждом рендере и
// каждый раз читает текущее значение C.bg — поэтому тема применяется
// одинаково везде, а не выборочно.
const sx={
  root:  ()=>({background:C.bg,minHeight:"100vh",width:"100%",color:C.light,fontFamily:font,
           display:"flex",flexDirection:"column",maxWidth:500,margin:"0 auto",paddingBottom:40}),
  hdr:   ()=>({background:C.panel,borderBottom:`1px solid ${C.border}`,
           padding:"13px 16px",display:"flex",justifyContent:"space-between",alignItems:"center"}),
  card:  ()=>({background:C.panel,border:`1px solid ${C.border}`,borderRadius:6,padding:16,margin:"10px 16px"}),
  label: ()=>({fontSize:11,color:C.grey,letterSpacing:2,marginBottom:4}),
  inp:   ()=>({width:"100%",background:C.bg,border:`1px solid ${C.border}`,borderRadius:4,
           padding:"9px 10px",color:C.light,fontFamily:font,fontSize:14,outline:"none",boxSizing:"border-box"}),
  btn:   (col=C.cyan,out=false)=>({
    background:out?"transparent":`${col}18`,border:`1px solid ${col}`,color:col,
    borderRadius:4,padding:"10px 0",cursor:"pointer",fontFamily:font,
    fontSize:13,letterSpacing:2,width:"100%",marginTop:8,
  }),
  tab:   (a)=>({flex:1,padding:"10px 0",textAlign:"center",cursor:"pointer",fontSize:12,
    letterSpacing:2,border:"none",outline:"none",background:"transparent",fontFamily:font,
    color:a?C.cyan:C.grey,borderBottom:`2px solid ${a?C.cyan:"transparent"}`}),
  alert: (t)=>({background:t==="ok"?"#071A0F":t==="err"?"#1A0808":"#0A0F1A",
    border:`1px solid ${t==="ok"?C.green:t==="err"?C.red:C.gold}`,
    borderRadius:4,padding:"8px 12px",margin:"6px 16px",fontSize:12,
    color:t==="ok"?C.green:t==="err"?C.red:C.gold}),
};

function useCopy(){
  const [copied,setCopied]=useState("");
  const copy=(text,key)=>{ navigator.clipboard?.writeText(text); setCopied(key); setTimeout(()=>setCopied(""),2000); };
  return{copied,copy};
}

function WordCountSelector({value,onChange}){
  return(
    <div style={{marginBottom:14}}>
      <div style={sx.label()}>SEED PHRASE LENGTH</div>
      <div style={{display:"flex",gap:5,marginBottom:6}}>
        {SEED_OPTIONS.map(opt=>(
          <button key={opt.words} onClick={()=>onChange(opt.words)} style={{
            flex:1,padding:"7px 2px",borderRadius:3,cursor:"pointer",fontFamily:font,fontSize:12,
            border:`1px solid ${value===opt.words?C.cyan:C.border}`,
            background:value===opt.words?`${C.cyan}18`:C.bg,
            color:value===opt.words?C.cyan:C.grey,position:"relative",
          }}>
            {opt.words}
            {opt.star&&<span style={{position:"absolute",top:-5,right:-2,fontSize:9,
              background:C.green,color:"#000",borderRadius:2,padding:"1px 3px"}}>★</span>}
          </button>
        ))}
      </div>
      <StrengthBar bits={SEED_OPTIONS.find(o=>o.words===value)?.bits??128}/>
      <div style={{fontSize:11,color:C.grey,marginTop:4}}>
        {SEED_OPTIONS.find(o=>o.words===value)?.desc}
      </div>
    </div>
  );
}

function WordGrid({words,reveal=true}){
  const cols=words.length<=18?3:4;
  const colors=[C.cyan,C.green,C.gold,C.purple];
  return(
    <div style={{display:"grid",gridTemplateColumns:`repeat(${cols},1fr)`,gap:5,margin:"10px 0"}}>
      {words.map((w,i)=>(
        <div key={i} style={{background:C.bg,border:`1px solid ${C.border}`,borderRadius:3,
          padding:"5px 7px",fontSize:12,color:reveal?colors[i%colors.length]:C.grey,
          display:"flex",alignItems:"center",gap:5,
          filter:reveal?"none":"blur(4px)",userSelect:reveal?"auto":"none"}}>
          <span style={{color:C.grey,fontSize:11,minWidth:18}}>{i+1}.</span>
          <span>{reveal?w:"●●●"}</span>
        </div>
      ))}
    </div>
  );
}

const Dot=({ok})=>(
  <span style={{width:7,height:7,borderRadius:"50%",background:ok?C.green:C.red,
    display:"inline-block",marginRight:5}}/>
);

// ── MAIN ──────────────────────────────────────────────────────────────────
export default function BiochainWallet(){
  const [screen,       setScreen]      = useState("splash");
  const [tab,          setTab]         = useState("send");
  const [swapView,     setSwapView]    = useState("board");   // board / deals / history
  const [dashboard,    setDashboard]   = useState(null);
  const [dashboardError, setDashboardError] = useState(null);
  const [offers,       setOffers]      = useState([]);
  const [myLocks,      setMyLocks]     = useState([]);
  // Active-deal secrets: lock_id -> preimage (hex). MEMORY ONLY -- never
  // persisted, never in the seed backup. Lost on reload of an in-flight
  // deal by design; the user is warned in the deal wizard.
  const swapSecrets = useRef({});
  const [offGive,   setOffGive]   = useState("");
  const [offWant,   setOffWant]   = useState("");
  const [offAsset,  setOffAsset]  = useState("");   // what the person wants, typed freely -- no default
  const [offAddr,   setOffAddr]   = useState("");
  const [wallet,       setWallet]      = useState(null);
  const [seedWords,    setSeedWords]   = useState([]);
  const [seedCount,    setSeedCount]   = useState(24);
  const [seedVisible,  setSeedVisible] = useState(false);
  const [restoreW,     setRestoreW]    = useState(Array(12).fill(""));
  const [restoreCount, setRestoreCount]= useState(24); // matches the CREATE
  // screen's own default (seedCount below) -- these two were out of sync
  // (12 here vs 24 there), so a wallet created with the default word count
  // and restored without manually touching this selector would silently
  // decrypt against a completely different, shorter phrase and fail with
  // a generic "wrong words" error that gave no hint the count itself was
  // the actual problem.
  const [restoreKeystoreText, setRestoreKeystoreText] = useState("");
  const [restoreKeystoreName, setRestoreKeystoreName] = useState("");
  const [network,      setNetwork]     = useState(null);
  const [balance,      setBalance]     = useState(0);
  const [isNode,       setIsNode]      = useState(false);
  const [nodeInfo,     setNodeInfo]    = useState(null);
  const [txAddr,       setTxAddr]      = useState("");
  const [txAmt,        setTxAmt]       = useState("");
  const [msg,          setMsg]         = useState(null);
  const [history,      setHistory]     = useState([]);
  const [online,       setOnline]      = useState(false);
  const [confirmed,    setConfirmed]   = useState(false);
  const [keystoreDownloaded, setKeystoreDownloaded] = useState(false);
  const [password,     setPassword]    = useState("");
  const [loginPwd,     setLoginPwd]    = useState("");
  const [loading,      setLoading]     = useState(false);
  const [loginError,   setLoginError]  = useState("");
  const [bioAvail,     setBioAvail]    = useState(false);
  const [bioScanning,  setBioScanning] = useState(false);
  const [bioRegistered,setBioRegistered]=useState(false);
  const [loginMode,    setLoginMode]   = useState("bio"); // "bio" | "password"
  const [installEvt,   setInstallEvt]  = useState(null);
  const [settings,      setSettings]   = useState(loadSettings);
  const [pqTestResult,  setPqTestResult] = useState(null);
  const [securityWords, setSecurityWords] = useState(Array(24).fill(""));
  const [securityWordCount] = useState(24);
  const [curPwd, setCurPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [myStake, setMyStake] = useState(null);
  const [stakeAmt, setStakeAmt] = useState("");
  const [unstakeAmt, setUnstakeAmt] = useState("");
  const [pendingUnstakes, setPendingUnstakes] = useState([]);
  const [proposalsList, setProposalsList] = useState([]);
  const [propTitle, setPropTitle] = useState("");
  const [propParamKey, setPropParamKey] = useState("emerge_threshold");
  const [slashAddress, setSlashAddress] = useState("");
  const [slashAmount, setSlashAmount] = useState("");
  const [slashReason, setSlashReason] = useState("");
  const [listingAddress, setListingAddress] = useState("");
  const [listingExchange, setListingExchange] = useState("");
  const [listingPair, setListingPair] = useState("");
  const [propParamValue, setPropParamValue] = useState("");
  const [propDuration, setPropDuration] = useState(7);
  const [prevScreen,    setPrevScreen] = useState("start");
  const prevBalanceRef = useRef(null);
  const seenBlocksRef  = useRef(new Set());

  // Применяем тему синхронно в начале рендера — мутируем общий объект C,
  // т.к. весь интерфейс читает C.bg / C.cyan и т.д. напрямую. Простое
  // присвоение полей мгновенно перекрашивает всё приложение без
  // переписывания сотен строк стилей.
  Object.assign(C, THEMES[settings.theme] || THEME_DARK);
  // Keep the PAGE behind the app in sync with the theme: browsers give
  // body a white background and 8px margin by default, which leaks out
  // as white bars whenever the viewport re-lays out (Settings screen
  // switch, keyboard, rotation). Painting body/html here fixes it for
  // BOTH themes without touching index.css.
  useEffect(()=>{
    document.documentElement.style.background = C.bg;
    document.body.style.background = C.bg;
    document.body.style.margin = "0";
    document.body.style.minHeight = "100vh";
    document.documentElement.style.overscrollBehaviorY = "none";
    document.body.style.overscrollBehaviorY = "none";
    document.body.style.touchAction = "pan-x pan-y";
  },[settings.theme]);
  const {copied,copy} = useCopy();
  const pollRef = useRef(null);
  const seedCacheRef = useRef(""); // store seed for biometric unlock
  const keyRef = useRef(null); // {publicKey: Uint8Array, secretKey: Uint8Array} -- live in memory only
  const keystoreTextRef = useRef(""); // pre-computed keystore JSON, see the
  // useEffect below -- SHARE needs this ready *before* the tap, not derived
  // during it (PBKDF2 at 200k iterations is slow enough on a phone to burn
  // through Android's short post-tap "user activation" window, which is
  // what navigator.share() requires -- awaiting the derivation first was
  // causing a genuine "Permission denied" on real devices, not a flake).
  // (shareFailCountRef removed -- text-based sharing, see shareText, is
  // handled by virtually every Android share-sheet target, so the
  // "retry once then give up" logic that file-sharing needed no longer
  // applies here.)

  // ── INIT ────────────────────────────────────────────────────────────
  useEffect(()=>{
    setupPWA();
    const onInstallPrompt=(e)=>{ e.preventDefault(); setInstallEvt(e); };
    window.addEventListener("beforeinstallprompt", onInstallPrompt);
    (async()=>{
      const avail=await checkBiometricAvailable();
      setBioAvail(avail);
      const stored0=loadWallet();
      setBioRegistered(hasBiometric() && !!stored0?.bioEncrypted);
      setTimeout(()=>{
        setScreen(walletExists()?"login":"start");
        if(!hasBiometric() || !stored0?.bioEncrypted) setLoginMode("password");
      },2500);
    })();
    return ()=> window.removeEventListener("beforeinstallprompt", onInstallPrompt);
  },[]);

  // ── NETWORK ─────────────────────────────────────────────────────────
  const fetchState=useCallback(async()=>{
    let anySuccess = false;
    try{
      const [bio,ch]=await Promise.all([
        fetch(`${API}/biofield`,{signal:AbortSignal.timeout(5000)}).then(r=>r.json()),
        fetch(`${API}/chain`,  {signal:AbortSignal.timeout(5000)}).then(r=>r.json()),
      ]);
      setNetwork(bio); anySuccess = true;
      if(wallet){
        setHistory(ch.filter(b=>b.tx?.from===wallet.address||b.tx?.to===wallet.address).reverse().slice(0,15));

        // Уведомление: я кого-то валидировал и получил награду
        if(settings.notifValidated){
          for(const b of ch){
            if(b.validator===wallet.address && !seenBlocksRef.current.has(b.index)){
              seenBlocksRef.current.add(b.index);
              if(b.reward>0) notify("⛓ Block validated", `+${b.reward.toFixed(2)} BIO reward — block #${b.index}`);
            }
          }
        }
      }
    }catch{}

    // Баланс -- отдельный запрос, не должен молча "потерять" online-статус,
    // если этот конкретный запрос успешен, даже когда biofield/chain выше
    // не дозвонились (именно так баланс мог обновиться при виде OFFLINE)
    if(wallet){
      try{
        const d = await fetch(`${API}/balance`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({address:wallet.address}),signal:AbortSignal.timeout(5000)}).then(r=>r.json());
        anySuccess = true;
        const newBal = d.balance??0;
        // Уведомление: входящий перевод (баланс выросло не из-за валидации блока)
        if(settings.notifIncoming && prevBalanceRef.current!==null && newBal>prevBalanceRef.current+0.0001){
          notify("💰 BIO received", `Balance: ${newBal.toFixed(2)} BIO`);
        }
        prevBalanceRef.current=newBal;
        setBalance(newBal);setIsNode(d.is_node??false);setNodeInfo(d.node??null);
      }catch{}
    }

    setOnline(anySuccess);
  },[wallet,settings.notifValidated,settings.notifIncoming,settings.notifEnabled]);

  const fetchMyStake=useCallback(async()=>{
    if(!wallet) return;
    try{
      const d=await fetch(`${API}/stake`,{signal:AbortSignal.timeout(5000)}).then(r=>r.json());
      const mine=(d.stakes??[]).find(s=>s.address===wallet.address);
      setMyStake(mine?{tier:mine.tier,bio_staked:mine.bio_staked}:{tier:"NONE",bio_staked:0});
    }catch{}
  },[wallet]);

  const fetchPendingUnstakes=useCallback(async()=>{
    if(!wallet) return;
    try{
      const d=await fetch(`${API}/unstake?address=${wallet.address}`,{signal:AbortSignal.timeout(5000)}).then(r=>r.json());
      setPendingUnstakes(d.pending??[]);
    }catch{}
  },[wallet]);

  const fetchProposals=useCallback(async()=>{
    try{
      const d=await fetch(`${API}/proposals`,{signal:AbortSignal.timeout(5000)}).then(r=>r.json());
      setProposalsList(d??[]);
    }catch{}
  },[]);

  async function createProposal(){
    if(!keyRef.current){setMsg({type:"err",text:"Log in again"});return;}
    let actualValue = propParamValue;
    if(propParamKey==="slash"){
      if(!slashAddress||!slashAmount){setMsg({type:"err",text:"Fill address and amount to slash"});return;}
      actualValue = JSON.stringify({address:slashAddress,amount:parseFloat(slashAmount),reason:slashReason});
    } else if(propParamKey==="listing_reward"){
      if(!listingAddress||!listingExchange){setMsg({type:"err",text:"Fill address and exchange name"});return;}
      actualValue = JSON.stringify({address:listingAddress,exchange_name:listingExchange,pair_identifier:listingPair});
    } else if(!propTitle||!propParamValue){setMsg({type:"err",text:"Fill title and value"});return;}
    if(!propTitle){setMsg({type:"err",text:"Fill a title"});return;}
    try{
      const ts=Date.now()/1000;
      const nonce=await getNextNonce(wallet.address);
      const msgStr=`PROPOSAL|${wallet.address}|${propTitle}|${propParamKey}|${actualValue}|${ts.toFixed(6)}|${nonce}`;
      const sigHex=await signMessage(keyRef.current.secretKey,msgStr);
      const r=await fetch(`${API}/proposals`,{
        method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({title:propTitle,proposer:wallet.address,duration_days:propDuration,
          param_key:propParamKey,param_value:actualValue,
          pubkey:bytesToHex(keyRef.current.publicKey),signature:sigHex,timestamp:ts,nonce:nonce}),
      });
      const d=await r.json();
      if(d.status==="ok"){
        setMsg({type:"ok",text:`Proposal #${d.proposal_id} created`});
        setPropTitle(""); setPropParamValue("");
        setSlashAddress(""); setSlashAmount(""); setSlashReason("");
        setListingAddress(""); setListingExchange(""); setListingPair("");
        fetchProposals();
      }
      else setMsg({type:"err",text:d.error||"Failed"});
    }catch{ setMsg({type:"err",text:"Server unreachable"}); }
  }

  async function castVote(proposalId, voteValue){
    if(!keyRef.current){setMsg({type:"err",text:"Log in again"});return;}
    try{
      const ts=Date.now()/1000;
      const nonce=await getNextNonce(wallet.address);
      const msgStr=`VOTE|${proposalId}|${wallet.address}|${voteValue}|${ts.toFixed(6)}|${nonce}`;
      const sigHex=await signMessage(keyRef.current.secretKey,msgStr);
      const r=await fetch(`${API}/vote`,{
        method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({proposal_id:proposalId,voter:wallet.address,vote:voteValue,
          pubkey:bytesToHex(keyRef.current.publicKey),signature:sigHex,timestamp:ts,nonce:nonce}),
      });
      const d=await r.json();
      if(d.status==="ok"){ setMsg({type:"ok",text:`Voted ${voteValue}`}); fetchProposals(); }
      else setMsg({type:"err",text:d.error||"Vote failed"});
    }catch{ setMsg({type:"err",text:"Server unreachable"}); }
  }

  useEffect(()=>{
    if(screen==="staking"){ fetchMyStake(); fetchPendingUnstakes(); }
    if(screen==="governance") fetchProposals();
  },[screen,fetchMyStake,fetchPendingUnstakes,fetchProposals]);

  useEffect(()=>{
    if(screen!=="staking") return;
    const id=setInterval(()=>{ fetchMyStake(); fetchPendingUnstakes(); },15000); // stake/unstake state changes on the scale of days (7-day cooldown), not seconds
    return()=>clearInterval(id);
  },[screen,fetchMyStake,fetchPendingUnstakes]);

  useEffect(()=>{
    if(screen!=="governance") return;
    const id=setInterval(fetchProposals,20000); // proposals live for days; 20s is already generous
    return()=>clearInterval(id);
  },[screen,fetchProposals]);

  useEffect(()=>{
    if(screen!=="seed_show") return;
    if(!keyRef.current || !seedWords.length || !wallet) return;
    let cancelled=false;
    exportKeystore(keyRef.current, seedWords.join(" "), wallet.address)
      .then(text=>{ if(!cancelled) keystoreTextRef.current=text; })
      .catch(()=>{}); // SHARE/DOWNLOAD still fall back to computing it
                        // themselves on tap if this pre-warm failed for
                        // any reason -- this is a head start, not the
                        // only path.
    return ()=>{ cancelled=true; };
  },[screen, seedWords, wallet]);

  useEffect(()=>{
    if(screen!=="main") return;
    fetchState();
    pollRef.current=setInterval(fetchState,8000); // 8s, not 4s: fetchState fires 3 requests per tick (chain+biofield+balance) -- at 4s that was ~45 req/min hitting a phone-hosted server from its own wallet; the visibilitychange handler below already guarantees an instant refresh whenever the person actually returns to the tab, so a slower background cadence loses nothing they can see

    // Mobile browsers throttle or fully suspend setInterval timers while a
    // tab is backgrounded -- and any fetch already in flight at the moment
    // of backgrounding can be left in limbo rather than cleanly resolving
    // or rejecting. Without this, returning to the tab can show stale
    // ("offline-looking") state for anywhere up to the next 4s tick, or
    // sometimes longer if a request was actually stuck -- which is why
    // toggling airplane mode/wifi off-and-on appeared to "wake it up": that
    // forces the in-flight fetch to fail and the next one to start fresh.
    // Reacting to visibilitychange instead means returning to the tab
    // always triggers an immediate, real fetch, not a wait-and-hope.
    const onVisible=()=>{ if(document.visibilityState==="visible") fetchState(); };
    document.addEventListener("visibilitychange", onVisible);

    return()=>{
      clearInterval(pollRef.current);
      document.removeEventListener("visibilitychange", onVisible);
    };
  },[fetchState,screen]);

  // When the SWAP tab is open, keep board/locks fresh (lighter cadence
  // than the main poll -- swap state changes on the scale of minutes).
  useEffect(()=>{
    if(screen!=="main"||tab!=="swap") return;
    if(swapView==="board") fetchOffers(); else fetchMyLocks();
    const id=setInterval(()=>{ if(swapView==="board") fetchOffers(); else fetchMyLocks(); },12000);
    return ()=>clearInterval(id);
  },[tab,swapView,screen]);

  // NETWORK tab: transparency metrics, low-frequency poll -- this data
  // moves on the scale of hours (node births/deaths), not seconds.
  useEffect(()=>{
    if(screen!=="main"||tab!=="network") return;
    fetchDashboard();
    const id=setInterval(fetchDashboard, 30000);
    return ()=>clearInterval(id);
  },[tab,screen]);

  // ── UNLOCK WALLET ────────────────────────────────────────────────────
  // Unlocks using the REAL secret key (base64) decrypted from local storage.
  // Renamed from unlockWithSeed: the seed phrase is never stored locally
  // (only used once, at creation, to label the keystore backup file) --
  // what's decrypted here is the actual ML-DSA-44 secret key.
  async function unlockWithKey(skB64){
    const stored=loadWallet();
    const secretKey=base64ToBytes(skB64);
    const publicKey=base64ToBytes(stored.pubkeyB64);
    keyRef.current={publicKey,secretKey};
    setWallet({address:stored.address,wordCount:stored.wordCount??24});
    setScreen("main");
  }

  // ── BIOMETRIC LOGIN ──────────────────────────────────────────────────
  async function handleBiometricLogin(){
    setBioScanning(true); setLoginError("");
    try{
      await authenticateBiometric();
      const stored=loadWallet();
      if(!stored?.bioEncrypted) throw new Error("No biometric data");
      const bioData=JSON.parse(localStorage.getItem(BIO_KEY));
      const skB64=await decryptData(stored.bioEncrypted, bioData.credId.slice(0,32).padEnd(32,"x"));
      setBioScanning(false);
      await unlockWithKey(skB64);
      setMsg({type:"ok",text:"👆 Biometric unlock successful"});
    }catch(e){
      setBioScanning(false);
      if(e.name==="NotAllowedError"){
        setLoginError("Biometric cancelled — use password");
      }else{
        setLoginError("Biometric failed — use password");
      }
      setLoginMode("password");
    }
  }

  // ── PASSWORD LOGIN ───────────────────────────────────────────────────
  async function handlePasswordLogin(){
    if(!loginPwd){setLoginError("Enter your password"); return;}
    setLoading(true); setLoginError("");
    try{
      const stored=loadWallet();
      const skB64=await decryptData(stored.encrypted,loginPwd);
      setLoading(false);
      await unlockWithKey(skB64);
      setMsg({type:"ok",text:"Welcome back!"});
    }catch{
      setLoading(false);
      setLoginError("Wrong password — try again");
    }
  }

  // ── CREATE ──────────────────────────────────────────────────────────
  async function handleCreate(){
    setLoading(true);
    try{
      const words=generateSeed(seedCount);
      const kp=await generateRealKeypair();
      const address=await addressFromPubkey(kp.publicKey);
      keyRef.current=kp;
      setSeedWords(words); setWallet({address,wordCount:seedCount});
      setConfirmed(false); setSeedVisible(false); setLoading(false);
      setScreen("seed_show");
    }catch(e){
      setLoading(false);
      setMsg({type:"err",text:`Key generation failed: ${e.message}`});
    }
  }

  // ── SAVE ────────────────────────────────────────────────────────────
  async function handleSaveAndOpen(){
    if(password.length<8){setMsg({type:"err",text:"Password min 8 characters"}); return;}
    if(!keyRef.current){setMsg({type:"err",text:"No key generated — go back and create wallet again"}); return;}
    setLoading(true);
    const seedStr=seedWords.join(" ");
    const skB64=bytesToBase64(keyRef.current.secretKey);
    const pkB64=bytesToBase64(keyRef.current.publicKey);
    // Encrypted by the LOGIN PASSWORD -- for quick access on THIS device.
    // The 24-word phrase is a SEPARATE secret, used only for the keystore
    // backup file (Security screen), not for this local copy.
    const encrypted=await encryptData(skB64,password);
    const walletData={encrypted,pubkeyB64:pkB64,wordCount:wallet.wordCount,address:wallet.address,created:Date.now()};

    if(bioAvail){
      try{
        const credId=await registerBiometric(skB64);
        const bioEncrypted=await encryptData(skB64,credId.slice(0,32).padEnd(32,"x"));
        walletData.bioEncrypted=bioEncrypted;
        setBioRegistered(true);
        setMsg({type:"ok",text:"✅ Wallet saved — biometric registered!"});
      }catch{
        setMsg({type:"ok",text:"Wallet saved — biometric skipped"});
      }
    }

    saveWallet(walletData);
    seedCacheRef.current=seedStr;

    // v5.40/wallet v2.2.5: one-time, best-effort registration grant for
    // the first 100 wallets ever created (10 BIO each). Fires silently
    // right after the wallet is confirmed usable -- NOT at handleCreate,
    // where the user might still back out before committing to this
    // wallet. Failure is expected and harmless once the 100 slots are
    // gone (no error shown to the user for that case -- it's a bonus,
    // not something the wallet's core function depends on); genuine
    // network/signature failures are logged to console for debugging
    // but likewise don't block the user from reaching their new wallet.
    tryWalletRegistration(keyRef.current, wallet.address).catch(()=>{});

    setLoading(false); setScreen("main");
  }

  // ── RESTORE ─────────────────────────────────────────────────────────
  async function handleRestore(){
    const words=restoreW.slice(0,restoreCount).map(w=>w.trim().toLowerCase());
    if(words.some(w=>!w)){setMsg({type:"err",text:"Fill all words"}); return;}
    if(!restoreKeystoreText){setMsg({type:"err",text:"Upload your keystore file — the 24 words alone cannot recreate the key"}); return;}
    if(!password||password.length<8){setMsg({type:"err",text:"Set a password (min 8 chars)"}); return;}
    setLoading(true);
    try{
      const seedStr=words.join(" ");
      const kp=await importKeystore(restoreKeystoreText,seedStr);
      keyRef.current={publicKey:kp.publicKey,secretKey:kp.secretKey};
      const address=kp.address; // from the keystore file -- matches the address derived from this pubkey at creation

      const skB64=bytesToBase64(kp.secretKey);
      const pkB64=bytesToBase64(kp.publicKey);
      const encrypted=await encryptData(skB64,password);
      const walletData={encrypted,pubkeyB64:pkB64,wordCount:restoreCount,address,created:Date.now()};

      if(bioAvail){
        try{
          const credId=await registerBiometric(skB64);
          walletData.bioEncrypted=await encryptData(skB64,credId.slice(0,32).padEnd(32,"x"));
          setBioRegistered(true);
        }catch{}
      }

      saveWallet(walletData);
      setWallet({address,wordCount:restoreCount});
      setLoading(false); setScreen("main");
      setMsg({type:"ok",text:"Wallet restored and saved"});
    }catch(e){
      setLoading(false);
      setMsg({type:"err",text:"Restore failed — wrong words, wrong file, or they don't match"});
    }
  }

  // ── REGISTER BIOMETRIC (from main) ───────────────────────────────────
  async function handleRegisterBiometric(){
    if(!keyRef.current){setMsg({type:"err",text:"Re-login to register biometric"}); return;}
    setBioScanning(true);
    try{
      const skB64=bytesToBase64(keyRef.current.secretKey);
      const credId=await registerBiometric(skB64);
      const stored=loadWallet();
      stored.bioEncrypted=await encryptData(skB64,credId.slice(0,32).padEnd(32,"x"));
      saveWallet(stored);
      setBioRegistered(true); setBioScanning(false);
      setMsg({type:"ok",text:"👆 Biometric registered successfully!"});
    }catch(e){
      setBioScanning(false);
      // Surface the REAL error instead of a generic message -- WebAuthn
      // failures have many distinct causes (rp.id mismatch, user cancelled,
      // no platform authenticator, timeout, NotAllowedError from a
      // permissions-policy header, etc.) and a fixed string makes every
      // one of them equally undiagnosable from the outside.
      setMsg({type:"err",text:`Biometric registration failed: ${e.name||"Error"} -- ${e.message||String(e)}`});
    }
  }

  // ── SEND ────────────────────────────────────────────────────────────
  async function handleSend(){
    if(!txAddr&&!txAmt){setMsg({type:"err",text:"Fill address and amount"}); return;}
    if(!txAddr){setMsg({type:"err",text:"Enter recipient address"}); return;}
    if(!txAmt){setMsg({type:"err",text:"Enter amount — address is already filled"}); return;}
    if(!txAddr.startsWith("BIO1")){setMsg({type:"err",text:"Invalid BIO address"}); return;}
    const amt=parseFloat(txAmt);
    if(isNaN(amt)||amt<=0){setMsg({type:"err",text:"Invalid amount"}); return;}
    if(!keyRef.current){setMsg({type:"err",text:"No key in memory — please log in again"}); return;}
    setMsg({type:"info",text:"Signing & broadcasting..."}); setLoading(true);
    try{
      // Message format MUST match the backend exactly: see signed_message
      // in biochain.py -- "TX|sender|receiver|value|timestamp|nonce".
      const ts=Date.now()/1000;
      const nonce=await getNextNonce(wallet.address);
      const msgStr=`TX|${wallet.address}|${txAddr}|${q8(amt)}|${ts.toFixed(6)}|${nonce}`;
      const sigHex=await signMessage(keyRef.current.secretKey, msgStr);
      const r=await fetch(`${API}/tx`,{
        method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          sender:wallet.address, receiver:txAddr, value:amt,
          pubkey:bytesToHex(keyRef.current.publicKey),
          signature:sigHex, timestamp:ts, nonce:nonce,
        }),
        signal:AbortSignal.timeout(10000),
      });
      const d=await r.json(); setLoading(false);
      if(d.status==="ok"){
        setMsg({type:"ok",text:`✅ Block #${d.block.index} confirmed`});
        saveRecent(wallet.address, txAddr); // remember who we sent to -- powers the RECENT chips on the send screen
        setTxAddr(""); setTxAmt(""); fetchState();
      }else setMsg({type:"err",text:`❌ ${d.reason||d.error||"Failed"}`});
    }catch(e){setLoading(false);setMsg({type:"err",text:`❌ ${e.message||"Server unreachable"}`});}
  }

  function handleLogout(){ setWallet(null); setSeedWords([]); setHistory([]); setLoginPwd(""); setScreen("login"); }

  // ── SWAP (HTLC atomic swaps, server v5.37) ──────────────────────────
  async function fetchDashboard(){
    try{
      const res = await fetch(`${API}/dashboard`,{signal:AbortSignal.timeout(5000)});
      if(!res.ok){ setDashboardError(`HTTP ${res.status}`); return; }
      const text = await res.text();
      let d;
      try{ d = JSON.parse(text); }
      catch(parseErr){ setDashboardError(`JSON parse failed: ${parseErr.message} -- raw: ${text.slice(0,200)}`); return; }
      setDashboardError(null);
      setDashboard(d);
    }catch(e){ setDashboardError(`fetch failed: ${e.message}`); }
  }

  async function fetchOffers(){
    try{
      const d = await fetch(`${API}/swaps/offers`,{signal:AbortSignal.timeout(5000)}).then(r=>r.json());
      setOffers(d.offers || []);
    }catch(e){/* keep last list on transient error */}
  }
  async function fetchMyLocks(){
    if(!wallet) return;
    try{
      const d = await fetch(`${API}/swaps/locks?address=${wallet.address}`,{signal:AbortSignal.timeout(5000)}).then(r=>r.json());
      setMyLocks(d.locks || []);
    }catch(e){/* keep last list */}
  }

  // Publish an order-board offer: "give X BIO, want Y of some external asset"
  async function handleCreateOffer(){
    const give = parseFloat(offGive), want = parseFloat(offWant);
    const asset = offAsset.trim();
    if(isNaN(give)||give<=0){ setMsg({type:"err",text:"Enter BIO amount to offer"}); return; }
    if(!asset){ setMsg({type:"err",text:"Enter what you want in exchange"}); return; }
    if(isNaN(want)||want<=0){ setMsg({type:"err",text:"Enter the amount you want"}); return; }
    if(!offAddr.trim()){ setMsg({type:"err",text:"Enter your receiving address for that asset"}); return; }
    if(!keyRef.current){ setMsg({type:"err",text:"No key in memory — log in again"}); return; }
    setMsg({type:"info",text:"Publishing offer..."}); setLoading(true);
    try{
      const ts = Date.now()/1000, nonce = await getNextNonce(wallet.address);
      const wantSat = Math.round(want * EXT_UNIT);      // display units -> smallest integer unit
      const ttl = 72*3600;                              // 72h default
      // Server signs SWAP_OFFER|sender|give_str8|asset|want|extaddr|ttl|ts|nonce
      const msgStr = `SWAP_OFFER|${wallet.address}|${q8(give)}|${asset}|${wantSat}|${offAddr.trim()}|${ttl}|${ts.toFixed(6)}|${nonce}`;
      const sigHex = await signMessage(keyRef.current.secretKey, msgStr);
      const r = await fetch(`${API}/swap/offer`,{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({address:wallet.address, give_bio:give, want_asset:asset,
          want_amount:wantSat, ext_address:offAddr.trim(), ttl, pubkey:bytesToHex(keyRef.current.publicKey),
          signature:sigHex, timestamp:ts, nonce})}).then(r=>r.json());
      if(r.error){ setMsg({type:"err",text:r.error}); }
      else { setMsg({type:"ok",text:"Offer published on the board"}); setOffGive(""); setOffWant(""); setOffAsset(""); setOffAddr(""); fetchOffers(); }
    }catch(e){ setMsg({type:"err",text:"Offer failed: "+e.message}); }
    setLoading(false);
  }

  // Cancel one of my offers
  async function handleCancelOffer(offerId){
    if(!keyRef.current) return;
    setLoading(true);
    try{
      const ts = Date.now()/1000, nonce = await getNextNonce(wallet.address);
      const msgStr = `SWAP_OFFER|${wallet.address}|CANCEL|${offerId}|${ts.toFixed(6)}|${nonce}`;
      const sigHex = await signMessage(keyRef.current.secretKey, msgStr);
      const r = await fetch(`${API}/swap/offer`,{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({address:wallet.address, cancel_offer_id:offerId,
          pubkey:bytesToHex(keyRef.current.publicKey), signature:sigHex, timestamp:ts, nonce})}).then(r=>r.json());
      if(r.error) setMsg({type:"err",text:r.error}); else { setMsg({type:"ok",text:"Offer cancelled"}); fetchOffers(); }
    }catch(e){ setMsg({type:"err",text:e.message}); }
    setLoading(false);
  }

  // Initiator side: lock BIO under a fresh hash-lock for a counterparty.
  // Generates the preimage, keeps it in memory, shows the counterparty
  // the deal's hash-lock parameters for the external network's own escrow.
  async function handleStartLock(receiver, bioAmount){
    if(!receiver||!receiver.startsWith("BIO1")){ setMsg({type:"err",text:"Enter counterparty BIO address"}); return; }
    const amt = parseFloat(bioAmount);
    if(isNaN(amt)||amt<=0){ setMsg({type:"err",text:"Enter BIO amount to lock"}); return; }
    if(!keyRef.current){ setMsg({type:"err",text:"No key in memory — log in again"}); return; }
    setMsg({type:"info",text:"Locking BIO under hash-lock..."}); setLoading(true);
    try{
      const preimage = newPreimage();
      const hashLock = await sha256HexOfHex(preimage);
      const timeout = 48*3600;   // BIO side 48h; external side's timeout MUST be shorter
      const ts = Date.now()/1000, nonce = await getNextNonce(wallet.address);
      const msgStr = `SWAP_LOCK|${wallet.address}|${receiver}|${q8(amt)}|${hashLock}|${timeout}|${ts.toFixed(6)}|${nonce}`;
      const sigHex = await signMessage(keyRef.current.secretKey, msgStr);
      const r = await fetch(`${API}/swap/lock`,{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({address:wallet.address, receiver, bio_amount:amt, hash_lock:hashLock,
          timeout, pubkey:bytesToHex(keyRef.current.publicKey), signature:sigHex, timestamp:ts, nonce})}).then(r=>r.json());
      if(r.error){ setMsg({type:"err",text:r.error}); }
      else {
        swapSecrets.current[r.lock_id] = preimage;   // MEMORY ONLY
        setMsg({type:"ok",text:"BIO locked. Keep this tab open until the deal completes — the secret lives only in memory."});
        fetchMyLocks();
      }
    }catch(e){ setMsg({type:"err",text:"Lock failed: "+e.message}); }
    setLoading(false);
  }

  // Receiver side: claim a lock by revealing the preimage. In a real deal
  // the preimage is read from the counterparty's transaction on the
  // external network; the
  // wizard accepts it as input.
  async function handleClaim(lockId, preimage){
    const pre = (preimage||"").trim().toLowerCase();
    if(!/^[0-9a-f]{64}$/.test(pre)){ setMsg({type:"err",text:"Preimage must be 64 hex chars"}); return; }
    if(!keyRef.current) return;
    setMsg({type:"info",text:"Claiming..."}); setLoading(true);
    try{
      const ts = Date.now()/1000, nonce = await getNextNonce(wallet.address);
      const msgStr = `SWAP_CLAIM|${wallet.address}|${lockId}|${pre}|${ts.toFixed(6)}|${nonce}`;
      const sigHex = await signMessage(keyRef.current.secretKey, msgStr);
      const r = await fetch(`${API}/swap/claim`,{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({address:wallet.address, lock_id:lockId, preimage:pre,
          pubkey:bytesToHex(keyRef.current.publicKey), signature:sigHex, timestamp:ts, nonce})}).then(r=>r.json());
      if(r.error) setMsg({type:"err",text:r.error}); else { setMsg({type:"ok",text:"Claimed! BIO received."}); fetchMyLocks(); fetchState(); }
    }catch(e){ setMsg({type:"err",text:e.message}); }
    setLoading(false);
  }

  // Initiator side: refund own lock after its timeout has passed.
  async function handleRefund(lockId){
    if(!keyRef.current) return;
    setMsg({type:"info",text:"Refunding..."}); setLoading(true);
    try{
      const ts = Date.now()/1000, nonce = await getNextNonce(wallet.address);
      const msgStr = `SWAP_REFUND|${wallet.address}|${lockId}|${ts.toFixed(6)}|${nonce}`;
      const sigHex = await signMessage(keyRef.current.secretKey, msgStr);
      const r = await fetch(`${API}/swap/refund`,{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({address:wallet.address, lock_id:lockId,
          pubkey:bytesToHex(keyRef.current.publicKey), signature:sigHex, timestamp:ts, nonce})}).then(r=>r.json());
      if(r.error) setMsg({type:"err",text:r.error}); else { setMsg({type:"ok",text:"Refunded to your balance."}); fetchMyLocks(); }
    }catch(e){ setMsg({type:"err",text:e.message}); }
    setLoading(false);
  }

  // ── MULTI-WALLET ────────────────────────────────────────────────────
  function handleSwitchWallet(addr){
    switchWallet(addr);
    keyRef.current=null;
    const stored=loadWallet();
    setBioRegistered(hasBiometric() && !!stored?.bioEncrypted);
    setLoginMode((hasBiometric() && stored?.bioEncrypted) ? "bio" : "password");
    setWallet(null); setSeedWords([]); setHistory([]); setLoginPwd(""); setLoginError("");
    setScreen("login");
  }
  function handleAddWallet(){
    keyRef.current=null;
    setWallet(null); setSeedWords([]); setHistory([]); setLoginPwd("");
    setScreen("start");
  }
  async function handleInstall(){
    if(!installEvt) return;
    installEvt.prompt();
    const choice = await installEvt.userChoice;
    if(choice.outcome === "accepted") setInstallEvt(null);
  }
  function handleDeleteWallet(){
    if(!confirm("Delete THIS wallet from this device? (Other saved wallets, if any, are unaffected)")) return;
    clearWallet();
    setWallet(null); setSeedWords([]); setHistory([]);
    setScreen(walletExists()?"login":"start");
  }

  // ── SETTINGS ──────────────────────────────────────────────────────────
  function openSettings(){ setPrevScreen(screen); setScreen("settings"); }
  function closeSettings(){ setScreen(prevScreen); }

  function setTheme(theme){
    const next = {...settings, theme};
    setSettings(next); saveSettings(next);
  }

  async function toggleNotifications(){
    if(!settings.notifEnabled){
      if(!("Notification" in window)){
        setMsg({type:"err",text:"Notifications not supported on this device"});
        return;
      }
      const perm = await Notification.requestPermission();
      if(perm !== "granted"){
        setMsg({type:"err",text:"Notification permission denied"});
        return;
      }
    }
    const next = {...settings, notifEnabled: !settings.notifEnabled};
    setSettings(next); saveSettings(next);
  }

  function setNotifPref(key, value){
    const next = {...settings, [key]: value};
    setSettings(next); saveSettings(next);
  }

  function notify(title, body){
    if(!settings.notifEnabled) return;
    if(!("Notification" in window) || Notification.permission !== "granted") return;
    try{ new Notification(title, {body, icon:undefined}); }catch{}
  }

  const Header=({subtitle})=>(
    <div style={sx.hdr()}>
      <div>
        <div style={{fontSize:18,fontWeight:"bold",color:C.cyan,letterSpacing:4}}>◈ BIOCHAIN</div>
        {subtitle&&<div style={{fontSize:11,color:C.grey,letterSpacing:2,marginTop:2}}>{subtitle}</div>}
      </div>
      <div style={{display:"flex",alignItems:"center",gap:8}}>
        {installEvt&&(
          <button onClick={handleInstall} style={{
            background:`${C.cyan}18`,border:`1px solid ${C.cyan}`,color:C.cyan,
            borderRadius:4,padding:"4px 8px",fontFamily:font,fontSize:11,
            letterSpacing:1,cursor:"pointer",
          }}>📲 INSTALL</button>
        )}
        <button onClick={openSettings} style={{
          display: screen==="settings" ? "none" : "inline-flex",
          background:"transparent",border:`1px solid ${C.border}`,color:C.grey,
          borderRadius:4,padding:"4px 8px",fontFamily:font,fontSize:13,
          cursor:"pointer",
        }} title="Settings">⚙</button>
        <div style={{fontSize:12,color:online?C.green:C.red}}><Dot ok={online}/>{online?"ONLINE":"OFFLINE"}</div>
      </div>
    </div>
  );

  // ════════════════════════════════════════════
  // SPLASH
  // ════════════════════════════════════════════
  if(screen==="splash") return(
    <div style={{...sx.root(),justifyContent:"center",alignItems:"center"}}>
      <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:24}}>
        <div style={{animation:"pulse 1.5s ease-in-out infinite"}}>
          <BiochainLogo size={230}/>
        </div>
        <div style={{textAlign:"center"}}>
          <div style={{fontSize:12,color:C.cyan,letterSpacing:4,marginBottom:8}}>INITIALIZING</div>
          <div style={{display:"flex",gap:6,justifyContent:"center"}}>
            {[0,1,2].map(i=>(
              <div key={i} style={{width:6,height:6,borderRadius:"50%",background:C.cyan,
                animation:`bounce 1.2s ${i*0.2}s ease-in-out infinite`,opacity:0.8}}/>
            ))}
          </div>
        </div>
        <div style={{fontSize:10,color:C.grey,letterSpacing:3}}>v{VERSION}</div>
      </div>
      <style>{`
        @keyframes pulse{0%,100%{transform:scale(1);filter:drop-shadow(0 0 8px #00C9B140)}50%{transform:scale(1.03);filter:drop-shadow(0 0 20px #00C9B180)}}
        @keyframes bounce{0%,100%{transform:translateY(0);opacity:0.4}50%{transform:translateY(-6px);opacity:1}}
        @keyframes fpScan{0%,100%{opacity:0.6;transform:scale(1)}50%{opacity:1;transform:scale(1.08)}}
      `}</style>
    </div>
  );

  // ════════════════════════════════════════════
  // LOGIN
  // ════════════════════════════════════════════
  if(screen==="login") return(
    <div style={sx.root()}>
      <style>{`@keyframes fpScan{0%,100%{opacity:0.6;transform:scale(1)}50%{opacity:1;transform:scale(1.08)}}`}</style>
      <Header subtitle="WELCOME BACK"/>
      <div style={{display:"flex",flexDirection:"column",alignItems:"center",padding:"20px 16px 8px"}}>
        <BiochainLogo size={130}/>
      </div>

      {/* BIOMETRIC MODE */}
      {loginMode==="bio"&&bioRegistered&&(
        <div style={{...sx.card(),textAlign:"center"}}>
          <div style={{fontSize:13,color:C.cyan,marginBottom:16,letterSpacing:2}}>
            TOUCH TO UNLOCK
          </div>
          <div style={{display:"flex",justifyContent:"center",marginBottom:20,cursor:"pointer"}}
            onClick={handleBiometricLogin}>
            <FingerprintIcon size={80} color={C.cyan} scanning={bioScanning}/>
          </div>
          {bioScanning&&(
            <div style={{fontSize:12,color:C.cyan,marginBottom:10,letterSpacing:2}}>
              VERIFYING BIOMETRIC...
            </div>
          )}
          {loginError&&<div style={{fontSize:12,color:C.red,marginBottom:8}}>{loginError}</div>}
          <button style={sx.btn(C.cyan)} onClick={handleBiometricLogin} disabled={bioScanning}>
            {bioScanning?"SCANNING...":"👆 USE FINGERPRINT / FACE ID"}
          </button>
          <button style={{...sx.btn(C.grey,true),marginTop:5,fontSize:12}}
            onClick={()=>{setLoginMode("password"); setLoginError("");}}>
            🔑 USE PASSWORD INSTEAD
          </button>
        </div>
      )}

      {/* PASSWORD MODE */}
      {(loginMode==="password"||!bioRegistered)&&(
        <div style={sx.card()}>
          <div style={{fontSize:13,color:C.cyan,marginBottom:14,letterSpacing:2,textAlign:"center"}}>
            🔐 ENTER PASSWORD
          </div>
          <div style={sx.label()}>PASSWORD</div>
          <input type="password" style={sx.inp()} placeholder="Your wallet password"
            value={loginPwd} onChange={e=>{setLoginPwd(e.target.value);setLoginError("");}}
            onKeyDown={e=>e.key==="Enter"&&handlePasswordLogin()} autoFocus/>
          {loginError&&<div style={{fontSize:12,color:C.red,marginTop:6,textAlign:"center"}}>{loginError}</div>}
          <button style={sx.btn(C.cyan)} onClick={handlePasswordLogin} disabled={loading}>
            {loading?"UNLOCKING...":"→ UNLOCK WALLET"}
          </button>
          {bioAvail&&bioRegistered&&(
            <button style={{...sx.btn(C.cyan,true),marginTop:5,fontSize:12}}
              onClick={()=>{setLoginMode("bio");setLoginError("");}}>
              👆 USE BIOMETRIC INSTEAD
            </button>
          )}
          <div style={{display:"flex",gap:5,marginTop:4}}>
            <button style={{...sx.btn(C.gold,true),fontSize:11}} onClick={()=>setScreen("restore")}>↩ RESTORE</button>
            <button style={{...sx.btn(C.red,true),fontSize:11}} onClick={handleDeleteWallet}>🗑 DELETE</button>
          </div>
        </div>
      )}
      <div style={{textAlign:"center",padding:12,fontSize:11,color:C.grey}}>
        AES-256-GCM · WebAuthn · v{VERSION}
      </div>
    </div>
  );

  // ════════════════════════════════════════════
  // START
  // ════════════════════════════════════════════
  if(screen==="start") return(
    <div style={sx.root()}>
      <Header subtitle="POST-QUANTUM WALLET"/>
      {msg&&<div style={sx.alert(msg.type)} onClick={()=>setMsg(null)}>{msg.text}</div>}
      <div style={{display:"flex",flexDirection:"column",alignItems:"center",padding:"20px 16px 8px"}}>
        <BiochainLogo size={160}/>
        <div style={{fontSize:12,color:C.grey,marginTop:8,letterSpacing:2,textAlign:"center"}}>
          ML-DSA-44 · AES-256-GCM · WebAuthn
        </div>
      </div>
      {bioAvail&&(
        <div style={{...sx.card(),borderColor:C.green,textAlign:"center",margin:"6px 16px",padding:"8px 12px"}}>
          <div style={{fontSize:11,color:C.green,letterSpacing:2}}>
            👆 BIOMETRIC AVAILABLE — Will be set up on wallet creation
          </div>
        </div>
      )}
      <div style={sx.card()}>
        <div style={{fontSize:12,color:C.gold,marginBottom:12,letterSpacing:2}}>NEW WALLET</div>
        <WordCountSelector value={seedCount} onChange={setSeedCount}/>
        <button style={sx.btn(C.cyan)} onClick={handleCreate} disabled={loading}>
          {loading?"GENERATING...":"+ CREATE "+seedCount+"-WORD WALLET"}
        </button>
      </div>
      <div style={sx.card()}>
        <div style={{fontSize:12,color:C.grey,marginBottom:10,letterSpacing:2}}>EXISTING WALLET</div>
        <button style={sx.btn(C.gold,true)} onClick={()=>setScreen("restore")}>
          ↩ RESTORE FROM SEED PHRASE
        </button>
      </div>
    </div>
  );

  // ════════════════════════════════════════════
  // SEED SHOW
  // ════════════════════════════════════════════
  if(screen==="seed_show") return(
    <div style={sx.root()}>
      <Header subtitle="SAVE YOUR SEED PHRASE"/>
      {msg&&<div style={sx.alert(msg.type)} onClick={()=>setMsg(null)}>{msg.text}</div>}
      <div style={{...sx.card(),borderColor:C.gold}}>
        <div style={{fontSize:12,color:C.gold,marginBottom:8,letterSpacing:2}}>
          ⬇ DOWNLOAD YOUR KEYSTORE FILE
        </div>
        <div style={{fontSize:12,color:C.light,lineHeight:1.7,marginBottom:8}}>
          Your real signing key is encrypted with the {seedWords.length} words above
          and saved into this file. <b>The words alone cannot recreate your key</b> —
          you need BOTH the words and this file to restore your wallet on another device.
        </div>
        <button style={sx.btn(C.gold)} onClick={async()=>{
          try{
            const text=await exportKeystore(keyRef.current, seedWords.join(" "), wallet.address);
            downloadTextFile(`biochain-keystore-${wallet.address.slice(0,12)}.json`, text);
            setKeystoreDownloaded(true);
            setMsg({type:"ok",text:"Keystore downloaded — store it somewhere safe, separate from the words"});
          }catch(e){
            setMsg({type:"err",text:`Keystore export failed: ${e.message}`});
          }
        }}>
          {keystoreDownloaded?"✅ DOWNLOADED — TAP TO DOWNLOAD AGAIN":"⬇ DOWNLOAD KEYSTORE FILE"}
        </button>
        <button style={{...sx.btn(C.gold,true),marginTop:6}} onClick={async()=>{
          try{
            const text = keystoreTextRef.current
              || await exportKeystore(keyRef.current, seedWords.join(" "), wallet.address);
            const result=await shareText(text, "BioChain Keystore");
            if(result==="shared"){ setKeystoreDownloaded(true); setMsg({type:"ok",text:"Shared — paste it on Restore screen"}); }
            else if(result==="unsupported") setMsg({type:"err",text:"Sharing not supported here — use Download instead"});
          }catch(e){
            setMsg({type:"err",text:`Share failed: ${e.message}`});
          }
        }}>📤 SHARE TO ANOTHER DEVICE (Bluetooth / Drive / etc.)</button>
        <div style={{fontSize:11,color:C.grey,marginTop:6,lineHeight:1.4}}>
          ⚠ The key is long (post-quantum keys are large by nature) — plain
          SMS/Messages will split it into many parts. Pick WhatsApp,
          Telegram, Email, or Google Keep from the share menu instead —
          those send it as one piece.
        </div>
      </div>
      <div style={{...sx.card(),borderColor:C.gold}}>
        <div style={{fontSize:12,color:C.gold,marginBottom:8,letterSpacing:2}}>
          ⚠️ WRITE THESE {seedWords.length} WORDS ON PAPER
        </div>
        <div style={{fontSize:12,color:C.light,lineHeight:1.7,marginBottom:8}}>
          Your only backup. Never share or store digitally.
        </div>
        <div style={{display:"flex",justifyContent:"flex-end",marginBottom:5}}>
          <button style={{...sx.btn(C.grey,true),width:"auto",padding:"3px 10px",marginTop:0,fontSize:11}}
            onClick={()=>setSeedVisible(v=>!v)}>
            {seedVisible?"🙈 HIDE":"👁 SHOW WORDS"}
          </button>
        </div>
        <WordGrid words={seedWords} reveal={seedVisible}/>
        <StrengthBar bits={SEED_OPTIONS.find(o=>o.words===seedWords.length)?.bits??128}/>
        {seedVisible&&(
          <button style={{...sx.btn(C.gold),marginTop:8}} onClick={()=>copy(seedWords.join(" "),"seed")}>
            {copied==="seed"?"✅ COPIED":"📋 COPY ALL WORDS"}
          </button>
        )}
      </div>
      <div style={sx.card()}>
        <div style={sx.label()}>YOUR BIO ADDRESS</div>
        <div style={{fontSize:12,color:C.cyan,wordBreak:"break-all",marginBottom:10}}>{wallet?.address}</div>
        <div style={sx.label()}>SET PASSWORD</div>
        <input type="password" style={sx.inp()} placeholder="Min 8 characters — remember this!"
          value={password} onChange={e=>setPassword(e.target.value)}/>
        {bioAvail&&(
          <div style={{fontSize:11,color:C.green,marginTop:5}}>
            👆 Biometric will be set up automatically after saving
          </div>
        )}
        <div style={{display:"flex",alignItems:"center",gap:8,margin:"12px 0"}}>
          <input type="checkbox" id="conf" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}
            style={{width:16,height:16,cursor:"pointer"}}/>
          <label htmlFor="conf" style={{fontSize:12,color:C.light,cursor:"pointer"}}>
            I saved all {seedWords.length} words AND downloaded the keystore file
          </label>
        </div>
        <button style={sx.btn(confirmed&&keystoreDownloaded&&password.length>=8?C.green:C.grey)}
          onClick={()=>{
            if(!keystoreDownloaded){setMsg({type:"err",text:"Download your keystore file first"}); return;}
            if(!confirmed){setMsg({type:"err",text:"Confirm you saved your seed"}); return;}
            handleSaveAndOpen();
          }} disabled={loading}>
          {loading?"SAVING...":"✅ SAVE & SETUP BIOMETRIC"}
        </button>
      </div>
    </div>
  );

  // ════════════════════════════════════════════
  // RESTORE
  // ════════════════════════════════════════════
  if(screen==="restore") return(
    <div style={sx.root()}>
      <Header subtitle="RESTORE WALLET"/>
      {msg&&<div style={sx.alert(msg.type)} onClick={()=>setMsg(null)}>{msg.text}</div>}
      <div style={sx.card()}>
        <div style={{fontSize:11,color:C.gold,marginBottom:8,lineHeight:1.4}}>
          ⚠ Select the SAME word count you were shown when this wallet was
          created — 12 vs 24 words produces a completely different phrase,
          and restore will fail with no clue that the count was the problem.
        </div>
        <WordCountSelector value={restoreCount}
          onChange={c=>{setRestoreCount(c);setRestoreW(Array(c).fill(""));}}/>
        <div style={{display:"grid",
          gridTemplateColumns:restoreCount<=18?"repeat(3,1fr)":"repeat(4,1fr)",gap:5,margin:"10px 0"}}>
          {Array(restoreCount).fill(0).map((_,i)=>(
            <div key={i} style={{display:"flex",alignItems:"center",gap:3}}>
              <span style={{fontSize:11,color:C.grey,minWidth:18}}>{i+1}.</span>
              <input style={{...sx.inp(),padding:"5px 6px",fontSize:12}}
                value={restoreW[i]??""} placeholder="word" autoComplete="off"
                onChange={e=>{const nw=[...restoreW];nw[i]=e.target.value;setRestoreW(nw);}}/>
            </div>
          ))}
        </div>
        <div style={{...sx.label(),marginTop:14}}>KEYSTORE FILE</div>
        <div style={{fontSize:11,color:C.grey,marginBottom:6}}>
          The 24 words alone cannot recreate your key — you need the keystore
          file you downloaded when this wallet was created, OR the keystore
          text if it was sent to you via SHARE (paste it below instead).
        </div>
        <input type="file" accept=".json" style={{...sx.inp(),padding:8}}
          onChange={async e=>{
            const f=e.target.files?.[0];
            if(!f) return;
            setRestoreKeystoreName(f.name);
            setRestoreKeystoreText(await f.text());
          }}/>
        {restoreKeystoreName && (
          <div style={{fontSize:11,color:C.green,marginTop:4}}>✓ {restoreKeystoreName}</div>
        )}
        <div style={{fontSize:11,color:C.grey,margin:"8px 0 4px",textAlign:"center"}}>— or —</div>
        <textarea style={{...sx.inp(),fontSize:11,fontFamily:"monospace",minHeight:70,resize:"vertical"}}
          placeholder="paste keystore text here (if it was shared to you, not downloaded as a file)"
          onChange={e=>{
            const t=e.target.value.trim();
            if(!t){ return; }
            try{
              JSON.parse(t); // just a shape check -- the real validation
              // (does it actually decrypt) happens in handleRestore itself
              setRestoreKeystoreName("(pasted text)");
              setRestoreKeystoreText(t);
            }catch{
              // not valid JSON yet -- likely still mid-paste/mid-edit,
              // say nothing rather than flash an error on every keystroke
            }
          }}/>

        <div style={{...sx.label(),marginTop:10}}>NEW PASSWORD</div>
        <input type="password" style={sx.inp()} placeholder="Min 8 characters"
          value={password} onChange={e=>setPassword(e.target.value)}/>
        <button style={sx.btn(C.cyan)} onClick={handleRestore} disabled={loading}>
          {loading?"RESTORING...":"↩ RESTORE & SAVE"}
        </button>
        <button style={{...sx.btn(C.grey,true),marginTop:5}}
          onClick={()=>setScreen(walletExists()?"login":"start")}>← BACK</button>
      </div>
    </div>
  );

  // ════════════════════════════════════════════
  // SETTINGS
  // ════════════════════════════════════════════
  if(screen==="settings") return(
    <div style={sx.root()}>
      <Header subtitle="SETTINGS"/>

      {msg&&<div style={sx.alert(msg.type)} onClick={()=>setMsg(null)}>{msg.text}</div>}

      <div style={sx.card()}>
        <div style={{fontSize:12,color:C.cyan,marginBottom:8,letterSpacing:2}}>MENU</div>
        {[
          {icon:"🔐",label:"Security & Backup",screen:"security"},
          {icon:"🔀",label:"Wallets",screen:"wallets"},
          {icon:"⛓",label:"Staking",screen:"staking"},
          {icon:"🗳",label:"Governance",screen:"governance"},
          {icon:"ℹ️",label:"About",screen:"about"},
        ].map(item=>(
          <button key={item.screen} onClick={()=>setScreen(item.screen)} style={{
            width:"100%",display:"flex",alignItems:"center",justifyContent:"space-between",
            background:"transparent",border:"none",borderBottom:`1px solid ${C.border}`,
            padding:"12px 2px",cursor:"pointer",fontFamily:font,
          }}>
            <span style={{fontSize:14,color:C.light}}>{item.icon}&nbsp;&nbsp;{item.label}</span>
            <span style={{fontSize:14,color:C.grey}}>›</span>
          </button>
        ))}
      </div>

      <div style={sx.card()}>
        <div style={{fontSize:12,color:C.cyan,marginBottom:12,letterSpacing:2}}>🎨 COLOR THEME</div>
        <div style={{display:"flex",gap:8}}>
          <button onClick={()=>setTheme("dark")} style={{
            flex:1,padding:"14px 0",borderRadius:6,cursor:"pointer",fontFamily:font,
            border:`2px solid ${settings.theme==="dark"?C.cyan:C.border}`,
            background:settings.theme==="dark"?`${C.cyan}18`:C.bg,
            color:settings.theme==="dark"?C.cyan:C.grey,
          }}>
            <div style={{fontSize:20,marginBottom:4}}>🌙</div>
            <div style={{fontSize:12,letterSpacing:1}}>DARK</div>
          </button>
          <button onClick={()=>setTheme("light")} style={{
            flex:1,padding:"14px 0",borderRadius:6,cursor:"pointer",fontFamily:font,
            border:`2px solid ${settings.theme==="light"?C.cyan:C.border}`,
            background:settings.theme==="light"?`${C.cyan}18`:C.bg,
            color:settings.theme==="light"?C.cyan:C.grey,
          }}>
            <div style={{fontSize:20,marginBottom:4}}>☀️</div>
            <div style={{fontSize:12,letterSpacing:1}}>LIGHT</div>
          </button>
        </div>
      </div>

      <div style={sx.card()}>
        <div style={{fontSize:12,color:C.cyan,marginBottom:4,letterSpacing:2}}>🔔 NOTIFICATIONS</div>
        <div style={{fontSize:11,color:C.grey,marginBottom:12}}>
          Get notified about wallet activity even with the screen off.
        </div>

        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"8px 0",borderBottom:`1px solid ${C.border}`}}>
          <div>
            <div style={{fontSize:13,color:C.light}}>Enable notifications</div>
            <div style={{fontSize:10,color:C.grey,marginTop:2}}>Requires browser permission</div>
          </div>
          <button onClick={toggleNotifications} style={{
            width:46,height:24,borderRadius:12,border:`1px solid ${settings.notifEnabled?C.green:C.border}`,
            background:settings.notifEnabled?`${C.green}30`:C.bg,cursor:"pointer",position:"relative",
          }}>
            <div style={{
              width:18,height:18,borderRadius:"50%",background:settings.notifEnabled?C.green:C.grey,
              position:"absolute",top:2,left:settings.notifEnabled?24:2,transition:"left .2s",
            }}/>
          </button>
        </div>

        <div style={{opacity:settings.notifEnabled?1:0.4,pointerEvents:settings.notifEnabled?"auto":"none"}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"8px 0",borderBottom:`1px solid ${C.border}`}}>
            <div style={{fontSize:13,color:C.light}}>💰 Incoming BIO received</div>
            <input type="checkbox" checked={settings.notifIncoming}
              onChange={e=>setNotifPref("notifIncoming",e.target.checked)}
              style={{width:16,height:16,cursor:"pointer"}}/>
          </div>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"8px 0"}}>
            <div style={{fontSize:13,color:C.light}}>⛓ I validated a block</div>
            <input type="checkbox" checked={settings.notifValidated}
              onChange={e=>setNotifPref("notifValidated",e.target.checked)}
              style={{width:16,height:16,cursor:"pointer"}}/>
          </div>
        </div>
      </div>

      <div style={{margin:"10px 16px"}}>
        <button style={sx.btn(C.cyan,true)} onClick={closeSettings}>← BACK</button>
      </div>

      <div style={{textAlign:"center",padding:12,fontSize:11,color:C.grey}}>
        BioChain Wallet v{VERSION}
      </div>
    </div>
  );

  // ════════════════════════════════════════════
  // WALLETS (switch / add)
  // ════════════════════════════════════════════
  if(screen==="wallets") return(
    <div style={sx.root()}>
      <Header subtitle="WALLETS ON THIS DEVICE"/>
      {msg&&<div style={sx.alert(msg.type)} onClick={()=>setMsg(null)}>{msg.text}</div>}

      <div style={sx.card()}>
        <div style={{fontSize:12,color:C.cyan,marginBottom:10,letterSpacing:2}}>🔀 SAVED WALLETS</div>
        {listWallets().map(w=>(
          <div key={w.address} style={{
            display:"flex",justifyContent:"space-between",alignItems:"center",
            padding:"10px 0",borderBottom:`1px solid ${C.border}`,
          }}>
            <div>
              <div style={{fontSize:13,color:w.address===wallet?.address?C.green:C.light,fontFamily:"monospace"}}>
                {w.address.slice(0,12)}...{w.address.slice(-4)}
              </div>
              {w.address===wallet?.address&&(
                <div style={{fontSize:10,color:C.green,marginTop:2}}>● ACTIVE NOW</div>
              )}
            </div>
            {w.address!==wallet?.address&&(
              <button style={{...sx.btn(C.cyan,true),fontSize:11,width:"auto",padding:"6px 14px"}}
                onClick={()=>handleSwitchWallet(w.address)}>SWITCH</button>
            )}
          </div>
        ))}
      </div>

      <div style={{margin:"10px 16px"}}>
        <button style={sx.btn(C.gold)} onClick={handleAddWallet}>+ ADD ANOTHER WALLET</button>
      </div>
      <div style={{fontSize:11,color:C.grey,margin:"0 16px 10px",textAlign:"center"}}>
        Each wallet keeps its own key and password — switching asks you to
        log in to whichever one you pick, same as opening it fresh.
      </div>

      <div style={{margin:"10px 16px"}}>
        <button style={sx.btn(C.cyan,true)} onClick={closeSettings}>← BACK</button>
      </div>
    </div>
  );

  // ════════════════════════════════════════════
  // SECURITY & BACKUP
  // ════════════════════════════════════════════
  if(screen==="security") return(
    <div style={sx.root()}>
      <Header subtitle="SECURITY & BACKUP"/>
      {msg&&<div style={sx.alert(msg.type)} onClick={()=>setMsg(null)}>{msg.text}</div>}

      <div style={sx.card()}>
        <div style={{fontSize:12,color:C.cyan,marginBottom:6,letterSpacing:2}}>⬇ RE-EXPORT KEYSTORE</div>
        <div style={{fontSize:11,color:C.grey,marginBottom:10}}>
          The app never stores your 24 words — type them again to export a
          fresh keystore file for your current key.
        </div>
        <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:5,marginBottom:8}}>
          {Array(securityWordCount).fill(0).map((_,i)=>(
            <input key={i} style={{...sx.inp(),padding:"5px 6px",fontSize:12}}
              value={securityWords[i]??""} placeholder={`${i+1}`} autoComplete="off"
              onChange={e=>{const nw=[...securityWords];nw[i]=e.target.value;setSecurityWords(nw);}}/>
          ))}
        </div>
        <button style={sx.btn(C.gold)} onClick={async()=>{
          if(!keyRef.current){setMsg({type:"err",text:"Log in again first"});return;}
          const words=securityWords.slice(0,securityWordCount).map(w=>w.trim().toLowerCase());
          if(words.some(w=>!w)){setMsg({type:"err",text:"Fill all words"});return;}
          try{
            const text=await exportKeystore(keyRef.current, words.join(" "), wallet.address);
            downloadTextFile(`biochain-keystore-${wallet.address.slice(0,12)}.json`, text);
            setMsg({type:"ok",text:"Keystore downloaded"});
          }catch(e){ setMsg({type:"err",text:"Export failed — check the words"}); }
        }}>⬇ EXPORT KEYSTORE FILE</button>
        <button style={{...sx.btn(C.gold,true),marginTop:6}} onClick={async()=>{
          if(!keyRef.current){setMsg({type:"err",text:"Log in again first"});return;}
          const words=securityWords.slice(0,securityWordCount).map(w=>w.trim().toLowerCase());
          if(words.some(w=>!w)){setMsg({type:"err",text:"Fill all words"});return;}
          try{
            const text=await exportKeystore(keyRef.current, words.join(" "), wallet.address);
            const result=await shareText(text, "BioChain Keystore");
            if(result==="shared"){ setMsg({type:"ok",text:"Shared — paste it on Restore screen"}); }
            else if(result==="unsupported") setMsg({type:"err",text:"Sharing not supported here — use Export instead"});
          }catch(e){
            setMsg({type:"err",text:"Share failed — check the words"});
          }
        }}>📤 SHARE TO ANOTHER DEVICE (Bluetooth / Drive / etc.)</button>
        <div style={{fontSize:11,color:C.grey,marginTop:6,lineHeight:1.4}}>
          ⚠ The key is long (post-quantum keys are large by nature) — plain
          SMS/Messages will split it into many parts. Pick WhatsApp,
          Telegram, Email, or Google Keep from the share menu instead —
          those send it as one piece.
        </div>
      </div>

      <div style={sx.card()}>
        <div style={{fontSize:12,color:C.cyan,marginBottom:10,letterSpacing:2}}>🔑 CHANGE PASSWORD</div>
        <input type="password" style={sx.inp()} placeholder="Current password"
          value={curPwd} onChange={e=>setCurPwd(e.target.value)}/>
        <input type="password" style={{...sx.inp(),marginTop:6}} placeholder="New password (min 8 chars)"
          value={newPwd} onChange={e=>setNewPwd(e.target.value)}/>
        <button style={sx.btn(C.cyan)} onClick={async()=>{
          if(newPwd.length<8){setMsg({type:"err",text:"New password too short"});return;}
          try{
            const stored=loadWallet();
            const skB64=await decryptData(stored.encrypted,curPwd);
            stored.encrypted=await encryptData(skB64,newPwd);
            saveWallet(stored);
            setCurPwd(""); setNewPwd("");
            setMsg({type:"ok",text:"Password changed"});
          }catch{ setMsg({type:"err",text:"Current password is wrong"}); }
        }}>CHANGE PASSWORD</button>
      </div>

      <div style={sx.card()}>
        <div style={{fontSize:12,color:C.cyan,marginBottom:10,letterSpacing:2}}>👆 BIOMETRIC</div>
        <div style={{fontSize:13,color:C.light,marginBottom:8}}>
          Status: {bioRegistered?"✅ Registered":"Not registered"}
        </div>
        {!bioRegistered&&bioAvail&&(
          <button style={sx.btn(C.green)} onClick={handleRegisterBiometric} disabled={bioScanning}>
            {bioScanning?"SCANNING...":"👆 REGISTER BIOMETRIC"}
          </button>
        )}
      </div>

      <div style={{...sx.card(),borderColor:C.red}}>
        <div style={{fontSize:12,color:C.red,marginBottom:10,letterSpacing:2}}>⚠️ DANGER ZONE</div>
        <button style={sx.btn(C.red)} onClick={handleDeleteWallet}>🗑 DELETE WALLET FROM THIS DEVICE</button>
      </div>

      <div style={{margin:"10px 16px"}}>
        <button style={sx.btn(C.cyan,true)} onClick={closeSettings}>← BACK</button>
      </div>
    </div>
  );

  // ════════════════════════════════════════════
  // STAKING
  // ════════════════════════════════════════════
  if(screen==="staking") return(
    <div style={sx.root()}>
      <Header subtitle="STAKING"/>
      {msg&&<div style={sx.alert(msg.type)} onClick={()=>setMsg(null)}>{msg.text}</div>}

      <div style={sx.card()}>
        <div style={sx.label()}>YOUR CURRENT TIER</div>
        <div style={{fontSize:18,color:C.cyan,marginBottom:4}}>{myStake?.tier??"NONE"}</div>
        <div style={{fontSize:12,color:C.grey}}>Staked: {(myStake?.bio_staked??0).toFixed(2)} BIO</div>
      </div>

      <div style={sx.card()}>
        <div style={{fontSize:12,color:C.cyan,marginBottom:10,letterSpacing:2}}>⛓ STAKE TIERS</div>
        {[{t:"VALIDATOR",a:"1,000",m:"×1.0"},{t:"SENIOR VALIDATOR",a:"5,000",m:"×1.5"},{t:"ANCHOR VALIDATOR",a:"20,000",m:"×2.0"}].map(row=>(
          <div key={row.t} style={{display:"flex",justifyContent:"space-between",padding:"6px 0",borderBottom:`1px solid ${C.border}`,fontSize:12}}>
            <span style={{color:C.light}}>{row.t}</span>
            <span style={{color:C.grey}}>{row.a} BIO ({row.m})</span>
          </div>
        ))}
      </div>

      <div style={sx.card()}>
        <div style={sx.label()}>AMOUNT TO STAKE (BIO)</div>
        <input style={sx.inp()} placeholder="0.00" value={stakeAmt} onChange={e=>setStakeAmt(e.target.value)}/>
        <button style={sx.btn(C.gold)} onClick={async()=>{
          const amt=parseFloat(stakeAmt);
          if(isNaN(amt)||amt<=0){setMsg({type:"err",text:"Invalid amount"});return;}
          if(!keyRef.current){setMsg({type:"err",text:"Log in again"});return;}
          setLoading(true);
          try{
            const ts=Date.now()/1000;
            const nonce=await getNextNonce(wallet.address);
            const msgStr=`STAKE|${wallet.address}|${q8(amt)}|${ts.toFixed(6)}|${nonce}`;
            const sigHex=await signMessage(keyRef.current.secretKey,msgStr);
            const r=await fetch(`${API}/stake`,{
              method:"POST",headers:{"Content-Type":"application/json"},
              body:JSON.stringify({address:wallet.address,bio_amount:amt,
                pubkey:bytesToHex(keyRef.current.publicKey),signature:sigHex,timestamp:ts,nonce:nonce}),
            });
            const d=await r.json(); setLoading(false);
            if(d.status==="ok"){ setMsg({type:"ok",text:`Staked — tier: ${d.tier}`}); setStakeAmt(""); fetchMyStake(); }
            else setMsg({type:"err",text:d.error||"Stake failed"});
          }catch(e){ setLoading(false); setMsg({type:"err",text:"Server unreachable"}); }
        }} disabled={loading}>{loading?"STAKING...":"⛓ STAKE BIO"}</button>
      </div>

      <div style={{...sx.card(),borderColor:C.red}}>
        <div style={{fontSize:12,color:C.red,marginBottom:6,letterSpacing:2}}>↩ UNSTAKE</div>
        <div style={{fontSize:11,color:C.grey,marginBottom:10}}>
          Drops your tier immediately. The BIO becomes spendable again after
          a 7-day cooldown -- this is intentional, so misbehavior can still
          be caught and slashed before a withdrawal goes through.
        </div>
        <div style={sx.label()}>AMOUNT TO UNSTAKE (BIO)</div>
        <input style={sx.inp()} placeholder="0.00" value={unstakeAmt} onChange={e=>setUnstakeAmt(e.target.value)}/>
        <button style={sx.btn(C.red)} onClick={async()=>{
          const amt=parseFloat(unstakeAmt);
          if(isNaN(amt)||amt<=0){setMsg({type:"err",text:"Invalid amount"});return;}
          if(!keyRef.current){setMsg({type:"err",text:"Log in again"});return;}
          setLoading(true);
          try{
            const ts=Date.now()/1000;
            const nonce=await getNextNonce(wallet.address);
            const msgStr=`UNSTAKE|${wallet.address}|${q8(amt)}|${ts.toFixed(6)}|${nonce}`;
            const sigHex=await signMessage(keyRef.current.secretKey,msgStr);
            const r=await fetch(`${API}/unstake`,{
              method:"POST",headers:{"Content-Type":"application/json"},
              body:JSON.stringify({address:wallet.address,bio_amount:amt,
                pubkey:bytesToHex(keyRef.current.publicKey),signature:sigHex,timestamp:ts,nonce:nonce}),
            });
            const d=await r.json(); setLoading(false);
            if(d.status==="ok"){
              setMsg({type:"ok",text:`Unstake requested — spendable in ${d.cooldown_days} days`});
              setUnstakeAmt(""); fetchMyStake(); fetchPendingUnstakes();
            }else setMsg({type:"err",text:d.error||"Unstake failed"});
          }catch(e){ setLoading(false); setMsg({type:"err",text:"Server unreachable"}); }
        }} disabled={loading}>{loading?"REQUESTING...":"↩ REQUEST UNSTAKE"}</button>

        {pendingUnstakes.length>0&&(
          <div style={{marginTop:12}}>
            <div style={{fontSize:11,color:C.grey,marginBottom:6,letterSpacing:1}}>PENDING WITHDRAWALS</div>
            {pendingUnstakes.map((p,i)=>(
              <div key={i} style={{display:"flex",justifyContent:"space-between",padding:"6px 0",borderBottom:`1px solid ${C.border}`,fontSize:12}}>
                <span style={{color:C.light}}>{p.bio_amount.toFixed(2)} BIO</span>
                <span style={{color:p.days_left<=0?C.green:C.gold}}>
                  {p.days_left<=0?"ready next block":`${p.days_left.toFixed(1)}d left`}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div style={{margin:"10px 16px"}}>
        <button style={sx.btn(C.cyan,true)} onClick={closeSettings}>← BACK</button>
      </div>
    </div>
  );

  // ════════════════════════════════════════════
  // GOVERNANCE
  // ════════════════════════════════════════════
  if(screen==="governance") return(
    <div style={sx.root()}>
      <Header subtitle="GOVERNANCE"/>
      {msg&&<div style={sx.alert(msg.type)} onClick={()=>setMsg(null)}>{msg.text}</div>}

      <div style={sx.card()}>
        <div style={{fontSize:12,color:C.cyan,marginBottom:8,letterSpacing:2}}>+ NEW PROPOSAL</div>
        <input style={sx.inp()} placeholder="Title" value={propTitle} onChange={e=>setPropTitle(e.target.value)}/>
        <select style={{...sx.inp(),marginTop:6}} value={propParamKey} onChange={e=>setPropParamKey(e.target.value)}>
          {["emerge_threshold","burn_rate","theta_s","theta_w","theta_i",
            "rate_limit_per_min","checkpoint_every",
            "tier_validator_min","tier_senior_min","tier_anchor_min",
            "slash","listing_reward"].map(k=>(
            <option key={k} value={k}>{k}</option>
          ))}
        </select>
        {propParamKey==="slash"?(
          <>
            <div style={{fontSize:11,color:C.grey,marginTop:8}}>One-time action -- slashes a stake, doesn't change a parameter.</div>
            <div style={{...sx.label(),marginTop:6}}>ADDRESS TO SLASH</div>
            <input style={sx.inp()} placeholder="BIO1..." value={slashAddress} onChange={e=>setSlashAddress(e.target.value)}/>
            <div style={{...sx.label(),marginTop:6}}>AMOUNT (BIO)</div>
            <input style={sx.inp()} placeholder="0.00" value={slashAmount} onChange={e=>setSlashAmount(e.target.value)}/>
            <div style={{...sx.label(),marginTop:6}}>REASON</div>
            <input style={sx.inp()} placeholder="Why" value={slashReason} onChange={e=>setSlashReason(e.target.value)}/>
          </>
        ):propParamKey==="listing_reward"?(
          <>
            <div style={{fontSize:11,color:C.grey,marginTop:8}}>One-time action -- confirms a real exchange listing happened.</div>
            <div style={{...sx.label(),marginTop:6}}>ADDRESS TO REWARD</div>
            <input style={sx.inp()} placeholder="BIO1..." value={listingAddress} onChange={e=>setListingAddress(e.target.value)}/>
            <div style={{...sx.label(),marginTop:6}}>EXCHANGE NAME</div>
            <input style={sx.inp()} placeholder="e.g. SomeDEX" value={listingExchange} onChange={e=>setListingExchange(e.target.value)}/>
            <div style={{...sx.label(),marginTop:6}}>PAIR (OPTIONAL)</div>
            <input style={sx.inp()} placeholder="e.g. BIO/USDT" value={listingPair} onChange={e=>setListingPair(e.target.value)}/>
          </>
        ):(
          <input style={{...sx.inp(),marginTop:6}} placeholder="New value" value={propParamValue} onChange={e=>setPropParamValue(e.target.value)}/>
        )}
        <input style={{...sx.inp(),marginTop:6}} type="number" placeholder="Duration (days)" value={propDuration} onChange={e=>setPropDuration(parseInt(e.target.value)||7)}/>
        <button style={sx.btn(C.gold)} onClick={createProposal}>+ CREATE PROPOSAL</button>
      </div>

      {proposalsList.length===0&&(
        <div style={sx.card()}><div style={{fontSize:12,color:C.grey}}>No proposals yet.</div></div>
      )}

      {proposalsList.map(p=>(
        <div key={p.id} style={sx.card()}>
          <div style={{display:"flex",justifyContent:"space-between"}}>
            <div style={{fontSize:13,color:C.light,fontWeight:"bold"}}>#{p.id} {p.title}</div>
            <div style={{fontSize:11,color:p.status==="APPLIED"?C.green:p.status==="REJECTED"||p.status==="FAILED"?C.red:C.gold}}>{p.status}</div>
          </div>
          <div style={{fontSize:11,color:C.grey,marginTop:4}}>{p.param_key} → {p.param_value}</div>
          <div style={{fontSize:11,color:C.grey,marginTop:2}}>For: {p.pct_for}% ({p.votes_for}/{p.votes_for+p.votes_against})</div>
          {p.status==="ACTIVE"&&(
            <div style={{display:"flex",gap:6,marginTop:8}}>
              <button style={{...sx.btn(C.green),marginTop:0}} onClick={()=>castVote(p.id,"FOR")}>FOR</button>
              <button style={{...sx.btn(C.red),marginTop:0}} onClick={()=>castVote(p.id,"AGAINST")}>AGAINST</button>
            </div>
          )}
        </div>
      ))}

      <div style={{margin:"10px 16px"}}>
        <button style={sx.btn(C.cyan,true)} onClick={closeSettings}>← BACK</button>
      </div>
    </div>
  );

  // ════════════════════════════════════════════
  // ABOUT
  // ════════════════════════════════════════════
  if(screen==="about") return(
    <div style={sx.root()}>
      <Header subtitle="ABOUT"/>
      <div style={sx.card()}>
        <div style={{fontSize:16,color:C.cyan,marginBottom:10}}>BIOCHAIN Wallet</div>
        <div style={{fontSize:12,color:C.grey,lineHeight:1.8}}>
          Version: {VERSION}<br/>
          API endpoint: {API}<br/>
          ML-DSA-44 (Dilithium3) · AES-256-GCM · WebAuthn<br/>
        </div>
      </div>
      <div style={{margin:"10px 16px"}}>
        <button style={sx.btn(C.cyan,true)} onClick={closeSettings}>← BACK</button>
      </div>
    </div>
  );

  // ════════════════════════════════════════════
  // MAIN
  // ════════════════════════════════════════════
  return(
    <div style={sx.root()}>
      <style>{`@keyframes fpScan{0%,100%{opacity:0.6;transform:scale(1)}50%{opacity:1;transform:scale(1.08)}}`}</style>
      <Header subtitle={`${wallet?.wordCount}-WORD WALLET`}/>
      {msg&&<div style={sx.alert(msg.type)} onClick={()=>setMsg(null)}>{msg.text} <span style={{float:"right",cursor:"pointer"}}>✕</span></div>}

      <div style={{...sx.card(),textAlign:"center"}}>
        <div style={{display:"flex",justifyContent:"center",marginBottom:8}}>
          <BiochainLogo size={60}/>
        </div>
        <div style={sx.label()}>YOUR ADDRESS</div>
        <div style={{fontSize:12,color:C.cyan,wordBreak:"break-all",marginBottom:6}}>{wallet?.address}</div>
        <button style={{...sx.btn(C.cyan,true),width:"auto",padding:"3px 12px",marginTop:0,fontSize:11}}
          onClick={()=>copy(wallet?.address,"addr")}>
          {copied==="addr"?"✅ COPIED":"📋 COPY"}
        </button>
        <div style={{marginTop:12,background:"#0a1520",borderRadius:6,padding:"14px",textAlign:"center",border:"1px solid #1a2940"}}>
          <div style={{fontSize:11,color:"#4a6b8a",letterSpacing:2,marginBottom:4}}>BALANCE</div>
          <div style={{fontSize:34,fontWeight:"bold",color:"#00ff9c",letterSpacing:2}}>{(balance??0).toFixed(2)}<span style={{fontSize:14,color:"#4a6b8a",marginLeft:6}}>BIO</span></div>
          {isNode&&<div style={{fontSize:11,color:"#00d4ff",marginTop:4}}>● NODE · {nodeInfo?.role??"—"} · rep {nodeInfo?.reputation?.toFixed(2)??"—"}</div>}
        </div>
        <div style={{marginTop:12,display:"grid",gridTemplateColumns:"1fr 1fr",gap:8}}>
          <div style={{background:C.bg,borderRadius:4,padding:"10px 6px"}}>
            <div style={sx.label()}>BIOFIELD</div>
            <div style={{fontSize:22,color:C.cyan,fontWeight:"bold"}}>{network?.biofield?.toFixed(0)??"—"}</div>
            <div style={{fontSize:11,color:C.grey}}>{network?.phase??"—"}</div>
          </div>
          <div style={{background:C.bg,borderRadius:4,padding:"10px 6px"}}>
            <div style={sx.label()}>STABILITY</div>
            <div style={{fontSize:22,color:C.green,fontWeight:"bold"}}>{network?.stability?.toFixed(3)??"—"}</div>
            <div style={{fontSize:11,color:C.grey}}>S = 1/(1+R)</div>
          </div>
        </div>
        <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:5,marginTop:8}}>
          {[
            {l:"φ_BIO",v:network?.phi_bio?.toFixed(4)??"—",c:C.cyan},
            {l:"NODES",v:network?`${network.nodes_alive??0}/${network.nodes_total??0}`:"—",c:C.green},
            {l:"BLOCKS",v:network?.blocks??"—",c:C.purple},
          ].map(m=>(
            <div key={m.l} style={{background:C.bg,borderRadius:3,padding:"5px 3px",textAlign:"center"}}>
              <div style={{fontSize:10,color:C.grey}}>{m.l}</div>
              <div style={{fontSize:13,color:m.c,fontWeight:"bold"}}>{m.v}</div>
            </div>
          ))}
        </div>
      </div>

      <div style={{display:"flex",margin:"0 16px",background:C.panel,
        borderRadius:"4px 4px 0 0",border:`1px solid ${C.border}`,borderBottom:"none"}}>
        {[["send","▲ SEND"],["receive","▼ RECEIVE"],["history","≡ HISTORY"],["swap","⇄ SWAP"],["network","◈ NETWORK"]].map(([id,label])=>(
          <button key={id} style={sx.tab(tab===id)} onClick={()=>setTab(id)}>{label}</button>
        ))}
      </div>

      {tab==="send"&&(
        <div style={{...sx.card(),borderTopLeftRadius:0,borderTopRightRadius:0,margin:"0 16px 10px"}}>
          <div style={sx.label()}>RECIPIENT ADDRESS</div>
          <input style={sx.inp()} placeholder="BIO1..." value={txAddr}
            onChange={e=>setTxAddr(e.target.value)} autoComplete="off"/>
          {txAddr&&!txAddr.startsWith("BIO1")&&(
            <div style={{fontSize:11,color:C.red,marginTop:3}}>⚠ Invalid address format</div>
          )}
          {(()=>{ const rec=loadRecents(wallet?.address); return rec.length>0&&(
            <div style={{marginTop:8}}>
              <div style={{fontSize:10,color:C.grey,marginBottom:4}}>RECENT — tap to fill</div>
              <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
                {rec.map(a=>(
                  <button key={a} onClick={()=>setTxAddr(a)}
                    style={{fontSize:11,fontFamily:"monospace",padding:"4px 8px",
                      background:txAddr===a?C.cyan+"33":"transparent",
                      border:`1px solid ${C.cyan}55`,borderRadius:6,color:C.cyan,cursor:"pointer"}}>
                    {a.slice(0,10)}…{a.slice(-4)}
                  </button>
                ))}
              </div>
            </div>
          );})()}
          <div style={{...sx.label(),marginTop:10}}>AMOUNT (BIO)</div>
          <input id="send-amount" style={sx.inp()} placeholder="0.00" type="number" min="0"
            value={txAmt} onChange={e=>setTxAmt(e.target.value)}/>
          {txAmt&&parseFloat(txAmt)>0&&(
            <div style={{fontSize:11,color:C.grey,marginTop:4,display:"flex",justifyContent:"space-between"}}>
              <span>Fee: {(0.01 + parseFloat(txAmt)*0.0005).toFixed(4)} BIO fee</span>
              <span style={{color:C.green}}>Gets: {Math.max(0, parseFloat(txAmt) - 0.01 - parseFloat(txAmt)*0.0005).toFixed(4)} BIO</span>
            </div>
          )}
          <button style={sx.btn(C.cyan)} onClick={handleSend} disabled={loading}>
            {loading?"BROADCASTING...":"→ SEND BIO"}
          </button>
        </div>
      )}

      {tab==="receive"&&(
        <div style={{...sx.card(),borderTopLeftRadius:0,borderTopRightRadius:0,
          margin:"0 16px 10px",textAlign:"center"}}>
          <div style={sx.label()}>SCAN TO SEND BIO</div>
          <div style={{display:"flex",justifyContent:"center",margin:"14px 0"}}>
            <QRCode value={wallet?.address??""} size={150} color={C.cyan}/>
          </div>
          <div style={{fontSize:12,color:C.cyan,wordBreak:"break-all",margin:"8px 0"}}>{wallet?.address}</div>
          <button style={sx.btn(C.cyan)} onClick={()=>copy(wallet?.address,"rcv")}>
            {copied==="rcv"?"✅ ADDRESS COPIED":"📋 COPY ADDRESS"}
          </button>
        </div>
      )}

      {tab==="history"&&(
        <div style={{...sx.card(),borderTopLeftRadius:0,borderTopRightRadius:0,margin:"0 16px 10px"}}>
          <div style={sx.label()}>TRANSACTION HISTORY</div>
          {history.length===0?(
            <div style={{fontSize:12,color:C.grey,textAlign:"center",padding:"20px 0"}}>No transactions yet</div>
          ):history.map(bl=>{
            const out=bl.tx?.from===wallet?.address;
            const other=out?bl.tx?.to:bl.tx?.from; // the counterparty either way
            return(
              <div key={bl.index} style={{borderBottom:`1px solid ${C.border}`,padding:"9px 0",
                display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                <div style={{minWidth:0,flex:1}}>
                  <div style={{fontSize:12,color:out?C.red:C.green,fontWeight:"bold"}}>
                    {out?"▲ SENT":"▼ RECEIVED"}
                  </div>
                  <div style={{fontSize:11,color:C.grey,marginTop:2}}>
                    Block #{bl.index}
                    {bl.hash&&(
                      <span onClick={()=>copy(bl.hash,`h${bl.index}`)}
                        style={{marginLeft:6,color:C.cyan,cursor:"pointer",fontFamily:"monospace"}}>
                        {copied===`h${bl.index}`?"✅ copied":`#${bl.hash.slice(0,10)}…`}
                      </span>
                    )}
                  </div>
                  <div onClick={()=>{ if(other){ setTxAddr(other); setTab("send"); setTimeout(()=>document.getElementById("send-amount")?.focus(),50); } }}
                    style={{fontSize:11,color:C.cyan,cursor:"pointer",fontFamily:"monospace",
                      wordBreak:"break-all",marginTop:2}}
                    title="Tap to send to this address">
                    {out?"→ ":"← "}{other} <span style={{color:C.grey}}>(tap to send)</span>
                  </div>
                </div>
                <div style={{textAlign:"right",marginLeft:8}}>
                  <div style={{fontSize:14,color:out?C.red:C.green,fontWeight:"bold"}}>
                    {out?"−":"+"}{bl.tx?.value?.toFixed(2)} BIO
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {tab==="swap"&&(
        <div style={{...sx.card(),borderTopLeftRadius:0,borderTopRightRadius:0,margin:"0 16px 10px"}}>
          {/* sub-tabs */}
          <div style={{display:"flex",gap:4,marginBottom:10}}>
            {[["board","BOARD"],["deals","MY DEALS"],["history","DONE"]].map(([id,label])=>(
              <button key={id} onClick={()=>{setSwapView(id); if(id==="board")fetchOffers(); else fetchMyLocks();}}
                style={{flex:1,padding:"6px 0",fontSize:11,letterSpacing:1,
                  background:swapView===id?C.accent:C.panel,color:swapView===id?C.bg:C.grey,
                  border:`1px solid ${C.border}`,borderRadius:3,cursor:"pointer"}}>{label}</button>
            ))}
          </div>

          {/* one-time warning */}
          <div style={{fontSize:10,color:C.gold,marginBottom:10,lineHeight:1.4,padding:"6px 8px",
            background:C.panel,border:`1px solid ${C.gold}33`,borderRadius:3}}>
            ⚠ Whatever you receive in exchange stays in YOUR OWN wallet for that asset. Verify the
            amount and confirmations there BEFORE revealing any secret. The swap secret lives only
            in memory during an active deal — do not close the wallet mid-deal.
          </div>

          {swapView==="board"&&(<>
            {/* create offer */}
            <div style={sx.label()}>PUBLISH OFFER</div>
            <input style={sx.inp()} placeholder="Give — BIO amount" value={offGive}
              onChange={e=>setOffGive(e.target.value)} inputMode="decimal"/>
            <input style={sx.inp()} placeholder="Want — what asset?" value={offAsset}
              onChange={e=>setOffAsset(e.target.value)}/>
            <input style={sx.inp()} placeholder="Want — amount" value={offWant}
              onChange={e=>setOffWant(e.target.value)} inputMode="decimal"/>
            <input style={sx.inp()} placeholder="Your receiving address for that asset" value={offAddr}
              onChange={e=>setOffAddr(e.target.value)}/>
            <button style={sx.btn(C.accent)} onClick={handleCreateOffer} disabled={loading}>
              PUBLISH TO BOARD
            </button>
            <div style={{height:1,background:C.border,margin:"12px 0"}}/>
            <div style={sx.label()}>OPEN OFFERS</div>
            {offers.length===0 && <div style={{fontSize:11,color:C.grey,textAlign:"center",padding:8}}>No open offers</div>}
            {offers.map(o=>(
              <div key={o.offer_id} style={{padding:"8px",marginBottom:6,background:C.panel,
                border:`1px solid ${C.border}`,borderRadius:3,fontSize:11}}>
                <div style={{color:C.light}}>{o.give_bio} BIO → {(o.want_amount/EXT_UNIT).toFixed(8)} {o.want_asset||"?"}</div>
                <div style={{color:C.grey,fontSize:10,marginTop:2}}>from {o.sender.slice(0,16)}… · expires in {Math.floor(o.expires_in/3600)}h</div>
                <div style={{color:C.grey,fontSize:10,wordBreak:"break-all"}}>{o.want_asset||"external"} address: {o.ext_address}</div>
                {o.sender===wallet.address
                  ? <button style={{...sx.btn(C.red),fontSize:10,padding:"4px 0",marginTop:4}} onClick={()=>handleCancelOffer(o.offer_id)}>CANCEL MY OFFER</button>
                  : <div style={{fontSize:10,color:C.gold,marginTop:4}}>To take: send funds on the external network per the deal, then coordinate the hash-lock (deal wizard — see MY DEALS after locking)</div>}
              </div>
            ))}
          </>)}

          {swapView==="deals"&&(<>
            <div style={sx.label()}>LOCK BIO FOR A COUNTERPARTY</div>
            <input style={sx.inp()} placeholder="Counterparty BIO1… address" id="lock-rcv"/>
            <input style={sx.inp()} placeholder="BIO amount to lock" id="lock-amt" inputMode="decimal"/>
            <button style={sx.btn(C.accent)} disabled={loading}
              onClick={()=>handleStartLock(document.getElementById("lock-rcv").value.trim(), document.getElementById("lock-amt").value)}>
              LOCK UNDER HASH-LOCK
            </button>
            <div style={{height:1,background:C.border,margin:"12px 0"}}/>
            <div style={sx.label()}>ACTIVE & RECENT LOCKS</div>
            {myLocks.length===0 && <div style={{fontSize:11,color:C.grey,textAlign:"center",padding:8}}>No locks yet</div>}
            {myLocks.map(l=>{
              const iAmSender = l.sender===wallet.address;
              const iAmReceiver = l.receiver===wallet.address;
              const secret = swapSecrets.current[l.lock_id];
              return (
                <div key={l.lock_id} style={{padding:"8px",marginBottom:6,background:C.panel,
                  border:`1px solid ${l.state==="LOCKED"?C.accent:C.border}`,borderRadius:3,fontSize:11}}>
                  <div style={{color:C.light}}>{l.amount_bio} BIO · <span style={{color:l.state==="LOCKED"?C.gold:C.grey}}>{l.state}</span></div>
                  <div style={{color:C.grey,fontSize:10,marginTop:2}}>
                    {iAmSender?`→ to ${l.receiver.slice(0,14)}…`:`← from ${l.sender.slice(0,14)}…`}
                    {l.state==="LOCKED" && ` · ${Math.floor(l.expires_in/3600)}h ${Math.floor((l.expires_in%3600)/60)}m left`}
                  </div>
                  {l.preimage && <div style={{color:C.grey,fontSize:9,wordBreak:"break-all",marginTop:2}}>secret revealed: {l.preimage}</div>}
                  {/* Sender with an active lock: show secret (to reveal on the external network's side) + refund after timeout */}
                  {l.state==="LOCKED" && iAmSender && secret && (
                    <div style={{fontSize:9,color:C.gold,wordBreak:"break-all",marginTop:4}}>your secret (reveal on the external network's side to claim your funds): {secret}</div>
                  )}
                  {l.state==="LOCKED" && iAmSender && l.expires_in===0 && (
                    <button style={{...sx.btn(C.red),fontSize:10,padding:"4px 0",marginTop:4}} onClick={()=>handleRefund(l.lock_id)}>REFUND (timeout passed)</button>
                  )}
                  {/* Receiver with an active lock: enter secret read from counterparty's transaction on the external network, then claim */}
                  {l.state==="LOCKED" && iAmReceiver && (
                    <div style={{marginTop:4}}>
                      <input style={{...sx.inp(),fontSize:10,marginBottom:4}} placeholder="Preimage (64 hex) from the external network" id={"claim-"+l.lock_id}/>
                      <button style={{...sx.btn(C.green),fontSize:10,padding:"4px 0"}}
                        onClick={()=>handleClaim(l.lock_id, document.getElementById("claim-"+l.lock_id).value)}>CLAIM BIO</button>
                    </div>
                  )}
                </div>
              );
            })}
          </>)}

          {swapView==="history"&&(<>
            <div style={sx.label()}>COMPLETED SWAPS</div>
            {myLocks.filter(l=>l.state!=="LOCKED").length===0 && <div style={{fontSize:11,color:C.grey,textAlign:"center",padding:8}}>No completed swaps</div>}
            {myLocks.filter(l=>l.state!=="LOCKED").map(l=>(
              <div key={l.lock_id} style={{padding:"8px",marginBottom:6,background:C.panel,
                border:`1px solid ${C.border}`,borderRadius:3,fontSize:11}}>
                <div style={{color:C.light}}>{l.amount_bio} BIO · <span style={{color:l.state==="CLAIMED"?C.green:C.grey}}>{l.state}</span></div>
                <div style={{color:C.grey,fontSize:10}}>{l.sender===wallet.address?"sent":"received"} · {l.hash_lock.slice(0,16)}…</div>
              </div>
            ))}
          </>)}
        </div>
      )}

      {tab==="network"&&(
        <div style={{...sx.card(),borderTopLeftRadius:0,borderTopRightRadius:0,margin:"0 16px 10px"}}>
          <div style={sx.label()}>NETWORK TRANSPARENCY</div>
          {!dashboard && !dashboardError && <div style={{fontSize:11,color:C.grey,textAlign:"center",padding:12}}>Loading…</div>}
          {dashboardError && <div style={{fontSize:11,color:C.red,textAlign:"center",padding:12,wordBreak:"break-all"}}>Error: {dashboardError}</div>}
          {dashboard && (<>
            <div style={{display:"flex",gap:6,marginBottom:10}}>
              <div style={{flex:1,padding:"8px",background:C.panel,border:`1px solid ${C.border}`,borderRadius:3,textAlign:"center"}}>
                <div style={{fontSize:18,color:C.accent}}>{dashboard.node_count.alive}</div>
                <div style={{fontSize:9,color:C.grey}}>ALIVE NODES</div>
              </div>
              <div style={{flex:1,padding:"8px",background:C.panel,border:`1px solid ${C.border}`,borderRadius:3,textAlign:"center"}}>
                <div style={{fontSize:18,color:C.grey}}>{dashboard.node_count.dead}</div>
                <div style={{fontSize:9,color:C.grey}}>DEAD NODES</div>
              </div>
              <div style={{flex:1,padding:"8px",background:C.panel,border:`1px solid ${C.border}`,borderRadius:3,textAlign:"center"}}>
                <div style={{fontSize:18,color:C.light}}>{dashboard.node_count.total}</div>
                <div style={{fontSize:9,color:C.grey}}>TOTAL EVER</div>
              </div>
            </div>

            <div style={sx.label()}>TIER DISTRIBUTION</div>
            <div style={{marginBottom:10}}>
              {Object.entries(dashboard.tier_distribution).map(([tier,count])=>(
                <div key={tier} style={{display:"flex",justifyContent:"space-between",fontSize:11,color:C.light,padding:"3px 0"}}>
                  <span>{tier}</span><span style={{color:C.grey}}>{count}</span>
                </div>
              ))}
            </div>

            <div style={sx.label()}>BALANCE CONCENTRATION (live nodes)</div>
            <div style={{marginBottom:10,fontSize:11,color:C.light}}>
              <div>Top 1 address: <span style={{color:dashboard.balance_concentration.top1_pct>50?C.red:C.gold}}>{dashboard.balance_concentration.top1_pct}%</span></div>
              <div>Top 5 addresses: {dashboard.balance_concentration.top5_pct}%</div>
              <div>Top 10 addresses: {dashboard.balance_concentration.top10_pct}%</div>
            </div>

            <div style={sx.label()}>STAKE CONCENTRATION (live nodes)</div>
            <div style={{marginBottom:10,fontSize:11,color:C.light}}>
              <div>Top 1 address: {dashboard.stake_concentration.top1_pct}%</div>
              <div>Top 5 addresses: {dashboard.stake_concentration.top5_pct}%</div>
              <div>Top 10 addresses: {dashboard.stake_concentration.top10_pct}%</div>
            </div>

            {dashboard.synchronized_birth_clusters.length>0 && (
              <>
                <div style={sx.label()}>SYNCHRONIZED BIRTH CLUSTERS</div>
                <div style={{marginBottom:10}}>
                  {dashboard.synchronized_birth_clusters.map((c,i)=>(
                    <div key={i} style={{fontSize:11,color:C.gold,padding:"3px 0"}}>
                      {c.count} nodes born within one window — worth a look, not proof of anything
                    </div>
                  ))}
                </div>
              </>
            )}

            <div style={{fontSize:9,color:C.grey,lineHeight:1.4,marginTop:8,padding:"6px 8px",
              background:C.panel,border:`1px solid ${C.border}`,borderRadius:3}}>
              {dashboard.limitations}
            </div>
          </>)}
        </div>
      )}

      {/* Footer */}
      <div style={{margin:"4px 16px",display:"flex",gap:5,flexWrap:"wrap"}}>
        <button style={{...sx.btn(C.red,true),fontSize:11,padding:"6px 0",flex:1}} onClick={handleLogout}>
          🔒 LOCK
        </button>
        {bioAvail&&!bioRegistered&&(
          <button style={{...sx.btn(C.green,true),fontSize:11,padding:"6px 0",flex:1}}
            onClick={handleRegisterBiometric} disabled={bioScanning}>
            {bioScanning?"SCANNING...":"👆 ADD BIOMETRIC"}
          </button>
        )}
        {bioAvail&&bioRegistered&&(
          <button style={{...sx.btn(C.green,true),fontSize:11,padding:"6px 0",flex:1}} disabled>
            👆 BIOMETRIC ON
          </button>
        )}
        <button style={{...sx.btn(C.gold,true),fontSize:11,padding:"6px 0",flex:1}}
          onClick={()=>{setSeedVisible(true); setTimeout(()=>setSeedVisible(false),30000);}}>
          🔑 SEED
        </button>
        <button style={{...sx.btn(C.grey,true),fontSize:11,padding:"6px 0",flex:1}} onClick={fetchState}>
          ↻
        </button>
      </div>

      {seedVisible&&seedWords.length>0&&(
        <div style={{...sx.card(),borderColor:C.gold}}>
          <div style={{fontSize:11,color:C.gold,marginBottom:6,letterSpacing:2}}>🔑 SEED — AUTO-HIDES IN 30s</div>
          <WordGrid words={seedWords} reveal={true}/>
          <button style={sx.btn(C.gold)} onClick={()=>copy(seedWords.join(" "),"seed2")}>
            {copied==="seed2"?"✅ COPIED":"📋 COPY SEED"}
          </button>
        </div>
      )}

      <div style={{textAlign:"center",paddingTop:12,fontSize:11,color:C.grey}}>
        🛡 ML-DSA-44 · AES-256-GCM · WebAuthn · v{VERSION}
      </div>
    </div>
  );
}
