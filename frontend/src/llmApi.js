export async function llmFetch(apiUrl, path, options = {}) {
  const url = new URL(path, apiUrl.endsWith('/') ? apiUrl : `${apiUrl}/`);
  const body = options.body;
  const response = await fetch(url, {
    method: options.method || 'GET',
    headers: {
      ...(body ? { 'content-type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
    ...(body ? { body } : {}),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || `LLM API returned HTTP ${response.status}.`);
  }
  return data;
}

export async function fetchOcrDocuments(ocrApiUrl) {
  const response = await fetch(`${ocrApiUrl.replace(/\/$/, '')}/documents`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Could not load OCR document history.');
  return data.documents || [];
}

export async function fetchOcrDocument(ocrApiUrl, documentId) {
  const response = await fetch(
    `${ocrApiUrl.replace(/\/$/, '')}/document/${encodeURIComponent(documentId)}`,
  );
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Could not load the OCR document.');
  return data;
}
