# Code Review: `wassengerhq/whatsapp-chatgpt-bot` (master, pushed 2025-11-18)

**Scope:** `main.js` (155), `server.js` (146), `bot.js` (444), `actions.js` (353), `config.js` (266), `functions.js` (113), `store.js` (12). Node.js + Express + Wassenger BSP API + OpenAI. ~1,489 LOC, **zero tests**, no TypeScript, no CI.

Cloned to `/tmp/wassenger-review` for the review.

---

## What was done well

| Area | Why it's good |
|---|---|
| **Async webhook ack** (`server.js:49-54`) | Returns 200 immediately, then `bot.processMessage(body).catch(...)`. Meta/Wassenger won't retry on slow OpenAI calls. **This is the single most important production pattern** — your hotel-bot lacks it. |
| **Tool-call loop with safety ceiling** (`bot.js:379-434`) | `maxCalls = 10` breaks runaway tool-calling loops. `if (response?.finish_reason === 'stop') break`. Solid loop hygiene. |
| **`canReply` predicate** (`bot.js:12-70`) | All skip conditions (groups, banned, blacklist, archived, self, already-assigned-to-human) in one function with single-responsibility. Easy to read, easy to add rules. |
| **Multi-message-type graceful degradation** (`bot.js:246-268`) | Audio → transcribe; video/document → polite refusal; location/poll/event/contacts → text representation. No crashes on unexpected payloads. |
| **`user` param on OpenAI calls** (`bot.js:369`) | `user: '${device.id}_${chat.id}'` — OpenAI uses this for abuse monitoring per end-user. Free tracing/billing dimension. Your bot doesn't pass it. |
| **Input length clamp** (`bot.js:271`) | `body.slice(0, Math.min(maxInputCharacters, 10000))` — defensive even if config is mis-set. |
| **`parseArguments` helper** (`bot.js:153-159`) | Wraps `JSON.parse` for tool call args. Won't crash on malformed model output. |
| **Self-loop prevention** (`bot.js:21-24`) | Skips if `fromNumber === device.phone`. Catches the classic "bot replies to its own outbound message" disaster. |
| **Typing indicator** (`bot.js:103`, `actions.js:340`) | Sends `typing`/`recording` chat state while processing. Real UX touch. |
| **Per-chat-quota auto-handoff** (`bot.js:184-205`) | After N messages, assigns to a human + sets metadata flag. Cost ceiling + bot-doom-loop killswitch. |
| **In-memory TTL cache for members/labels** (`actions.js:42-49, 93-101`) | 10-minute TTL on `pullMembers`/`pullLabels`. Cheap optimization that saves Wassenger API calls. |
| **README quality** | 28KB README — by far the best part of the project. Detailed config explanations, deployment options, function-calling examples. |

---

## Production defects (serious)

### 1. **NO webhook signature verification anywhere**
**`server.js:39-55`** — accepts any POST to `/webhook`. Anyone who finds the public URL (and can guess `event: 'message:in:new'`) can inject fake inbound messages, trigger OpenAI calls on the operator's dime, and make the bot reply to attacker-controlled phone numbers.

Wassenger doesn't HMAC-sign webhooks the way Meta does, but this repo ships **zero IP allowlisting, zero shared-secret header check, zero auth**. Compare: your hotel-bot does proper HMAC-SHA256 with `compare_digest`. **Your bot's security is better than this 157-star reference.**

**Fix for them:** at minimum, require a shared-secret header (`X-Webhook-Token`) and reject all other requests. Better: Wassenger source IP allowlist.

### 2. **`/sample` and `/message` endpoints are unauthenticated**
**`server.js:58-92`** — public POST endpoints that **send WhatsApp messages on the operator's behalf** with no auth. Anyone who finds the deployed URL can spam any phone number, fully attributed to the operator's WhatsApp Business number. This is a **direct path to account-ban + abuse liability**.

```js
app.post('/message', (req, res) => {           // ← no auth
  actions.sendMessage(body).then((data) => ...
```

**Fix:** require an API key header, or remove from production builds.

### 3. **No outbound HTTP timeouts (same defect as your bot)**
All `axios.post/get/patch/delete` calls in `actions.js` use defaults. Axios's default is **no timeout**. A hung Wassenger API call = hung worker forever. Same bug class as your `improvements.md` defect #3.

### 4. **No message-ID dedup**
Server acks 200 immediately (great), but if Wassenger re-delivers (network blip, restart mid-process), the bot has no `processed_messages` table — it'll run the full OpenAI + reply cycle again. They acknowledge in `store.js:8`: *"You can use a database instead for persistence"* — but ship without one.

