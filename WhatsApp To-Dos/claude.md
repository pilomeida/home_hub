# WhatsApp To-Do App

## What this is
A personal to-do manager. Front office is WhatsApp (via Twilio sandbox). 
Back office is a Next.js web UI. Solo user only (Pedro).

## Stack
- Next.js 14 (App Router) + TypeScript
- Supabase (Postgres) for the database
- Twilio WhatsApp Sandbox for messaging
- Anthropic Claude API for intent parsing
- Deployed on Vercel, code on GitHub


## Specifications
Specifications are in whatsapp-todo-spec.md
If during the build there are decisions that adjust the specs, edit the file in accordance at the end of each run


## Key files
- `app/api/whatsapp/route.ts` — Twilio webhook handler
- `app/api/todos/route.ts` — REST API for the web UI
- `lib/claude.ts` — natural language → structured intent parser
- `lib/todos.ts` — all Supabase CRUD operations
- `lib/supabase.ts` — Supabase client

## Environment variables
See `.env.example` for all required vars.

## Important rules
- Timezone is always Europe/Lisbon
- Only messages from MY_WHATSAPP_NUMBER are processed (single user)
- Webhook must respond within 5 seconds (Twilio timeout)
- Never expose service role key on the client side

## Current status
All features built and deployed as of 2026-03-26.

- ✅ WhatsApp webhook (Twilio sandbox) — all 7 intents working
- ✅ Claude intent parser (claude-sonnet-4-20250514)
- ✅ Supabase CRUD layer
- ✅ REST API (`/api/todos` — GET, POST, PATCH, DELETE)
- ✅ Back-office web dashboard (filters, inline edit, add form)
- ✅ Deployed on Vercel at https://project-tygma.vercel.app

**Known gotcha:** Twilio sandbox sessions expire every 72h — rejoin by sending `join person-sign` to `+1 415 523 8886` from WhatsApp.
