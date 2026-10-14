# Custom Chat UI Implementation Plan

## Goal

Build a custom chat client for Toston's current chat backend without depending on
`@ai-sdk/react`. The client must:

- authenticate with the existing bearer token;
- create, load, list, and delete conversations;
- submit only the newest user UI message to the streaming endpoint;
- parse the Vercel AI UI Message Stream Protocol v6 over SSE;
- render text, generic working state, sources, and Toston data cards;
- handle clarification questions and editable mutation confirmations;
- recover cleanly from HTTP errors, in-stream errors, cancellation, and unknown
  future events.

This document describes the contract implemented in this repository, not the
OpenAI Chat Completions streaming API.

## Source of truth and pinned protocol

The relevant implementation is:

- `backend/app/app/api/api_v1/endpoints/chat.py`
- `backend/app/app/ai/agent.py`
- `backend/app/app/schemas/chat.py`
- `frontend/chat/src/api/chat.ts`
- `frontend/chat/src/hooks/useChat.ts`
- `frontend/chat/src/components/MessageBubble.tsx`
- `frontend/chat/src/components/render/RenderDirectives.tsx`

The backend lockfile currently resolves `pydantic-ai-slim` to `2.13.0`. The
existing frontend resolves `ai` to `6.0.218` and declares `@ai-sdk/react`
`^3.0.220`. The endpoint constructs `VercelAIAdapter` with `sdk_version=6`.

Treat the event names and payloads in this document as a versioned application
contract. Add contract tests before upgrading Pydantic AI or the Vercel AI SDK.

## Architecture

```text
Custom UI
  ├── REST client
  │     ├── list/load/delete conversations
  │     └── confirm a pending mutation
  ├── stream transport
  │     ├── POST only the newest user UI message
  │     ├── distinguish HTTP JSON errors from SSE
  │     └── parse `data: ...\n\n` frames
  ├── event reducer
  │     ├── correlate text/reasoning blocks by `id`
  │     ├── correlate tool events by `toolCallId`
  │     └── append `data-*`, source, and file parts
  └── render registry
        ├── generic protocol parts
        └── Toston-specific `data-*` components

FastAPI `/api/v1/chat`
  ├── public stream projection
  │     └── text, data cards, sources, files, and lifecycle events
  └── private Pydantic AI history
        └── reasoning, tool calls/results, and OpenRouter context
```

## Authentication and base URL

All chat routes require the same bearer token used elsewhere by the frontend:

```http
Authorization: Bearer <access_token>
```

The current frontend stores it under `access_token` and prepends
`VITE_API_BASE_URL`. The chat prefix is:

```text
/api/v1/chat
```

The browser client may also send `credentials: include`, but bearer
authentication is the required mechanism in the current implementation.

## HTTP API contract

### 1. Send a message and stream the assistant response

```http
POST /api/v1/chat
Authorization: Bearer <token>
Content-Type: application/json
Accept: text/event-stream
```

Request:

```json
{
  "id": "client-owned-conversation-id",
  "trigger": "submit-message",
  "messages": [
    {
      "id": "user-message-id",
      "role": "user",
      "parts": [
        {
          "type": "text",
          "text": "Show my accounts"
        }
      ]
    }
  ]
}
```

Rules:

- Generate and retain the conversation ID on the client before the first send.
  The backend uses `id` as `conversation_id`; if it is absent, the backend
  generates one, but the raw stream does not return that conversation ID as a
  separate application field.
- Generate stable message IDs on the client.
- Send only the newest user `UIMessage`. Conversation context is server-owned;
  older clients that send complete history are tolerated, but the backend uses
  only the newest user turn.
- Raw reasoning, tool inputs, and tool outputs are private harness data. They
  are never part of the public SSE contract or persisted `ui_messages`.
- Use `trigger: "submit-message"` for a normal send. The protocol also defines
  regeneration triggers, but this backend/UI does not currently expose a
  regeneration workflow.
- Serialize stored assistant messages with their structured `parts`; do not
  flatten tool or data parts back into plain text.
- Allow only one active send per conversation unless the UI deliberately adds
  queuing.

Successful response:

```http
Content-Type: text/event-stream
x-vercel-ai-ui-message-stream: v1
```

Each SSE frame contains one camelCase JSON chunk:

```text
data: {"type":"text-delta","id":"0","delta":"Hello"}

```