### 5. **In-memory state is wiped on restart**
**`store.js`** — `state = {}`, `cache = {}`, `stats = {}` are plain JS objects. `nodemon`/`pm2`/server restart = total amnesia. Quota counters reset, conversation history reset (until next backfill from Wassenger), tool state lost. **Your Supabase setup is genuinely better.**

### 6. **Race conditions on shared state**
Two concurrent webhooks for the same chat both read-modify-write `state[chatId]` and `stats[chatId]` without locks. Node single-threaded saves them from data corruption, but `async` boundaries inside `processMessage` (the `await ai.chat.completions.create(...)`) absolutely interleave. Quota counter can under-increment; history can interleave out of order.

### 7. **`updateChatLabels` is a confirmed bug**
**`actions.js:106-110`:**
```js
for (const label of labels) {
  if (newLabels.includes(label)) {       // ← inverted!
    newLabels.push(label)
  }
}
```
This only pushes labels that are **already** in the array. First-time label application is a no-op. The label was never added on the initial call. Should be `if (!newLabels.includes(label))`.

### 8. **`validateMembers` argument mismatch**
**`actions.js:51`** declares `validateMembers (device, members)` but **`main.js:119`** calls `actions.validateMembers(members)`. The `device` param is `undefined`, `members` is unused inside the function (function reads `members` from closure on `config.teamWhitelist/teamBlacklist` but iterates over `members` arg that's actually `device` arg from caller). Silently passes because the `for` loop iterates over `members.concat(...)` which is `device.concat(...)`. **The validation doesn't validate.** Latent failure waiting for someone to actually configure a whitelist.

### 9. **`transcribeAudio` writes to CWD, not `tempPath`**
**`actions.js:319`** — `const tmpFile = '${message.media.id}.mp3'` — relative path means it lands in `process.cwd()`. Two concurrent audio messages with the same media.id race-write to the same file. Also breaks if the process is started from a different directory.

### 10. **`sendMessage` retry has no backoff**
**`actions.js:25-37`** — `while (retries) { retries -= 1; try { ... } catch { ... } }`. Three immediate retries with zero sleep between them = three failures in <100ms on a real outage. Pointless retries; missing exponential backoff.

---

## Should improve

| # | Location | Issue | Fix |
|---|---|---|---|
| 11 | `bot.js:339-355` | Last-message-already-in-history check is fragile (compares `content` strings) and the surrounding "build messages" block is one of the harder things to read in the codebase. | Always append `body` as user message; rely on Wassenger pull to handle dedup. |
| 12 | `bot.js:310-331` | `Object.values(...).sort.slice.reverse.map.filter.slice` chain — full re-sort of all messages on every inbound. | Maintain `state[chatId]` as an array (insertion-ordered) instead of a map keyed by waId. |
| 13 | `server.js:113` | `/files/:id` audio file endpoint has no auth — anyone who guesses a 15–18 hex ID can download. Files self-delete on first read but there's no signed URL or expiry. | Sign URLs with HMAC + short expiry. |
| 14 | Everything | **Zero tests.** No `*.test.js`, no `__tests__/`, no `package.json` test script. 1,489 lines of unverified JS. | At least `vitest` smoke tests for `canReply`, `parseArguments`, `hasChatMessagesQuota`. |
| 15 | `package.json:dependencies` | `ngrok`, `nodemon` are in `dependencies` (loaded in prod), not `devDependencies`. | Move them. |
| 16 | `config.js` | 266 lines of mixed concerns: secrets, behavior toggles, prompt text, tool definitions. | Split: `secrets.js` (env), `prompt.txt`, `tools.js` (functions), `flags.js` (toggles). |
| 17 | `main.js:153-155` | `main().catch(err => exit(...))` — top-level error swallow. Stack traces lost. | Log `err.stack` then `process.exit(1)`. |
| 18 | `bot.js:289` | `/^human|person|help|stop$/i.test(body) || /^human/i.test(body)` — second regex is redundant (first already matches `^human`). | Delete the second clause. |
| 19 | `bot.js:288-294` | Hardcoded English keywords for human handoff (`human|person|help|stop`). | i18n table; especially since they market multilingual. |
| 20 | `actions.js:78-79` | Color name list (`'tomato', 'orange', …`) inlined and re-allocated every label creation. | Constant. |
| 21 | `bot.js:281` | `if (data.type !== 'image' \|\| (data.type === 'image' && !config.features.imageInput) \|\| (data.type === 'image' && config.features.imageInput && data.media.size > config.limits.maxImageSize))` | Refactor to early-returns or a helper `isUnsupportedImage(data)`. |
| 22 | `bot.js:175-181` | `hasChatMessagesQuota` returns `true` after resetting the counter — meaning the message that triggers the reset is counted as "has quota." Probably intended, but the function name lies. | Rename to `checkAndResetQuota` or split. |
| 23 | `actions.js:51-62` | `exit()` is called inside `validateMembers` mid-startup. Kills process on any validation failure with no graceful unwinding. | Throw, catch in `main()`, decide what to do. |
| 24 | `functions.js` | Demo functions (`getPlanPrices`, `bookSalesMeeting`) have hardcoded English strings and SaaS-shaped data. Anyone who deploys "as-is" ends up with a bot advertising **Wassenger's own plans**. | Make the demo functions visibly placeholder. |
| 25 | Everywhere | No structured logging, no request IDs, no correlation IDs across the tool-call loop. All `console.log`. | Use `pino` or at least prefix log lines with `[chatId=xxx, msgId=yyy]`. |
| 26 | `package.json` | `"engine"` should be `"engines"`. **Typo silently ignores Node version constraint.** | Fix key. |

---

## Security checklist

1. **Webhook auth: absent.** Defect #1 above. Critical.
2. **`/sample`, `/message`: unauthenticated mutation endpoints.** Defect #2. Critical.
3. **`/files/:id`: unauthenticated download.** Item #13. Medium.
4. **Tool functions can return arbitrary strings** that get fed straight into the next LLM call and may be echoed to users. `loadUserInformation` returns CRM data; no PII redaction. → Indirect prompt-injection vector if CRM data is attacker-controlled.
5. **Logs include `data.body`** (raw user input) — `console.log('[info] New inbound message received:', chat.id, data.type, body)`. PII in plaintext logs.
6. **No rate limiting** on any endpoint. `express-rate-limit` missing.
7. **Secrets in `config.js`** with env-var fallback. Easier to leak than a strict env-only pattern.
8. **No CSRF/origin checks** on the management endpoints — `/sample` is a GET that mutates (sends a WhatsApp message). GETs should never mutate.

---

## Best practices missing (vs current Node/OpenAI/webhook conventions)

| Convention | Their state |
|---|---|
| **Webhook signature verification** | None. (Meta does it via `X-Hub-Signature-256`; Wassenger via shared secret. Neither implemented.) |
| **Idempotency keys** on inbound webhooks | None. |
| **Idempotency keys** on outbound (`Idempotency-Key` header on `axios.post`) | None — duplicate sends on retry will duplicate the user-visible message. |
| **Retry with exponential backoff + jitter** | Has fixed-attempt retry only, no delay. |
| **Circuit breaker** on OpenAI / Wassenger | None. |
| **Graceful shutdown** (drain in-flight `processMessage`s on SIGTERM) | None — `nodemon`/k8s rolling deploy will drop in-flight requests. |
| **Structured JSON logs** | All `console.log` text. |
| **TypeScript or JSDoc types** | Neither. |
| **CI / GitHub Actions** | No `.github/workflows`. |
| **Linter run in CI** | `standard` is installed but no CI enforces it. |
| **OpenAI SDK timeout + maxRetries** | Defaults (10 min / 2 retries). Not tuned. |
| **`AbortController` on long OpenAI calls** | None. |
| **Health endpoint** | Index `/` returns route map, not a real health probe. No `/healthz`. |
| **Container/Dockerfile** | None. |
| **`.env` discipline** (`dotenv` package) | Not used — relies on shell-set env vars. |

---

## Verdict (relative to your hotel-chat-bot)

| Dimension | wassenger | hotel-chat-bot |
|---|---|---|
| **Webhook security** | none | HMAC-SHA256 |
| **Persistence** | in-memory only | Supabase |
| **Async webhook ack** | yes | synchronous |
| **Function calling** | rich, with loop | keyword-based |
| **Audio/image input** | yes | no |
| **Tests** | zero | 31 tests |
| **Outbound timeouts** | none | none |
| **Message dedup** | none | none |
| **Human handoff** | built-in | none |
| **Vendor lock-in** | paid Wassenger | Meta direct |
| **LOC** | 1,489 (no tests) | ~200 + tests |
| **Confirmed bugs** | 2 (label add, validateMembers signature) | 0 |

**The repo's 157 stars are for the README and the feature surface, not the code quality.** Your ~200 LOC Python implementation is materially safer (HMAC, persistence, tests) but missing three of their best ideas: **async ack, function calling for booking intent, human-handoff fallback**. Those are the three things to copy, which is exactly what `proposalstoenhance.md` already recommends.

What this repo is actually good for: **a feature checklist and an OpenAI tool-calling loop reference**. Not a code template.
