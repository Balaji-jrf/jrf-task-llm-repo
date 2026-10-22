# Qwen document-Q&A frontend

Standalone React/Vite application hosted by AWS Amplify. It reads the existing OCR
document history and previews through the existing OCR API, and sends indexing/chat
requests to the new Qwen backend over HTTPS. It is not connected to or imported by
the existing OCR React app or router.

## Amplify configuration

The repository is a monorepo with the React app in `frontend/`. The root
`amplify.yml` selects that app directory, runs `npm ci` and `npm run build`, and
publishes `frontend/dist`.

In Amplify, connect the GitHub repository and deployment branch, then configure:

| Variable | Value |
|---|---|
| `VITE_JRF_TASK_LLM_API_URL` | `https://api.<your-domain>/api` |
| `VITE_OCR_API_URL` | `https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod` |

The first value is the public HTTPS API origin. The frontend calls it directly from
the user's browser; API Gateway is not required in the first deployment. The second
value is the existing read-only OCR service. Only public URLs belong in Vite
environment variables; anything prefixed with `VITE_` is included in the browser
bundle.

Add the exact Amplify branch origin to the backend's
`JRF_TASK_LLM_ALLOWED_ORIGINS`. After attaching a custom frontend domain, add its
`https://` origin too, then restart the backend service. The EC2 security group must
also allow HTTPS from the tester public IPs. CORS alone does not protect an API.

## Frontend file map

| File | Purpose |
|---|---|
| `src/main.jsx` | Reads the two public Vite URLs and mounts the app |
| `src/llmApi.js` | Calls the Qwen backend and read-only OCR API |
| `src/components/LLMTab.jsx` | Document selection, indexing, chat, and source links |
| `src/components/HistoryOfLLMChat.jsx` | Lists and opens saved conversations |
| `src/components/LLMComparePane.jsx` | Displays a cited source page and highlight |
| `src/components/llm.css` | Workspace, chat, history, and document-view styling |
| `package.json` / `package-lock.json` | React, Vite, and PDF rendering dependencies |

See [`../docs/AWS-DEPLOYMENT-PLAN.md`](../docs/AWS-DEPLOYMENT-PLAN.md) for the
complete Amplify setup, environment variables, domain configuration, and test
checklist.