The terminal frame is:

```text
data: [DONE]

```

Do not use `EventSource`; message submission is a `POST` with a JSON body and
authorization header. Use `fetch()` and read `response.body`.

### 2. List conversations

```http
GET /api/v1/chat/conversations
Authorization: Bearer <token>
```

Response:

```ts
type ConversationSummary = {
  id: string
  title: string
  updated_at: string
  last_message_preview?: string | null
}
```

### 3. Load one conversation

```http
GET /api/v1/chat/conversations/{conversationId}
Authorization: Bearer <token>
```

Response:

```ts
type ConversationDetail = {
  id: string
  title: string
  updated_at: string
  messages: Array<{
    role: string
    content: string
    created_at?: string | null
  }>
  ui_messages?: UIMessage[] | null
}
```

Use `ui_messages` to restore the custom UI. It preserves public structured text,
data, source, and file parts. Tool and reasoning history remains in private
server storage. `messages` is the flattened compatibility representation for
legacy conversations. If `ui_messages` is absent, the current frontend displays
an empty history; a new custom client should instead convert each legacy
`messages` entry into a text-only `UIMessage` so old conversations remain
visible.

### 4. Delete a conversation

```http
DELETE /api/v1/chat/conversations/{conversationId}
Authorization: Bearer <token>
```

Response:

```json
{"ok": true}
```

### 5. Confirm a pending mutation

This is a normal JSON request, not a stream:

```http
POST /api/v1/chat/conversations/{conversationId}/confirm
Authorization: Bearer <token>
Content-Type: application/json
```

Request:

```ts
type ChatConfirmRequest = {
  action_id: string
  tool_args?: Record<string, unknown>
}
```

Omit `tool_args` to accept the pending payload unchanged. Send it to apply
editable values from a confirmation form. The server merges allowed edits with
the stashed tool arguments, executes the mutation, deletes the pending action,
and returns:

```ts
type ChatMessageResponse = {
  conversation_id: string
  reply: string
  actions: Array<{
    type: string
    detail?: Record<string, unknown> | null
  }>
  render: Array<{
    component: string
    detail?: Record<string, unknown> | null
  }>
  pending_confirmation?: PendingConfirmation | null
  pending_question?: PendingQuestion | null
  ui_message?: UIMessage | null
}
```

Append `ui_message` to the visible history after success. Refresh the
conversation list because confirmation updates persisted history and its
timestamp.

Canceling a confirmation is client-side only in the current API. Hide/dismiss
the card locally. There is no reject endpoint, so the Redis action remains until
it expires.

## UI message model

The event reducer should produce a transport-neutral form:

```ts
type UIMessage = {
  id: string
  role: "user" | "assistant" | "system"
  metadata?: unknown
  parts: UIMessagePart[]
}
```

At minimum, support these accumulated part shapes:

```ts
type UIMessagePart =
  | { type: "text"; text: string; state?: "streaming" | "done" }
  | { type: "reasoning"; text: string; state?: "streaming" | "done" }
  | {
      type: `tool-${string}`
      toolCallId: string
      toolName: string
      state:
        | "input-streaming"
        | "input-available"
        | "output-available"
        | "output-error"
        | "output-denied"
      input?: unknown
      output?: unknown
      errorText?: string
      approval?: { id: string; approved?: boolean }
    }
  | { type: "source-url"; sourceId: string; url: string; title?: string }
  | {
      type: "source-document"
      sourceId: string
      mediaType: string
      title: string
      filename?: string
    }
  | { type: "file"; url: string; mediaType: string }
  | { type: `data-${string}`; id?: string; data: unknown }
  | { type: "step-start" }
```

Exact accumulated part details can evolve independently from the wire events.
Keep the wire parser and UI model reducer in separate modules.

## Complete stream event reference

JSON keys are camelCase on the wire even though the Python classes use
snake_case internally.

### Message and step lifecycle

