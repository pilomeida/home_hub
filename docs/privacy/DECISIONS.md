# Privacy data-flow — decisions

Newest first. Technical choices are Claude's; Pedro decides only what he sees/does.

## 2026-10-15 — D11. Review screen (Pedro's answers)
"To check" list on the Inbox page. (1) While a value waits, it is held out of totals and Ask answers, marked "pending check"; lines in statements that add up to their balances count immediately. (2) Alert = a weekly Telegram nudge from the existing bot ("N things to check"), plus the count on the Inbox. (3) Anyone in the family can check; each confirmed value records who. (4) Photos/scans may produce more cards; accepted. Cards show the source page next to the extracted values, highlight the doubtful one, and offer "Looks right" / "Fix the value" / "Not a real line"; corrections are remembered.

## 2026-10-15 — D12. Tier wiring is configuration in the Hub, not code (Pedro)
Supersedes the code-level `WIRING` dict in `app/privacy/tiers.py` (D1/D2). A Hub settings screen lists every document type (domain + category) with its tier (low/mid/high/not set) and the redaction switch; changes apply at once and are logged (who, when, old, new). Stored in the database. A type that is "not set" behaves as today (D2) and is shown with a banner so it is not forgotten. Existing documents of a type are re-tiered when its setting changes.

## 2026-10-15 — D13. Recognising documents: known ones automatic, new ones ask (Pedro; mechanism by Claude)
- **Known, recurrent documents** are recognised without any AI, by fingerprints the Hub holds: sender address (email/Telegram sender), issuer words in the PDF's own text layer, filename pattern, account/contract number already on file. A match assigns the document type and therefore the tier, and the document proceeds.
- **New or unrecognised documents** stop at a "What is this?" card with the list of types (including "Sensitive: don't read it") before anything reads them. Choosing a type sets the tier; the card then offers "Remember this for next time" (on by default), which stores a new fingerprint.
- **Scans and photos have no text layer to fingerprint**, so most go the "What is this?" route unless the sender or caption identifies them. Nothing is sent to any AI until the type (and so the tier) is known. This also removes a chicken-and-egg: today the classifier sends the raw file to a model to decide what it is, before any tier exists.
- Optional later: for text PDFs only, a mid-tier (zero-retention, US/EU, redacted) suggestion to pre-select the type on the card. Not built unless Pedro asks.

## 2026-10-15 — D12 (built, step 1). Settings screen at /privacy (Pedro's answers: unset = held; suggestions pre-filled)
- **Storage:** tables `document_type_settings`, `privacy_settings`, `tier_changes` (Hub migration a8c4e2f6b913). No tier is in code any more (the earlier `WIRING` dict is removed).
- **Behaviour:** a global switch "Levels are binding" is **off** until Pedro switches it on, so the live Hub is unchanged meanwhile. When on, a document whose type has no level is **held** (`TierHeld`: nothing may read it) — this is the gate every reader must call (`app.privacy.settings.require_ready`). Suggested levels are shown pre-filled but only count after "Confirm all suggestions" or a per-row Save. Suggestions: house documents low; bills, statements, loan papers, ownership documents and all insurance mid; house_high is never suggested (no route). A document type added later suggests mid.
- **Saving** a type logs who/when/old/new and re-stamps every document and record already filed under it.
- **Existing gateway levels on the same screen ("The AI jobs"):** for each of the Hub's own jobs, shows the model and lets Pedro change the provider-privacy level and the model-strength level (the gateway's existing per-pairing overrides). Backed by new worker-scoped gateway endpoints `GET /worker/pairings` and `PUT /worker/pairings/{workload_type}`: a Worker can only see/change its own pairings' two overrides (never the shared workload types, another Worker's pairing, or the chosen model); a change that would leave the pairing with no eligible supplier is refused (400) before anything is written. "Standard" = inherit the type's value.
- **Not yet wired:** the readers (extraction, Ask, wiki, classifier) do not call `require_ready` yet; that comes with step 2 (recognition). So switching the levels on today holds nothing and changes nothing.
- **Shipping note:** the Hub migrations f7b3d5e9a182 and a8c4e2f6b913 chain on `e6a2c4d8f731` (uncommitted, from the in-flight transaction-description work); production is at `d5f1b3c9e826`. They must ship together with, or after, that work.

