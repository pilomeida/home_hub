# WhatsApp To-Do App — Claude Code Spec

## Overview
Build a personal to-do manager where the front office is WhatsApp (via Twilio sandbox) and the back office is a clean web UI. The stack should be chosen for simplicity and zero/near-zero cost at personal scale.

---

## Recommended Stack

- **Runtime**: Node.js with TypeScript
- **Framework**: Next.js 14 (App Router) — handles both the API routes (webhook) and the web UI in one project
- **Database**: Supabase (hosted Postgres) — use the free tier
- **WhatsApp**: Twilio WhatsApp Sandbox
- **AI layer**: Anthropic Claude API (claude-sonnet-4-20250514) — for parsing natural language messages into structured to-dos
- **Deployment**: Vercel (Hobby free tier)
- **Styling**: Tailwind CSS

---

## On GitHub and file structure
Before writing any code: initialise a git repo, and set up the project assuming it will be pushed to GitHub and connected to Vercel for automatic deployments. Include a .env.example file listing all required environment variables (with no actual values).
Before writing any code, confirm the full file structure you plan to create and ask me if I have any questions.

---

## Environment Variables Needed

```
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
ANTHROPIC_API_KEY=
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
MY_WHATSAPP_NUMBER=whatsapp:+351XXXXXXXXX  # owner's number, for security check
```

---

## Database Schema (Supabase / Postgres)

```sql
create table todos (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  notes text,
  priority text check (priority in ('high', 'medium', 'low')) default 'medium',
  deadline date,
  done boolean default false,
  done_at timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
```

---

## Project Structure

```
/
├── app/
│   ├── api/
│   │   └── whatsapp/
│   │       └── route.ts        # Twilio webhook POST handler
│   ├── page.tsx                # Back-office UI (to-do dashboard)
│   └── layout.tsx
├── lib/
│   ├── supabase.ts             # Supabase client
│   ├── claude.ts               # Claude message parser
│   └── todos.ts                # CRUD functions
├── .env.local
└── ...
```

---

## WhatsApp Webhook (`/api/whatsapp`)

- Accept POST from Twilio
- **Security**: verify the request comes from Twilio (use `twilio` npm package `validateRequest`) AND that the sender matches `MY_WHATSAPP_NUMBER`
- Extract the `Body` field (user's message)
- Pass to Claude for intent parsing (see below)
- Execute the resulting action against Supabase
- Reply via Twilio TwiML

---

## Claude Intent Parser (`lib/claude.ts`)

Send the user's raw WhatsApp message to Claude API with a system prompt like:

```
You are a to-do assistant. Parse the user's message and return ONLY valid JSON with this shape:
{
  "intent": "add" | "list_today" | "list_week" | "done" | "prioritize" | "set_deadline" | "delete" | "unknown",
  "todo": {
    "title": string | null,
    "notes": string | null,
    "priority": "high" | "medium" | "low" | null,
    "deadline": "YYYY-MM-DD" | null,
    "search_term": string | null   // for done/prioritize/delete actions
  }
}

Today's date is {TODAY}.
Never return anything other than the JSON object.
```

Handle the `unknown` intent gracefully — reply asking the user to rephrase.

---

## Intent Handlers

| Intent | Action | WhatsApp Reply |
|---|---|---|
| `add` | Insert to-do | "✅ Added: *{title}*" |
| `list_today` | Query todos due today or with no deadline, not done | Formatted list |
| `list_week` | Query todos due within 7 days, not done | Formatted list |
| `done` | Find by search_term, mark done | "✔️ Done: *{title}*" |
| `prioritize` | Update priority | "🔺 *{title}* is now {priority} priority" |
| `set_deadline` | Update deadline | "📅 *{title}* due {deadline}" |
| `delete` | Delete todo | "🗑️ Deleted: *{title}*" |
| `unknown` | No-op | "Sorry, I didn't get that. Try: 'add call João Friday high priority'" |

For `list_today` and `list_week`, format the reply like:
```
📋 *Today's to-dos:*

1. Call João 🔴 high · due today
2. Review proposal 🟡 medium
3. Pay invoice 🟢 low · due Fri

Reply with "done 1" or "done call joão" to mark complete.
```

---

## Back-Office Web UI (`app/page.tsx`)

A clean, functional dashboard. Design it with a refined, minimal aesthetic — think a well-designed productivity tool, not a generic CRUD app. Use:
- A distinctive serif or editorial font for the header
- Neutral, sophisticated color palette (not purple gradients)
- Cards or a clean table for to-dos
- Visual priority indicators (color dots or tags)
- Deadline display with overdue highlighting

### Features:
- List all open to-dos (sorted by priority then deadline)
- Filter by: All / Today / This Week / Done
- Click to mark as done
- Edit priority and deadline inline
- "Add to-do" form (for adding directly from the web UI, bypassing WhatsApp)
- Show done/completion date for completed items

The UI should fetch from `/api/todos` (a simple GET route) and post to `/api/todos` for adds/updates. Build these API routes too.

---

## Example WhatsApp Interactions

```
User: add call João on Friday high priority
Bot:  ✅ Added: Call João  🔴 high · due Fri 28 Mar

User: what's on today
Bot:  📋 Today's to-dos:
      1. Call João 🔴 high · due today
      2. Review proposal 🟡 medium
      Reply "done [number or name]" to mark complete.

User: done 1
Bot:  ✔️ Done: Call João

User: set deadline review proposal next monday
Bot:  📅 Review proposal due Mon 31 Mar

User: this week
Bot:  📋 This week:
      1. Review proposal 🟡 medium · due Mon
      2. Pay invoice 🟢 low · due Wed
```

---

## Implementation Notes

1. Use the `twilio` npm package for both validation and sending TwiML replies
2. Use `@supabase/supabase-js` for DB access
3. Use `@anthropic-ai/sdk` for Claude calls
4. Dates: always work in `Europe/Lisbon` timezone
5. The webhook must respond within 5 seconds (Twilio timeout) — Claude API calls are fast enough, but add a timeout fallback
6. For fuzzy matching on `done`/`prioritize`/`delete`, pass the `search_term` from Claude directly into a Postgres `ilike '%term%'` query — pick the first match
7. Deploy to Vercel: `vercel --prod`. Set all env vars in Vercel dashboard.

---

## Getting Started Sequence

1. `npx create-next-app@latest todo-whatsapp --typescript --tailwind --app`
2. Install deps: `npm i twilio @supabase/supabase-js @anthropic-ai/sdk`
3. Create Supabase project → run the schema SQL above
4. Set up `.env.local` with all vars
5. Build webhook + Claude parser + CRUD layer
6. Build the back-office UI
7. Deploy to Vercel
8. Point Twilio sandbox webhook to `https://your-app.vercel.app/api/whatsapp`
9. Send "join [your-sandbox-code]" from your WhatsApp to the Twilio sandbox number
10. Test end to end