| Event | Payload | Current reachability | Client behavior |
|---|---|---:|---|
| `start` | `messageId?`, `messageMetadata?` | Always | Create/select the assistant message. If `messageId` is absent, use a client temporary ID and reconcile if one later appears. |
| `start-step` | no additional fields | Always, once per model response/round | Append a step boundary; show working state. |
| `finish-step` | no additional fields | Always, at each round boundary and final round | Mark the current step complete. Do not mark the whole message complete yet. |
| `message-metadata` | `messageMetadata` | Normal successful completion | Merge metadata into the assistant message. |
| `finish` | `finishReason?`, `messageMetadata?` | Always if the stream completes | Mark generation complete and retain the finish reason. |
| `[DONE]` | non-JSON sentinel | Always if the stream completes | Close the parser and finalize any remaining streaming parts. |
| `abort` | `reason?` | Defined by the protocol, not emitted by the current adapter path | Mark aborted without treating partial content as a successful completion. |

The adapter also has an internal `done` chunk class, but it is encoded on the
wire as the non-JSON `[DONE]` sentinel rather than
`{"type":"done"}`. A client should never wait for a JSON `done` event.

`finishReason` is one of:

```text
stop | length | content-filter | tool-calls | error | other
```

The normal multi-round order is:

```text
start
start-step
  ...reasoning/text/tool events for round 1...
finish-step
start-step
  ...reasoning/text/tool events for round 2...
message-metadata
finish-step
finish
[DONE]
```

### Text

| Event | Payload | Current reachability | Client behavior |
|---|---|---:|---|
| `text-start` | `id`, `providerMetadata?` | Common | Create a streaming text part keyed by `id`. |
| `text-delta` | `id`, `delta`, `providerMetadata?` | Common | Append `delta` to the matching text part. Never replace prior text. |
| `text-end` | `id`, `providerMetadata?` | Common | Mark that text part done. |

One assistant message may contain multiple text blocks. Correlate by `id`;
do not assume a single global text buffer.

### Reasoning (server-private)

| Event | Payload | Harness behavior | Client behavior |
|---|---|---|---|
| `reasoning-start/delta/end` | reasoning ID, text, and provider metadata | Retained only in private model history | Filtered before SSE serialization. |

Reasoning is not part of the public chat contract and is never persisted in
`ui_messages`.

### Tool input and output (server-private)

| Event | Payload | Harness behavior | Client behavior |
|---|---|---:|---|
| `tool-input-*` | tool name, call ID, and arguments | Used by the agent harness and observability | Filtered before SSE serialization. |
| `tool-output-*` | call ID, result, or error | Used by the agent harness and observability | Filtered before SSE serialization. |
| `tool-approval-request` | approval and call IDs | Not used by the current confirmation design | Filtered before SSE serialization. |

The Toston agent intentionally does **not** use the v6
`tool-approval-request` flow because edited arguments cannot be returned through
that flow. Mutations instead return a normal successful tool output plus a
`data-pending-confirmation` event, then wait for the separate `/confirm`
endpoint.

User-facing results from tools travel through purpose-built `data-*` chunks,
sources, assistant text, and `data-pending-confirmation` or
`data-pending-question`. Raw tool lifecycle parts never enter the accumulated
public UI model.

### Sources and files

| Event | Payload | Current reachability | Client behavior |
|---|---|---:|---|
| `source-url` | `sourceId`, `url`, `title?`, `providerMetadata?` | Emitted by `search_web` for each result with a URL | Render a safe external link; deduplicate by `sourceId` or URL. |
| `source-document` | `sourceId`, `mediaType`, `title`, `filename?`, `providerMetadata?` | Protocol-supported, no current agent emitter | Store and render generically if added later. |
| `file` | `url`, `mediaType` | Protocol-supported when the model returns a file; no current agent tool emits one | Render by media type and treat URLs as untrusted. |

`search_web` also emits a richer `data-web-sources` component. A UI may render
that card and use `source-url` for citations/deduplication.

### Errors

| Event | Payload | Current reachability | Client behavior |
|---|---|---:|---|
| `error` | `errorText` | Any failure after SSE headers begin | Show an in-stream error, set the request to failed, and retain partial content. |

An `error` chunk is different from a non-2xx HTTP response. The server can still
emit `finish` with `finishReason: "error"` and `[DONE]` after an in-stream
failure. The UI should remain in an error state even if terminal frames arrive.

If the user cancels `fetch()` or the network closes before a terminal event,
there may be no `abort`, `error`, `finish`, or `[DONE]` chunk. Handle local
`AbortError` and unexpected EOF separately.

### Dynamic application data

Every dynamic application event has this wire shape:

