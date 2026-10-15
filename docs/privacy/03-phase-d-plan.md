# Phase (d) plan — tiers in the Hub, recognising documents, extraction (draft 2026-10-15)

Order matters: each step ships working on its own and leaves the live Hub unchanged until a type is configured.

1. **Type settings in the database + admin screen** (D12). Tables: `document_type_settings(domain, category, tier, redact, updated_by, updated_at)` and a change log. `tier_for` reads the table (cached), no code dict. Screen under Settings: one row per type, tier dropdown, redact switch, "not set" banner.
2. **Recognition** (D13). Table `document_fingerprints(kind, pattern, domain, category, source, created_by)`. A recogniser runs first on every intake path (web upload pages already know their type; email, Telegram and Inbox do not). No AI. Unmatched or image-only documents land on the "What is this?" card; choosing a type creates a fingerprint when "Remember" is ticked. The existing LLM Inbox classifier is called only after a tier is known and only as a suggestion.
3. **Extraction schema** (generic): `extracted_facts(document_id, page, kind, field, value, unit, confidence, status[ok|needs_check|confirmed|rejected], checked_by, checked_at)` plus type-specific views over the tables that already exist (transactions, positions, records, wiki claims). Every row keeps document + page.
4. **Bank statements first**: reuse the existing extractor; add the arithmetic check (opening + lines = closing), per-line confidence, page references; failures create "To check" items (D11). Held-out-until-checked rule applies to doubtful lines.
5. **"To check" list + weekly Telegram nudge** (D11).
6. Insurance, then medical (needs a Health domain) — separate steps after bank statements are verified on real statements by Pedro.

Open questions for Pedro are in the chat message of 2026-10-15 (what happens to unset types; photo handling confirmation; suggested starting tiers).
