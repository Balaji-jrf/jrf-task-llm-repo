const LLM_API = (import.meta.env.VITE_LLM_API_URL || '').replace(/\/$/, '');
const OCR_API = (import.meta.env.VITE_OCR_API_URL || '').replace(/\/$/, '');
const ocrDocumentCache = new Map();

async function _fetch(url, options = {}) {
  const body = options.body;
  const resp = await fetch(url, {
    method: options.method || 'GET',
    headers: {
      ...(body ? { 'content-type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
    ...(body ? { body } : {}),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || data.detail || `HTTP ${resp.status}`);
  return data;
}

export const llmApi = {
  health: () => _fetch(`${LLM_API}/api/llm/health`),
  models: () => _fetch(`${LLM_API}/api/llm/models`),

  indexDocuments: (document_ids) =>
    _fetch(`${LLM_API}/api/llm/documents/index`, {
      method: 'POST',
      body: JSON.stringify({ document_ids }),
    }),
  pollIndexJob: (job_id) =>
    _fetch(`${LLM_API}/api/llm/documents/index/${encodeURIComponent(job_id)}`),

  chat: (payload) =>
    _fetch(`${LLM_API}/api/llm/chat`, { method: 'POST', body: JSON.stringify(payload) }),
  pollChatJob: (job_id) =>
    _fetch(`${LLM_API}/api/llm/chat-jobs/${encodeURIComponent(job_id)}`),

  chats: () => _fetch(`${LLM_API}/api/llm/chats`),
  chatDetail: (chat_id) =>
    _fetch(`${LLM_API}/api/llm/chats/${encodeURIComponent(chat_id)}`),

  documentChunks: (document_id, { statusOnly = false } = {}) =>
    _fetch(
      `${LLM_API}/api/llm/documents/${encodeURIComponent(document_id)}/chunks${statusOnly ? '?status_only=true' : ''}`,
    ),
};

export async function fetchOcrDocuments() {
  const data = await _fetch(`${OCR_API}/documents`);
  return data.documents || [];
}

export async function fetchOcrDocument(document_id) {
  const cached = ocrDocumentCache.get(document_id);
  if (cached && cached.expiresAt > Date.now()) return cached.promise;

  const promise = _fetch(`${OCR_API}/document/${encodeURIComponent(document_id)}`);
  ocrDocumentCache.set(document_id, { promise, expiresAt: Date.now() + 5 * 60 * 1000 });
  try {
    return await promise;
  } catch (error) {
    ocrDocumentCache.delete(document_id);
    throw error;
  }
}

export async function waitForJob(pollFn, maxAttempts = 120, intervalMs = 5000) {
  for (let i = 0; i < maxAttempts; i++) {
    const job = await pollFn();
    if (job.status === 'completed' || job.status === 'partial') return job;
    if (job.status === 'failed') throw new Error(job.error || 'Job failed');
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error('Job timed out. Check History or try again.');
}