```ts
type DataChunk = {
  type: `data-${string}`
  id?: string
  data: unknown
  transient?: boolean
}
```

Current Toston events:

| Event | `data` payload | Purpose |
|---|---|---|
| `data-accounts-list` | `{items: Account[]}` | Account names, types, and current balances. |
| `data-categories-list` | `{items: Category[]}` | Categories with nested subcategories. |
| `data-places-list` | `{items: Place[]}` | Physical/online places. |
| `data-transactions-list` | `{items: Transaction[], total: number, truncated: boolean}` | Search results. |
| `data-balance-adjustments-list` | `{items: BalanceAdjustment[]}` | Balance adjustment history. |
| `data-period-summary` | `SummaryResponse` | Income, expenses, net, and balances for a period. |
| `data-charts` | `ChartsResponse` | Net, income/expense, and category chart series. |
| `data-web-sources` | `{items: WebSource[], answer?: unknown}` | Rich web search result card. |
| `data-data_table` | `{title?: string, columns: string[], rows: (string \| number)[][]}` | Model-generated comparison table. |
| `data-stat_cards` | `{items: {label: string, value: string \| number, sub?: string}[]}` | Model-generated key metrics. |
| `data-pending-confirmation` | `PendingConfirmation` | Editable approval gate for a mutation. |
| `data-pending-question` | `PendingQuestion` | Clarifying question and answer choices. |

Core entity shapes used by the current renderers:

```ts
type Account = {
  id: number
  name: string
  type?: string | null
  current_balance: number
}

type Category = {
  id: number
  name: string
  is_income: boolean
  subcategories: Array<{
    id: number
    name: string
    category_id: number
  }>
}

type Place = {
  id: number
  name: string
  is_online: boolean
}

type SummaryResponse = {
  country: string
  balance: {
    total: number
    income: number
    outcome: number
  }
  period_income: number
  period_expenses: number
  period_net: number
}

type ChartsResponse = {
  net: unknown
  income_vs_expense: unknown
  categories: unknown | null
}

type PendingConfirmation = {
  action_id: string
  tool_name: string
  preview: Record<string, unknown>
  message: string
}

type PendingQuestion = {
  question: string
  options: Array<{id: string; label: string}>
  allow_multiple: boolean
}
```

Transaction, adjustment, and chart payloads are serialized from backend domain
schemas and may gain fields. Validate only the fields required by a renderer and
preserve unknown fields.

`render_data` constructs its type dynamically as `data-${component}`. The
backend currently constrains model-selected component IDs, but the client must
still include a generic unknown-data renderer, such as a collapsed JSON panel.
Never crash the whole message because one data card is unknown or malformed.

If `transient: true` appears in a future chunk, use it for live UI state but do
not persist it in conversation history. Current Toston `DataChunk` emitters do
not set `transient`.

## Reducer design

Implement a pure reducer:

```ts
reduceChunk(state: ChatStreamState, chunk: UIMessageChunk): ChatStreamState
```

Recommended indexes:

```ts
type ChatStreamState = {
  assistantMessage: UIMessage
  textPartById: Map<string, number>
  reasoningPartById: Map<string, number>
  toolPartByCallId: Map<string, number>
  rawToolInputByCallId: Map<string, string>
  currentStep: number
  finishReason?: string
  status: "submitted" | "streaming" | "ready" | "error" | "aborted"
  error?: string
}
```

Reducer invariants:

1. Events may arrive in several model rounds inside one assistant message.
2. Text/reasoning IDs and tool call IDs are correlation keys; array position is
   not.
3. Duplicate terminal events should be harmless.
4. Unknown events and unknown fields must be ignored or logged, not fatal.
5. A delta without a known start should create a recoverable placeholder part.
6. `tool-input-available` is authoritative over accumulated raw argument text.
7. `error` is sticky; later `finish`/`[DONE]` must not change it to success.
8. Unexpected EOF leaves partial content visible and marks the request
   interrupted.
9. Data payloads are untrusted JSON. Validate per renderer.
10. Do not render model text, tool input/output, or data payloads as raw HTML.

## SSE parser design

The parser should:

