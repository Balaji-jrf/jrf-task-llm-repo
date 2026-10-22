# Qwen document-Q&A backend

The backend reads completed OCR results from the existing OCR API, chunks the text,
retrieves passages with local BM25 search, sends those passages to the Hugging Face
Qwen2.5-1.5B-Instruct GGUF model through `llama.cpp`, and records answers/citations
in JSON chat history. It does not write to the OCR API or its storage.

## EC2 staging deployment

The exact AWS CLI steps for an EC2 instance, Systems Manager, security groups, DNS,
and Amplify are in [`../docs/AWS-DEPLOYMENT-PLAN.md`](../docs/AWS-DEPLOYMENT-PLAN.md).
Use an Ubuntu 24.04 `m7i.large` instance with an encrypted 30-GB gp3 root volume,
IMDSv2 required, and an instance role limited to Systems Manager. Do not open SSH.

After launching the instance and pointing the API DNS A record to its public IPv4,
connect using Session Manager and run:

```bash
sudo apt-get update
sudo apt-get install -y git
sudo git clone https://github.com/Balaji-jrf/jrf-task-llm-repo.git /opt/jrf-task-llm
cd /opt/jrf-task-llm
sudo bash backend/deploy/install-ec2.sh \
  api.<your-domain> \
  https://<your-amplify-branch-domain>
```

The install script creates a non-login service user, Python virtual environment,
downloads the Qwen GGUF from Hugging Face, creates a systemd unit with filesystem
protections, and configures Caddy for automatic TLS. It assumes the DNS A record
already resolves to the EC2 instance and inbound port 80 is available for TLS
certificate validation. The service binds to `127.0.0.1:8787`; only Caddy accepts
public API traffic.

## EC2 security settings

- Allow HTTPS port 443 only from the tester's public IP CIDR(s), normally `/32`.
- Allow HTTP port 80 so Caddy can validate and renew TLS certificates and redirect
  to HTTPS. Do not serve API responses over plain HTTP.
- Do not create an SSH ingress rule. Use the `AmazonSSMManagedInstanceCore` role.
- Require IMDSv2 and use encrypted EBS. Keep OCR service IAM permissions out of the
  EC2 role; OCR document access uses the existing read-only API and signed URLs.
- Restrict CORS with `JRF_TASK_LLM_ALLOWED_ORIGINS` to the exact Amplify/custom
  frontend origin. CORS is not authentication; keep the EC2 security-group IP
  restriction in place for the demonstration.
- Use `JRF_TASK_LLM_MODEL_THREADS=2` on the two-vCPU instance. Inference is
  serialized to prevent concurrent requests exhausting RAM.

## Environment variables

The installer writes `/etc/jrf-task-llm.env` with:

| Variable | Purpose |
|---|---|
| `LOCAL_CHAT_MODEL_PATH` | Absolute path to downloaded Qwen GGUF |
| `OCR_API_BASE_URL` | Existing read-only OCR API base |
| `JRF_TASK_LLM_DATA_DIR` | EBS path for JSON chat history and BM25 chunks |
| `JRF_TASK_LLM_HOST` | Bind address; keep loopback behind Caddy |
| `JRF_TASK_LLM_PORT` | API port; default `8787` |
| `JRF_TASK_LLM_MODEL_THREADS` | CPU threads for local llama.cpp |
| `JRF_TASK_LLM_ALLOWED_ORIGINS` | Comma-separated exact browser origins |

The frontend separately needs `VITE_JRF_TASK_LLM_API_URL=https://api.<your-domain>/api`
in Amplify. `VITE_OCR_API_URL` may be set to the existing OCR API base; if omitted,
the current frontend default is used.

## API routes

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Service health check |
| `GET` | `/api/models` | Return the configured Qwen model |
| `POST` | `/api/documents/index` | Index up to three completed OCR documents |
| `GET` | `/api/documents/index/{job_id}` | Read indexing result |
| `POST` | `/api/chat` | Ask a question using Qwen and BM25 |
| `GET` | `/api/chat-jobs/{job_id}` | Read chat result |
| `GET` | `/api/chats` | List saved conversation summaries |
| `GET` | `/api/chats/{chat_id}` | Read turns, sources, and citations |

## Operations and tests

```bash
sudo systemctl status jrf-task-llm caddy
sudo journalctl -u jrf-task-llm -f
sudo journalctl -u caddy -f
curl --fail https://api.<your-domain>/api/health
```

Run tests before pushing changes:

```bash
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q jrf_task_llm tests
```

The JSON files contain document excerpts and user conversations. Keep EBS encrypted,
limit service access, and arrange a backup before instance replacement. This
single-instance design is suitable for a short staging demonstration, not a
multi-instance or production deployment.
