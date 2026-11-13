# Website

Static files served from S3 behind CloudFront. There is no build step: `scripts/publish_web.sh` fills in the config
block of `index.html`, uploads the folder and clears the CloudFront cache (CodeBuild does the same on every push to
`main` that changes `web/`).

## Traveler app

| File | What it does |
| --- | --- |
| `index.html` | The chat planner: sign-in, trip form, conversation, progress steps, itinerary cards, PDF downloads and the feedback panel (HTML, CSS and the main script) |
| `trip-form.js` | The structured trip form (destinations, dates, travelers, budget, cuisines, pacing) with input validation |
| `auth-session.js` | Cognito token refresh; a request is retried only after an explicit HTTP 401, never after a dropped connection |
| `background-status.js` | Polls for the result of a background plan, so locking the phone or losing the connection does not lose the plan |
| `chat-history.js` | The history drawer: lists past chats and reopens one |
| `data/cities.json` | City list with coordinates for the destination search in the form |

## Partner portal (`agency/`)

| File | What it does |
| --- | --- |
| `index.html`, `app.js` | Agencies and local guides register and manage their packages (draft, submit, withdraw) |
| `admin.html`, `admin.js` | Administrators approve providers and packages; only users in the admin Cognito group can open it |
| `package-fields.js` | Package form fields: cost categories and theme scores |
| `config.js` | API endpoint, terms URL and admin group, set at deployment |
| `terms.html`, `styles.css` | Partner terms and portal styles |

The website talks to two back-ends: the agent on AgentCore Runtime (planning and chat, streamed) and API Gateway
(`tp-api-trips` for trips, PDFs, feedback and history; `tp-ahmed-agency-api` for the partner portal). Tests for the
website are in `tests/test_*.js`.