## 2026-10-14 — D8. Gateway mechanism: one optional field, tighten-only (Claude; US/EU rule and migration added 2026-10-15 at Pedro's request)
`POST /run` gains an optional `sensitivity` (`house_low|house_mid|house_high`). Absent or `house_low` = today's behaviour for every wired app. `house_mid` = the selector only considers suppliers that can be made to enforce zero data retention per request (today: OpenRouter only); the OpenRouter call carries `provider.zdr: true` and `provider.only` = providers whose **declared headquarters is in the US or EU-27** (61 providers, generated from OpenRouter's `/providers` list by `scripts/refresh_house_mid_providers.py`; unknown HQ excluded; new providers excluded until the script is re-run; an empty list refuses; an operator veto list exists). Failover is filtered the same way and a last-line guard refuses to send to a non-enforceable supplier. `house_high` and unknown values are refused (HTTP 403/422) before anything is selected or sent. The level is stored on `usage.sensitivity` (gateway migration 14, one additive nullable column) and logged. Existing gateway tiers, pairings, workload types and the other Workers are untouched. **Shipped:** gateway main `f8a54b1`, deployed 2026-10-15 (schema 14, healthy). The audio change (`916eb82`, schema 13) was landed first, separately. Limit: "declared HQ" is OpenRouter's field, not an audit of ownership or data-centre location (e.g. Novita is declared US); veto list is the remedy.

## 2026-10-14 — D9. Hub checkpoint (Claude)
Every Hub call to the gateway now passes `app/privacy/outbound.py` first (inside `GatewayClient.run`). Fails closed on: house_high, unknown tier, redaction requested without a tier, redaction failure or leftover identifiers, redaction on a call with images/PDFs (they cannot be redacted), redaction with tool use (not supported yet). Audit line per call: tier, whether redacted, counts by kind, sizes, outcome — never content. Unwired call sites pass `sensitivity=None` and behave as before.

## 2026-10-14 — D10. Provider retention findings (verified 2026-10-14)
- **OpenRouter** (docs: provider routing; ZDR guide): `provider.zdr: true` restricts routing to endpoints with a zero-data-retention policy; `provider.data_collection: "deny"` excludes providers that may store/train on data. A request can only switch ZDR on, never off an account/guardrail setting. Endpoints with unclear policy are treated as retaining. Public list: `GET /api/v1/endpoints/zdr`. Caveats: ZDR covers model inference only (not plugins/web-search tools); implicit in-memory caching is not counted as retention; the docs do not state the error returned when no ZDR endpoint exists (expect a 404 "no endpoints"; the call fails, it is never retried without the flag). The "Data Policy" tags are OpenRouter's best knowledge, not a third-party guarantee.
- **GLM 5.3 Flash on OpenRouter:** the live ZDR list has ~29 endpoints for it; 7 pass the gateway's quantisation floor (fp8): BaseTen, Inceptron, Near AI, SiliconFlow, Morph, Novita, Z.AI. So the Hub's current model can serve house_mid. With the US/EU rule (D8) the usable ones are BaseTen, Morph, Inceptron (SE) and Novita; Z.AI and SiliconFlow (declared SG) and Near AI (no declared HQ) are excluded.
- **Nous Portal** (portal.nousresearch.com/privacy): no fixed retention period for API prompts/outputs. Without "Privacy Mode" prompts and outputs may be shared with third parties and used to train/fine-tune. "Privacy Mode" (account setting) says inference payloads are not stored or used for training, with security/legal/fraud exceptions; it is prospective only. No zero-data-retention product is described, and Nous's API rejects any per-request privacy parameter. Conclusion: Nous stays out of house_mid and house_high; at most house_low.
- **house_high route (future):** nothing exists yet. Dedicated capacity (e.g. Mistral) would need its own adapter and a verified per-request or contractual guarantee before it is added to `ZDR_ENFORCEABLE_PROVIDERS`-style allow-listing.

