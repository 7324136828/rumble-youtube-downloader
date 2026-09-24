# ClipFeed tools and activity in The Connector

ClipFeed can register video tools with The Connector and keep a system session
containing searches, playback observations, recent watched tags, and the
recommendations displayed in ClipFeed. This integration is off by default and
has its own switch, independent of **AI recommendations**.

The integration follows the API and video-rendering contract in
[`skill/the_connector/skill.md`](../skill/the_connector/skill.md).

## Setup

1. Start ClipFeed and The Connector. ClipFeed reaches The Connector through
   `RECOMMENDATION_CONNECTOR_URL`, which defaults to `http://127.0.0.1:8301`.
2. In ClipFeed, open **Recommendations**, expand **The Connector integration**,
   and enable **Connector tools and activity log**.
3. The backend registers the tools and creates the **ClipFeed activity** system
   session in the background. The card shows registered-tool count, session ID,
   queued activity count, and connection errors. Use **Connect now** or
   **Retry connection** to retry immediately.
4. Use The Connector's agent mode in a normal user chat to call the `clipfeed_*`
   tools. The activity system session is a log; it does not run the agent or a
   model when events arrive.

For recommendations, also choose a model and turn on **AI recommendations** in
ClipFeed. Search, library/history access, downloads, and activity logging do not
require that switch. Changing a recommendation hint does not enable it.

### Addresses and remote setups

There are two directions of communication:

| Address | Who uses it | Default |
| --- | --- | --- |
| `RECOMMENDATION_CONNECTOR_URL` | ClipFeed backend calls The Connector | `http://127.0.0.1:8301` |
| **Advanced connection settings → Downloader address** | The Connector calls ClipFeed tools; its browser loads thumbnails and streams | `http://127.0.0.1:8000` |

`CONNECTOR_PUBLIC_URL` sets the initial downloader address when the integration
record is first created. Its default uses `BACKEND_PORT`. Subsequent UI edits
persist in SQLite; changing the environment variable does not replace an
already-saved address. Changing this address causes tool callbacks to be
registered again.

Use an HTTP(S) root address with a real hostname or IP and optional port, without
credentials, a path, query, or fragment. A listening address such as `0.0.0.0`
is not a usable callback address. If either app or the browser runs on another
machine or in a separate container/WSL network, choose an address reachable from
both The Connector backend and that browser. Configure the binding, firewall,
and HTTPS as appropriate; an HTTPS browser page may block HTTP media.

## Available tools

Tool schemas can be inspected in ClipFeed at `GET /api/connector/tools` and, after
registration, in The Connector at `GET /api/agent/tools`.

| Tool | Behavior and main arguments |
| --- | --- |
| `clipfeed_search_videos` | Search enabled sites with `query`, `source`, `limit` (1–24), and optional `session_id`. Returns a task ID. Results include thumbnails and local playback when available. |
| `clipfeed_download_videos` | Queue 1–10 public video `urls`, with optional `quality` (`best`, `1080p`, `720p`, or `480p`). Uses saved download settings and reuses existing ready or active downloads. |
| `clipfeed_get_download_status` | Read progress and metadata for `video_ids` (up to 100); ready local files include playable streams. |
| `clipfeed_set_recommendation_hint` | Replace the saved `hint` (up to 2,000 characters). An empty string clears it. The same hint is editable in Recommendations. |
| `clipfeed_list_downloaded_videos` | Browse completed downloads with title, uploader, description, download date, keywords, thumbnails, and streams. Supports common list filters plus exact `keyword`. |
| `clipfeed_generate_recommendations` | Generate a playlist using request-specific `context`, `source`, `video_id`, `exclude_urls`, `limit`, and `refresh`. Requires enabled AI recommendations and returns a task ID. |
| `clipfeed_list_recommendation_history` | Browse persisted generated recommendations with common list filters and optional `fallback`. Includes `recommended_at`. |
| `clipfeed_list_watch_later` | Browse saved links with common list filters. Includes `saved_at`. |
| `clipfeed_add_watch_later` | Save up to 100 individual videos with optional titles/descriptions; existing entries update without duplication. Optional title lookup is on by default. |
| `clipfeed_list_watch_history` | Browse per-video watch totals with common list filters and optional `completed`. Includes watched seconds and the latest watch date. |
| `clipfeed_recent_tags` | Read up to `limit` (1–100) tags on videos watched in the past 30 days, ordered by recency. |
| `clipfeed_list_activity` | Browse logged events with `limit`, `offset`, optional `event_type` (`search`, `watch`, or `recommendation_impressions`), `date_from`, and `date_to`. |
| `clipfeed_get_task` | Poll a search or recommendation `task_id` until `completed` or `failed`. |

