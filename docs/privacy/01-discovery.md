# Privacy data-flow — Phase (a) discovery (2026-10-14)

Read-only survey of the Hub (`Home & Family`, HEAD bdbe690), the llmsel gateway (916eb82) and the VPS. Nothing was changed. No personal data was read or printed (only paths, permissions, schemas, code).

## What already exists (reuse, don't rebuild)

**Hub**
- One thin gateway client, `app/llm_gateway.py`: the only outbound LLM path (no provider SDKs, no keys). It is the natural choke point for redaction + tier checks. Call sites: `services/extraction.py` (bill, statement, utility, classify), `services/wiki_engine.py`, `services/domain_classifier.py`, `services/ask/*`, `services/wiki_lint`, `services/position_extraction.py`.
- Domain registry (`app/domains/registry.py`): domains financials, house, insurance. Each declares categories + fields. A `Document` already carries `domain` + `category` (set at Inbox approval, or at upload) — so a tier can be derived from (domain, category) at the single place where a document is filed.
- Structured data already exists: `transactions`, `accounts`, `merchants`, `utility_readings`, `records`, `debts`, `commitments`, `positions` (loan/insurance positions), and the wiki (`wiki_pages` → `wiki_claims` → `wiki_claim_sources` → `documents`), i.e. claims already link back to source documents. `Document.status` has `needs_attention`; `inbox_items.confidence` exists.
- No health/medical or tax domain yet. No concept of sensitivity anywhere in the Hub.

**Gateway (llmsel)** — shared by other Workers; contract must stay backward compatible (its CLAUDE.md)
- Already has a `privacy_tier` column (`personal|private|business|public`) on `workload_types` and per-pairing on `worker_task_bindings`. **These are the existing tiers: untouched.** The selector excludes models lacking a `no_retention_claim` for certain tiers, and keeps Nous out of them.
- OpenRouter adapter already sends `provider: {sort: price, data_collection: deny, quantizations: [...]}` on every call. It does **not** send `zdr: true` (the gap to close in phase c, after verifying OpenRouter's current docs).
- Nous adapter sends no provider controls at all (Nous rejects them with 400) — so no retention guarantee can be enforced there. Their published retention terms still to be checked in phase c.
- `/run` request is `extra: forbid` — a new optional field is possible but must be additive.
- `usage` table logs tokens, cost, endpoint, quantization, attachment count/bytes/pages — never content. Missing for our purposes: "redaction applied", tier, redaction count. (Gateway-side addition = new migration; Hub-side log can carry these without touching the shared gateway.)
- `batch_handles` stores prompt text (`system`, `user_msg`) at rest in `llmsel.db` — only used for batch calls; the Hub does not use batch today. Flag only.

## Conflicts with the plan (need a decision)

1. **Raw statements go to the model today.** Bank statements, bills and insurance docs are sent as PDF/image attachments for vision extraction (`extraction.py`), and Ask's `read_document_file` attaches raw files. "HIGH never raw" cannot hold for those paths as they exist. Redacting a scanned image/PDF is not possible deterministically without OCR on the VPS (no GPU).
2. **Ask sends wiki claims + `find_sources` field text** to the model; these can contain account numbers, policy numbers, names.
3. **Gateway privacy_tier ≠ house tiers.** The new LOW/MID/HIGH_house tiers must be a separate, additive dimension; no edit to existing values or rows.
4. **Likely supply problem:** the Hub's pairings all run GLM 5.3 Flash (Z.AI, "eastern" in the gateway's region policy). If OpenRouter has no ZDR endpoint for it, fail-closed would block MID calls. To verify in phase c; fix would be to re-point only the Hub's pairings, not the shared types.

## VPS baseline (read-only checks)

| Finding | Severity | Proposed fix (not applied) |
|---|---|---|
| `data/home_family.db` is mode 644 (`data/` 775); `llmsel.db` 644 | Low-med (single-user box, but world-readable on a box that runs other apps' users) | `chmod 600` / `640` + group; |
| Uploaded documents sit under `app/static/documents/` and nginx serves `/static/` straight from disk (`alias`), `expires 7d`, `Cache-Control: public`. Anyone who passes Cloudflare Access and knows/guesses a filename gets the PDF; Access is the only gate | Medium | Move documents out of `static/`, serve through the app (which already checks the Access JWT) |
| No automated backups; ~14+ manual DB copies in `/root` (mode 644, plaintext, same disk) incl. ones containing all statements | Medium | Nightly `sqlite3 .backup`, encrypted (age), off-box; prune the old plaintext copies after |
| Disk not encrypted (plain ext4) | Low (provider-level), note only | Accept / encrypt backups instead |
| Ports 8000/8001/8004/8006 listen on 0.0.0.0 | Low — UFW is active and allows only SSH + Nginx | Rebind to 127.0.0.1 for defence in depth |
| `.env` and the bank key are 600, dir 700 | OK | — |
| Secrets in plaintext `.env` | Accepted for this scale | — |

## Proposed approach (summary; details go in DECISIONS.md as they're made)
- Tier = pure function of (domain, category), stored on `Document` and `Record` (new column, default HIGH_house, backfill). Own table of tier names; existing gateway tiers untouched.
- Redactor + encrypted per-session mapping live in the Hub; enforcement sits in the Hub's single gateway client (fail closed), with an additive optional field to the gateway so it can *also* restrict endpoints (ZDR) for house-tier calls.
- Extraction pipeline builds on the existing tables rather than a new schema; generalises with one `extracted_facts`-style table only if the existing ones can't hold lab values / mortgage rate history.

## Pre-existing things to leave alone
Existing gateway tiers and bindings; eu_track bindings; the other wired Workers; the uncommitted Recipes/WhatsApp/Comics files in the monorepo.