1. Call `fetch()` with an `AbortController`.
2. Check `response.ok` before reading the body.
3. If non-2xx, parse the normal JSON error body.
4. Verify that a body exists; optionally verify the stream response header.
5. Decode bytes with a streaming `TextDecoder`.
6. Buffer across arbitrary network chunk boundaries.
7. Split complete SSE events on a blank line (`\n\n`, accepting `\r\n`).
8. Combine multiple `data:` lines if present.
9. Stop on `[DONE]`.
10. Parse each other `data:` value as JSON and dispatch by `type`.
11. Ignore SSE comments and unknown fields.
12. Flush any final buffered frame on EOF, then classify missing `[DONE]` as an
    interrupted stream.

Do not assume one `ReadableStream` chunk equals one SSE event.

## HTTP and transport error matrix

| Status/condition | Meaning | UI behavior |
|---|---|---|
| `400` from `/confirm` | Action belongs to a different conversation | Keep card visible and show the server detail. |
| `401` | Missing/expired authentication | Clear local auth and return to login. |
| `402` | Token limit reached (`reason: "token_limit_reached"`) | Parse `detail`, show upgrade UI, do not start an assistant stream. |
| `404` conversation | Missing/expired conversation | Remove stale sidebar item or return to a new chat. |
| `404` pending action | Action missing or expired | Dismiss the approval card and explain that it must be requested again. |
| `422` | Invalid request shape | Show a client/version error and log the validation detail. |
| `503` string detail | Chat disabled | Disable message submission. |
| `503` `{reason:"quota_unavailable"}` | Quota reservation could not be safely recorded | Offer retry; do not treat it as quota exhaustion. |
| SSE `error` | Failure after stream start | Preserve partial response and mark failed. |
| Local abort | User pressed Stop | Preserve partial response and mark aborted. |
| Unexpected EOF | Network/server interruption | Preserve partial response and offer retry. |

Quota response shape:

```ts
type QuotaError = {
  detail: {
    reason: string
    usage: { period_start: string; period_end: string | null; percent_used: number | null }
    tier: string
    upgrade_url: string
  }
}
```

Quota and chat-enabled checks occur before streaming, so these errors are JSON
HTTP responses rather than SSE events.

## Rendering plan

Create a registry instead of a large component switch:

```ts
const dataRenderers: Record<
  string,
  (data: unknown) => RenderResult
> = {
  "data-accounts-list": renderAccounts,
  "data-categories-list": renderCategories,
  "data-places-list": renderPlaces,
  "data-transactions-list": renderTransactions,
  "data-balance-adjustments-list": renderBalanceAdjustments,
  "data-period-summary": renderSummary,
  "data-charts": renderCharts,
  "data-web-sources": renderSources,
  "data-data_table": renderDataTable,
  "data-stat_cards": renderStatCards
}
```

Render `data-pending-confirmation` and `data-pending-question` in an interaction
dock rather than inline:

- use the newest unresolved pending confirmation;
- while it is active, disable normal message submission;
- approve through `/confirm`;
- cancel locally;
- show a transaction edit form only when the preview schema is recognized;
- otherwise show the server message and an optional raw-payload disclosure;
- for a single-choice question, send the chosen **label** as the next user
  message;
- for multiple choice, join selected labels with `", "` and send that text.

The client shows generic working state from the request/stream lifecycle.
Pending confirmation comes from its purpose-built `data-*` event.

## Conversation state and persistence

- Generate a conversation ID before the first send and keep it as the active
  route/store key.
- Optimistically append the user's message before opening the stream.
- Append one assistant message for the stream and mutate its parts through the
  reducer.
- Refresh the sidebar after a successful stream or `/confirm`.
- On load, prefer `ui_messages`; adapt legacy `messages` to text parts.
- Track dismissed `action_id` values so persisted pending-confirmation parts do
  not reopen after local cancellation or successful confirmation.
- Clear dismissed IDs when switching conversations.
- Do not assume that a locally stopped stream was persisted. The backend marks
  interrupted turns as errors and successful conversation persistence is
  best-effort.
- Conversation storage has a server-configured TTL, so a sidebar entry or
  pending action can expire.

## Delivery phases

### Phase 1 — Freeze the contract

- Add shared TypeScript wire types for every event in this document.
- Add runtime schemas for the request, HTTP errors, pending interactions, and
  each renderer's required fields.
- Store the tested dependency versions next to the protocol types.
- Add captured SSE fixtures for a text-only turn, multi-step tool turn, data
  turn, confirmation turn, question turn, source turn, and in-stream error.