Request-specific recommendation filters do not replace saved preferences. The
download tool starts downloads directly; it is an action with network and disk
side effects, and does not open a ClipFeed confirmation dialog.

### Browse from the beginning

Library, recommendation-history, Watch later, and watch-history tools default to
`order: "oldest"`, `offset: 0`, and `limit: 50` (maximum 100). Common filters are
`source`, `query`, `uploader`, `date_from`, and `date_to`; `order: "newest"` reverses
the order. `query` matches case-insensitive text in title, description, or
uploader; watch history matches title and uploader because its retained record
does not include a description.

Each response includes `items`, `total`, `limit`, `offset`, and `next_offset`.
Repeat the same tool with the returned `next_offset` until it is `null` to read
the complete matching collection. Activity is also paginated and oldest-first.
This includes existing stored library/history records from before integration
was enabled. Deleted records cannot be reconstructed.

Date filters are inclusive ISO dates or timestamps. A date-only `date_to`
includes that entire UTC day; timestamps without a timezone use UTC. The date
means completion for downloads (creation as a fallback), generation for
recommendation history, save time for Watch later, and **latest watch** for
watch history. Watch history stores cumulative totals per video, not one row
for every viewing session. Recent-tags duration is also cumulative for the
recently watched video/tag pairs, rather than time spent only within 30 days.

For example, ask The Connector's agent to invoke:

```json
{
  "tool": "clipfeed_list_downloaded_videos",
  "arguments": {
    "source": "all",
    "query": "wildlife",
    "date_from": "2026-01-01",
    "order": "oldest",
    "limit": 50,
    "offset": 0
  }
}
```

That is also the request shape for The Connector's `POST /api/agent/step`.

### Search and recommendation tasks

The Connector's external webhook timeout is 10 seconds. Searches and model
recommendations therefore return promptly with `status: "pending"`, `task_id`,
`next_tool: "clipfeed_get_task"`, and `poll_after_seconds: 2`. Wait that interval
and poll the task. Completed tasks contain their payload under `result`;
failures contain an error. A pending task is not an empty search result.

Task results are held in the backend process for up to 30 minutes after
completion, with a maximum of 128 tasks and four concurrent operations. They
are lost on restart and older completed tasks can be evicted when the task
store fills. Retry the original operation if a task expired. Queued downloads,
catalog entries, and history use the database and are separate from this
temporary task store. Download progress uses `clipfeed_get_download_status`,
not `clipfeed_get_task`.

### Thumbnails and playing in chat

Results expose `thumbnail_url` when metadata is available. Search/catalog
results can also include `thumbnail_markdown`. A platform watch-page URL is an
external link, not a video stream.

When a ready downloaded file exists, tools return `stream_url`,
`playable: true`, and `video_markdown` containing fenced `video` JSON blocks.
The Connector's assistant should preserve those blocks in its answer so its
chat renderer can display a player. Until the download is ready, show the
source link/thumbnail and poll progress. Actual playback also depends on the
browser's codec support and its ability to reach the configured downloader
address. ClipFeed's normal playback/conversion settings still apply.

## Activity and memory

New activity is recorded only while integration is enabled:

- **Searches:** online queries and **Video topics** keyword/word-cloud searches,
  with their selected source and result count. Video topics uses source
  `library_keywords`. Loading additional pages or refreshing keyword enrichment
  within the same search session is deduplicated; opening all topics or clearing
  the keyword query does not create a search event.
