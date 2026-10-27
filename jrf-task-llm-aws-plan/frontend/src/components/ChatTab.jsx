import React, { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { fetchOcrDocuments, llmApi, waitForJob } from '../llmApi';
import CitationPanel from './CitationPanel';

const MODES = [
  { id: 'chat',               label: 'Q&A Chat' },
  { id: 'summarize_doc',      label: 'Full Doc Summary' },
  { id: 'summarize_chapter',  label: 'Chapter-wise' },
  { id: 'summarize_page',     label: 'Page Range' },
  { id: 'summarize_chunk',    label: 'Chunk Range' },
];

const MODE_LABELS = {
  chat: 'Q&A',
  summarize_doc: 'Doc Summary',
  summarize_chunk: 'Chunk Summary',
  summarize_page: 'Page Summary',
  summarize_chapter: 'Chapter Summary',
};

export default function ChatTab({ restoredChat, onRestoreConsumed }) {
  const [documents, setDocuments] = useState([]);
  const [models, setModels] = useState([]);
  const [selectedDocs, setSelectedDocs] = useState([]);
  const [selectedModels, setSelectedModels] = useState([]);
  const [mode, setMode] = useState('chat');
  const [question, setQuestion] = useState('');
  const [pageRange, setPageRange] = useState('');
  const [chunkRange, setChunkRange] = useState('');
  const [chapterHint, setChapterHint] = useState('');
  const [chatId, setChatId] = useState('');
  const [turns, setTurns] = useState([]);
  const [citation, setCitation] = useState(null);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const messagesRef = useRef(null);

  // Auto-scroll on new turns
  useEffect(() => {
    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight, behavior: 'smooth' });
  }, [turns]);

  // Load documents + models on mount
  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchOcrDocuments(), llmApi.models()])
      .then(([docs, modelsData]) => {
        if (cancelled) return;
        const completed = docs.filter((d) => d.status === 'completed');
        setDocuments(completed);
        const available = modelsData.models || [];
        setModels(available);
        setSelectedModels(available.slice(0, 1).map((m) => m.model_id));
      })
      .catch((err) => { if (!cancelled) setError(err.message); });
    return () => { cancelled = true; };
  }, []);

  // Restore from history
  useEffect(() => {
    if (!restoredChat) return;
    const { chat, turns: historyTurns } = restoredChat;
    setChatId(chat.chat_id || '');
    setSelectedDocs(chat.document_ids || []);
    setSelectedModels(
      (chat.model_ids || []).filter((id) => models.some((m) => m.model_id === id))
      || models.slice(0, 1).map((m) => m.model_id)
    );
    setTurns(historyTurns || []);
    onRestoreConsumed();
  }, [restoredChat]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleDoc = (id) =>
    setSelectedDocs((prev) =>
      prev.includes(id) ? prev.filter((d) => d !== id) : prev.length < 3 ? [...prev, id] : prev
    );

  const toggleModel = (id) =>
    setSelectedModels((prev) =>
      prev.includes(id) ? prev.filter((m) => m !== id) : prev.length < 4 ? [...prev, id] : prev
    );

  const handleIndex = async () => {
    if (!selectedDocs.length) return;
    setIndexing(true);
    setError('');
    setStatus('Starting indexing job…');
    try {
      const resp = await llmApi.indexDocuments(selectedDocs);
      setStatus(`Indexing job started (${resp.job_id.slice(0, 8)}…). Waiting…`);
      const done = await waitForJob(() => llmApi.pollIndexJob(resp.job_id));
      const indexed = done.indexed_documents || [];
      const total = indexed.reduce((s, d) => s + (d.chunks_indexed || 0), 0);
      setStatus(`✓ Indexed ${indexed.length} document(s) → ${total} chunks (10 pages each).`);
    } catch (err) {
      setError(err.message);
      setStatus('');
    } finally {
      setIndexing(false);
    }
  };

  const buildPayload = () => {
    const base = {
      chat_id: chatId || undefined,
      document_ids: selectedDocs,
      model_ids: selectedModels,
      mode,
    };
    if (mode === 'chat') return { ...base, question: question.trim() };
    if (mode === 'summarize_page') return { ...base, page_range: pageRange };
    if (mode === 'summarize_chunk') return { ...base, chunk_range: chunkRange };
    if (mode === 'summarize_chapter') return { ...base, chapter_hint: chapterHint };
    return base; // summarize_doc needs nothing extra
  };

  const canSend = () => {
    if (busy || !selectedDocs.length || !selectedModels.length) return false;
    if (mode === 'chat') return question.trim().length > 0;
    if (mode === 'summarize_page') return pageRange.trim().length > 0;
    if (mode === 'summarize_chunk') return chunkRange.trim().length > 0;
    return true;
  };

  const handleSend = async (e) => {
    e.preventDefault();
    if (!canSend()) return;
    setBusy(true);
    setError('');
    const modelName = models.find((m) => m.model_id === selectedModels[0])?.display_name || selectedModels[0];
    setStatus(
      selectedModels.length > 1
        ? `Running ${selectedModels.length} models…`
        : `Running ${modelName}…`
    );
    try {
      const resp = await llmApi.chat(buildPayload());
      setChatId(resp.chat_id || chatId);
      const done = await waitForJob(() => llmApi.pollChatJob(resp.job_id));
      const result = done.result || {};
      setTurns((prev) => [...prev, {
        question: result.question || question,
        answers: result.answers || [],
        mode: result.mode || mode,
      }]);
      setQuestion('');
      setStatus(
        result.status === 'complete' ? '' :
        result.status === 'partial' ? 'Some models failed — partial results shown.' :
        'All models failed — see error details below.'
      );
    } catch (err) {
      setError(err.message);
      setStatus('');
    } finally {
      setBusy(false);
    }
  };

  const newChat = () => {
    setChatId('');
    setTurns([]);
    setCitation(null);
    setStatus('');
    setError('');
  };

  const selectedDocNames = documents
    .filter((d) => selectedDocs.includes(d.document_id))
    .map((d) => d.filename);

  return (
    <div className="chat-page">
      {status && <p className="status-bar">{status}</p>}
      {error && <p className="error-bar">{error}</p>}

      <div className="chat-layout">
        {/* ── Left sidebar ── */}
        <aside className="chat-sidebar">
          {/* Documents */}
          <div className="sidebar-card">
            <div className="sidebar-card-head">
              <h3>Documents</h3>
              <span>{selectedDocs.length}/3 selected</span>
            </div>
            <div className="sidebar-card-body">
              {documents.length === 0 && <p className="doc-empty">No completed OCR documents found.</p>}
              <div className="doc-list">
                {documents.map((doc) => (
                  <label
                    key={doc.document_id}
                    className={`doc-item ${selectedDocs.includes(doc.document_id) ? 'selected' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={selectedDocs.includes(doc.document_id)}
                      onChange={() => toggleDoc(doc.document_id)}
                      disabled={!selectedDocs.includes(doc.document_id) && selectedDocs.length >= 3}
                    />
                    <div>
                      <div className="doc-item-name">{doc.filename}</div>
                      <div className="doc-item-meta">{doc.page_count || '?'} pages</div>
                    </div>
                  </label>
                ))}
              </div>
            </div>
            <div className="index-btn-row">
              <button
                className="btn-primary"
                style={{ width: '100%' }}
                onClick={handleIndex}
                disabled={indexing || !selectedDocs.length}
              >
                {indexing ? <><span className="spinner" />Indexing…</> : 'Index selected docs'}
              </button>
            </div>
          </div>

          {/* Models */}
          <div className="sidebar-card">
            <div className="sidebar-card-head">
              <h3>Models</h3>
              <span>{selectedModels.length}/4</span>
            </div>
            <div className="sidebar-card-body">
              <div className="model-list">
                {models.map((model) => (
                  <label
                    key={model.model_id}
                    className={`model-item ${selectedModels.includes(model.model_id) ? 'selected' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={selectedModels.includes(model.model_id)}
                      onChange={() => toggleModel(model.model_id)}
                      disabled={!selectedModels.includes(model.model_id) && selectedModels.length >= 4}
                    />
                    <div>
                      <div className="model-item-name">{model.display_name}</div>
                      <div className="model-item-desc">{model.description}</div>
                      {selectedModels.includes(model.model_id) && (
                        <span className="model-selected-tag">Selected</span>
                      )}
                    </div>
                  </label>
                ))}
              </div>
            </div>
          </div>

          {/* New chat */}
          <button className="btn-ghost" style={{ width: '100%' }} onClick={newChat}>
            + New conversation
          </button>
        </aside>

        {/* ── Centre: chat area ── */}
        <div className="chat-main">
          {/* Context bar */}
          <div className="chat-context-bar">
            <span>Context:</span>
            {selectedDocNames.length === 0
              ? <span style={{ color: '#b0bab5' }}>No documents selected</span>
              : selectedDocNames.map((name) => (
                  <span className="chat-context-doc" key={name}>📄 {name}</span>
                ))
            }
          </div>

          {/* Messages */}
          <div className="chat-messages" ref={messagesRef}>
            {turns.length === 0 && (
              <div style={{ color: '#87928b', font: '12px DM Mono, monospace', padding: '20px 0' }}>
                Select documents, index them, then ask a question or run a summarization.
              </div>
            )}
            {turns.map((turn, i) => (
              <div key={i}>
                <div className="msg-question">{turn.question}</div>
                {turn.mode && turn.mode !== 'chat' && (
                  <div className="msg-mode-tag">{MODE_LABELS[turn.mode] || turn.mode}</div>
                )}
                <div className="msg-answers">
                  {(turn.answers || []).map((answer) => (
                    <div className="answer-card" key={answer.model_id}>
                      <div className="answer-card-model">
                        {answer.display_name || answer.model_id}
                      </div>
                      <span className="answer-card-model-id">{answer.model_id}</span>

                      {answer.error
                        ? <p className="answer-error">{answer.error}</p>
                        : <div className="answer-text answer-markdown">
                            <ReactMarkdown>{answer.text}</ReactMarkdown>
                          </div>
                      }

                      {answer.performance && (
                        <div className="answer-perf">
                          <span>⏱ {answer.performance.inference_seconds}s</span>
                          {answer.performance.output_tokens_per_second > 0 && (
                            <span>{answer.performance.output_tokens_per_second} tok/s</span>
                          )}
                        </div>
                      )}
                      {answer.usage?.output_tokens > 0 && (
                        <span className="answer-tokens">
                          {answer.usage.input_tokens} in / {answer.usage.output_tokens} out tokens
                        </span>
                      )}

                      {/* Citations */}
                      {(answer.citations?.length > 0) && (
                        <div className="citations-row">
                          {answer.citations.map((c) => (
                            <button
                              key={c.source_id}
                              className="citation-btn"
                              onClick={() => setCitation(c)}
                            >
                              <span className="citation-btn-icon">📌</span>
                              {c.source_id} · p.{c.page_start || c.page}–{c.page_end || c.page}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* Input area */}
          <div className="chat-input-area">
            {/* Summarization mode selector */}
            <div className="summarize-bar">
              <span className="summarize-bar-label">Mode</span>
              {MODES.map((m) => (
                <button
                  key={m.id}
                  className={`sum-btn ${mode === m.id ? 'active' : ''}`}
                  onClick={() => setMode(m.id)}
                  type="button"
                >
                  {m.label}
                </button>
              ))}
            </div>

            {/* Sub-options for summarization modes */}
            {mode === 'summarize_page' && (
              <div className="sum-options">
                <label>
                  Page range (e.g. 1-20)
                  <input
                    value={pageRange}
                    onChange={(e) => setPageRange(e.target.value)}
                    placeholder="1-20"
                  />
                </label>
              </div>
            )}
            {mode === 'summarize_chunk' && (
              <div className="sum-options">
                <label>
                  Chunk range (e.g. 1-3)
                  <input
                    value={chunkRange}
                    onChange={(e) => setChunkRange(e.target.value)}
                    placeholder="1-3"
                  />
                </label>
              </div>
            )}
            {mode === 'summarize_chapter' && (
              <div className="sum-options">
                <label>
                  Chapter hint (optional)
                  <input
                    value={chapterHint}
                    onChange={(e) => setChapterHint(e.target.value)}
                    placeholder="e.g. Chapter 2: Housing Policy"
                  />
                </label>
              </div>
            )}

            <form onSubmit={handleSend}>
              <div className="chat-input-row">
                {mode === 'chat' ? (
                  <textarea
                    className="chat-textarea"
                    rows={2}
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    placeholder="Ask a question about the selected documents…"
                    maxLength={4000}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(e); }
                    }}
                  />
                ) : (
                  <div
                    className="chat-textarea"
                    style={{ display: 'flex', alignItems: 'center', color: '#87928b', font: '12px DM Mono, monospace' }}
                  >
                    {MODES.find((m) => m.id === mode)?.label} — click Run to execute
                  </div>
                )}
                <button
                  type="submit"
                  className="btn-primary chat-send-btn"
                  disabled={!canSend()}
                >
                  {busy
                    ? <><span className="spinner" />Running…</>
                    : mode === 'chat'
                      ? selectedModels.length > 1 ? `Compare ${selectedModels.length}` : 'Send'
                      : 'Run'
                  }
                </button>
              </div>
            </form>
          </div>
        </div>

        {/* ── Right: citation panel ── */}
        <CitationPanel citation={citation} onClose={() => setCitation(null)} />
      </div>
    </div>
  );
}