Exit criteria: fixtures parse into deterministic `UIMessage` snapshots.

### Phase 2 — Transport and reducer

- Implement the POST-based SSE parser.
- Implement the pure event reducer and correlation indexes.
- Add cancellation with `AbortController`.
- Separate pre-stream HTTP errors from in-stream errors.
- Expose transport status: `submitted`, `streaming`, `ready`, `error`,
  `aborted`.

Exit criteria: the parser survives arbitrarily split byte chunks, unknown
events, duplicate terminal events, and missing `[DONE]`.

### Phase 3 — Core chat UI

- Build the conversation store and active-conversation routing.
- Implement composer, send, stop, retry, message list, markdown-safe text, and
  generic working state.
- Implement list/load/delete conversation calls.
- Convert legacy conversation messages to text-only UI messages.

Exit criteria: a user can create, reload, revisit, and delete a conversation
without receiving private harness events.

### Phase 4 — Rich application parts

- Implement the data-renderer registry and generic fallback.
- Add accounts, categories, places, transactions, adjustments, summary, charts,
  sources, data table, and stat cards.
- Add source URL deduplication and safe external-link handling.

Exit criteria: every currently emitted Toston `data-*` event renders without
breaking the surrounding message.

### Phase 5 — Interactive gates

- Implement the pending-question dock.
- Implement pending-confirmation preview, approve, local cancel, and editable
  transaction arguments.
- Append `/confirm`'s `ui_message` and refresh the sidebar.
- Track dismissed action IDs per conversation.

Exit criteria: mutations never execute before explicit approval, edited
arguments reach `/confirm`, and expired/cross-conversation actions fail safely.

### Phase 6 — Hardening and observability

- Add explicit handling for 401, 402, 404, 422, both 503 variants, malformed
  SSE, in-stream error, local abort, and unexpected EOF.
- Record request ID/trace headers when available, event type counts, first-token
  latency, total duration, finish reason, and interruption reason without
  logging sensitive message/tool content.
- Add accessibility for streaming announcements, keyboard submission, focus
  movement to approval/question docks, and reduced motion.
- Add large-history and slow-network tests.

Exit criteria: all error-matrix cases have deterministic UI and automated
coverage.

## Required automated tests

### Parser

- multiple events in one byte chunk;
- one event split across many byte chunks;
- CRLF and LF frame endings;
- `[DONE]` split across chunks;
- unknown event type;
- malformed JSON frame;
- EOF with and without terminal frame.

### Reducer

- multiple text blocks correlated by ID;
- private reasoning chunks are filtered;
- private tool lifecycle chunks are filtered while accompanying `data-*` parts
  remain;
- multiple model steps in one message;
- `data-*` before and after text;
- source deduplication;
- sticky error followed by `finish`;
- repeated terminal events.

### API flows

- new client-generated conversation ID;
- persisted history reload from `ui_messages`;
- legacy `messages` fallback;
- quota rejection before stream;
- chat disabled before stream;
- confirm unchanged arguments;
- confirm edited arguments;
- cancel locally;
- expired pending action;
- action/conversation mismatch;
- user stop and network interruption.

## Compatibility rules

- Ignore unknown JSON fields.
- Preserve unknown message parts when loading/saving client state.
- Render unknown `data-*` parts with a safe generic fallback.
- Do not interpret a new event as text merely because it has a `delta` field.
- Feature-detect optional fields such as `providerMetadata`, `dynamic`,
  `preliminary`, and `transient`.
- Keep Toston's confirmation flow separate from protocol-native
  `tool-approval-request`.
- Re-run fixture and integration tests whenever `pydantic-ai-slim`, `ai`, or
  `@ai-sdk/react` changes.

## Definition of done

The custom UI is complete when it can:

1. authenticate and maintain a client-owned conversation ID;
2. send full structured history and parse the POST SSE response;
3. handle every currently reachable event in this document;
4. safely ignore/render every protocol-supported fallback event;
5. render every current Toston `data-*` payload;
6. answer clarification questions;
7. approve, edit, or locally cancel pending mutations;
8. reload structured history and adapt legacy history;
9. distinguish quota/auth/service HTTP failures from in-stream failures;
10. stop a stream without losing the usable partial response; and
11. pass the parser, reducer, API-flow, and dependency-upgrade contract tests.