## 2026-10-14 — D4. Redactor design (Claude)
Detection is by shape + checksum (IBAN mod-97, NIB check digits, Luhn, NIF check digit), plus a lookup of values the Hub already knows (family names and name parts, addresses, policy/contract and account numbers). Rationale: no model is involved, so nothing leaves the VPS to find what to hide; checksums keep ordinary numbers (amounts, invoice refs) from being redacted. Over-redaction is the safe failure: a 9-digit number starting 2 or 9 may be labelled PHONE instead of NIF; it is hidden either way. Known limit: a value the Hub has never seen (a stranger's name, a free-text address) cannot be caught — which is why redaction is an extra layer on top of the tier's endpoint rule, not the rule itself.
After redacting, the output is re-scanned; anything still detectable, or any redactor error, blocks the call (`TierBlocked`).

## 2026-10-14 — D5. Vault and key (Claude)
One vault per session; same value, same placeholder, even if written differently (spaced vs unspaced IBAN). Restore puts back the first spelling seen. The mapping can be sealed with Fernet (authenticated encryption) using `HUB_PRIVACY_KEY` from the server's `.env`; wrong or missing key = error, never plaintext fallback. The key must be created and added to the VPS `.env` before sealed vaults are stored (not needed until phase c/d wires a call site).

## 2026-10-14 — D6. Tier column and backfill (Claude)
`sensitivity_tier` (nullable text) added to `documents` and `records` (migration f7b3d5e9a182, additive). NULL means "no tier wired for this type". `backfill_tiers` fills only NULLs from the wiring table; with nothing wired it changes nothing. Unknown or malformed tier values are rejected (`TierBlocked`), never defaulted. Revisits the original "unknown = HIGH" rule: today's data is unwired, and HIGH has no route, so defaulting to HIGH would block the whole live Hub (see D2).

## 2026-10-14 — D7. Photo/scan notice (Pedro's request)
Shown on the web upload forms (House, Insurance, Bills) and appended to the Telegram reply when the file is an image: "for photos and scans, privacy protection is less reliable…" (text in `app/privacy/notices.py`). Email-forwarded images are not yet covered (the mail poller sends no reply).

## 2026-10-14 — D1. Sensitivity tiers (Pedro's definition)

| Tier | Where a call may go | Status |
|---|---|---|
| `house_low` | Any provider:model | Defined |
| `house_mid` | Zero-data-retention (ZDR) endpoints only | Defined |
| `house_high` | Highly sensitive data/documents. Route TBD — likely dedicated inference capacity (e.g. via Mistral) | Defined as a tier; **no route yet** |

- **No document type is wired to any tier yet.** Pedro will wire each type "in due time". Until a type is wired, the Hub behaves exactly as today.
- `house_high` is declared now but nothing is attached to it. A call that asks for `house_high` while no route exists is **blocked** (fail closed), never downgraded to mid or low.
- Rationale for the `house_` prefix: the gateway already has quality tiers `low/high/top` (`min_quality_tier`) and privacy tiers `personal/private/business/public`. The prefix keeps the new dimension unmistakably separate. **Existing gateway tiers, bindings and workload types are not edited or deleted** (other Workers depend on them).
- Supersedes the earlier draft (LOW/MID/HIGH_house with "MID = redacted text + ZDR", "HIGH = structured values only").

## 2026-10-14 — D2. Enforcement is per call site and dormant until wired (Claude)
The earlier rule "unknown tier → treat as high → block" would break today's live extraction, Ask and wiki (nothing is wired). So: a call site is enforced only once its document type has a tier. A call that *declares* a tier is always enforced; a call that declares none keeps today's behaviour. Wiring a type = one registry entry; enforcement then starts for it.

## 2026-10-14 — D3. Redaction is a separate, optional layer (Claude)
Pedro's MID definition names ZDR only. Redaction (deterministic, CPU-only, encrypted local mapping) is still built, but is a per-tier/per-type switch rather than a hard requirement, because redacting identifiers can defeat extraction of those same identifiers. Default: off for `house_low`, available for `house_mid`, to be chosen per document type when wiring.

## 2026-10-15 — Live verification (synthetic text, Hub's `classification` pairing)
`sensitivity=house_mid` → 200 via `openrouter:z-ai/glm-5.3-flash`, row stored with `sensitivity=house_mid`; `house_high` → 403 `sensitivity_blocked`; no sensitivity → 200, unchanged. **Still to confirm:** which endpoint actually served the mid call (`served_by_endpoint` is filled later by the gateway's reconciliation); it should be one of BaseTen/Morph/Inceptron/Novita.