- **Watching:** the video's metadata, keywords, playback position, seconds
  actually watched since the previous report, and cumulative watched seconds.
  Observations are batched around every minute; short visits are flushed after
  a minute. Completion is emitted immediately with `finished: true`,
  `completed: true`, and `status: "finished"`.
- **Recent watched tags:** included with watch observations and available
  through the recent-tags tool. Videos need stored keywords to contribute tags.
- **Recommendation impressions:** the cards rendered in ClipFeed's Feed,
  Watch, or History panel, including their metadata at that time. Hidden-page,
  stale, and disabled-panel results are not reported. Generated recommendation
  history remains separate and does not prove a person saw a suggestion.

Playback reporting comes from ClipFeed's players. The Connector's native inline
video player does not currently send ClipFeed watch-progress callbacks.
Likewise, a generated recommendation returned to an agent is not automatically
recorded as a ClipFeed UI impression. Activity before enablement is not
backfilled, although the list tools can read older stored records.

Events and pending watch batches are stored in the ClipFeed SQLite database.
A background worker sends events to the **ClipFeed activity** system session
and retries after failures. Delivery is **at least once**: if The Connector
saves an event but its acknowledgement is lost, a retry can appear twice in
the transcript. Each message carries a stable `event_id` so consumers can
identify duplicates. The pending count is visible in the integration card.

The session has `user_session: false`. Enable **Show System Sessions** in The
Connector's sidebar settings to inspect it. System-session `/api/chat` calls
receive the deterministic `message received` acknowledgement and do not invoke
a model.

To let a normal Connector conversation use these observations as memory, its
configuration must enable `past_memory`, allow
`memory_sources.system_sessions`, and use `memory_scope: "all_sessions"`
with a nonzero memory window. The activity session is created with
`past_memory: true`. Connector memory is bounded and can summarize or omit
older facts; use the list tools for complete paginated records. Later memory
summarization or an ordinary agent conversation can invoke the configured
model even though activity delivery itself does not.

## Local API, storage, and access

| ClipFeed endpoint | Purpose |
| --- | --- |
| `GET /api/connector/status` | Enablement, URLs, session ID, registered tools, pending count, and last sync/error; no callback token. |
| `PATCH /api/connector/settings` | Change `enabled` and/or the reachable `base_url`; synchronization runs in the background. |
| `POST /api/connector/connect` | Retry registration, session setup, and a delivery batch immediately. Returns 409 while disabled or 502 if The Connector is unavailable. |
| `GET /api/connector/tools` | Tool descriptions and argument schemas. |
| `POST /api/connector/impressions` | Receive one rendered recommendation batch with `event_id`, `context`, and `items`; ignores logging while disabled. |
| `POST /api/connector/hooks/{token}/{name}` | Callback registered with The Connector; accepts a plain arguments object, validates it, and dispatches one tool. |

The callback token is generated and persisted locally, sent to The Connector
inside registered endpoint URLs, and omitted from the UI and status responses.
Treat those callback URLs as credentials. The surrounding applications are
local, single-user services without a general multi-user authentication layer;
keep them on the same machine or a trusted private network. Protect the
database and Connector tool registry because they contain access information
and viewing activity.

Turning integration off stops new observations and delivery, disables callbacks,
and rotates the token. The Connector API has no unregister operation, so old
tool names can remain visible there, but their callbacks return 403. Turning
it on again registers fresh callbacks. Already accepted downloads or background
tasks are not canceled by this switch. Existing log messages and queued local
events are retained; pending events can resume delivery after re-enabling.

Bridge state, events, watch receipts/batches, and tag aggregates use
`connector_integration`, `connector_activity_events`,
`connector_watch_activity`, `connector_watch_receipts`, and
`connector_watched_tags` in `JOBS_DB_PATH`. The Connector keeps its own session
transcript in its database. Back up both databases if the log needs to survive
system temporary-directory cleanup.
