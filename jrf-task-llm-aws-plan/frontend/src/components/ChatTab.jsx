import React, { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { fetchOcrDocuments, llmApi, waitForJob } from '../llmApi';
import { getDocumentTagOptions, UNTAGGED_FILTER } from '../documentTags';
import CitationPanel from './CitationPanel';

const MODES = [
  { id: 'chat',               label: 'Q&A Chat' },
  { id: 'summarize_doc',      label: 'Full Doc Summary' },
  { id: 'summarize_chapter',  label: 'Chapter-wise' },
  { id: 'summarize_page',     label: 'Page Range' },
];

const MODE_LABELS = {
  chat: 'Q&A',
  summarize_doc: 'Doc Summary',
  summarize_page: 'Page Summary',
  summarize_chapter: 'Chapter Summary',
};

function addInlineCitationLinks(answer) {
  const citations = answer.citations || [];
  const bySourceId = new Map(citations.map((citation) => [citation.source_id, citation]));
  return String(answer.text || '').replace(/\[(src\d+(?:\s*,\s*src\d+)*)\]/gi, (marker, sourceIds) => {
    const links = sourceIds.split(',').map((sourceId) => sourceId.trim()).map((sourceId) => {
      const citation = bySourceId.get(sourceId);
      if (!citation) return sourceId;
      const page = citation.page ?? citation.page_start ?? 1;
      return `[p.${page} · ${sourceId}](https://citation.local/${sourceId})`;
    });
    return links.join(' ');
  });
}

function AnswerMarkdown({ answer, onCitation }) {
  return (
    <ReactMarkdown components={{
      a: ({ href, children, ...props }) => {
        const match = href?.match(/^https:\/\/citation\.local\/(src\d+)$/);
        if (match) {
          const source = answer.citations?.find((item) => item.source_id === match[1]);
          return source ? (
            <button
              type="button"
              className="inline-citation"
              title={`${source.filename}, page ${source.page}`}
              onClick={() => onCitation(source)}
            >
              {children}
            </button>
          ) : null;
        }
        return <a href={href} target="_blank" rel="noreferrer" {...props}>{children}</a>;
      },
    }}>
      {addInlineCitationLinks(answer)}
    </ReactMarkdown>
  );
}

export default function ChatTab({ restoredChat, onRestoreConsumed }) {
  const [documents, setDocuments] = useState([]);
  const [documentIndexStatus, setDocumentIndexStatus] = useState({});
  const [tagFilter, setTagFilter] = useState('all');
  const [documentSearch, setDocumentSearch] = useState('');
  const [models, setModels] = useState([]);
  const [selectedDocs, setSelectedDocs] = useState([]);
  const [selectedModels, setSelectedModels] = useState([]);
  const [compareMode, setCompareMode] = useState(false);
  const [compareModel, setCompareModel] = useState('');
  const [mode, setMode] = useState('chat');
  const [question, setQuestion] = useState('');
  const [pageRange, setPageRange] = useState('');
  const [chapterHint, setChapterHint] = useState('');
  const [chatId, setChatId] = useState('');
  const [turns, setTurns] = useState([]);
  const [citation, setCitation] = useState(null);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const messagesRef = useRef(null);
  const layoutRef = useRef(null);
  const resizingRef = useRef(null);
  const [sidebarWidth, setSidebarWidth] = useState(300);
  const [citationWidth, setCitationWidth] = useState(360);

  const startResize = (pane, event) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    resizingRef.current = { pane, startX: event.clientX, startWidth: pane === 'sidebar' ? sidebarWidth : citationWidth };
  };
  const resizePane = (event) => {
    const resizing = resizingRef.current;
    if (!resizing) return;
    const availableWidth = layoutRef.current?.clientWidth || window.innerWidth;
    const maxWidth = Math.max(220, Math.min(520, availableWidth - 760));
    const delta = event.clientX - resizing.startX;
    if (resizing.pane === 'sidebar') {
      setSidebarWidth(Math.max(200, Math.min(maxWidth, resizing.startWidth + delta)));
    } else {
      setCitationWidth(Math.max(260, Math.min(maxWidth, resizing.startWidth - delta)));
    }
  };
  const stopResize = () => { resizingRef.current = null; };
  const resizeWithKeyboard = (pane, event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const delta = event.key === 'ArrowRight' ? 20 : -20;
    const availableWidth = layoutRef.current?.clientWidth || window.innerWidth;
    const maxWidth = Math.max(220, Math.min(520, availableWidth - 760));
    if (pane === 'sidebar') {
      setSidebarWidth((width) => Math.max(200, Math.min(maxWidth, width + delta)));
    } else {
      setCitationWidth((width) => Math.max(260, Math.min(maxWidth, width - delta)));
    }
  };

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
        setDocumentIndexStatus(Object.fromEntries(
          completed.map((doc) => [doc.document_id, 'checking']),
        ));
        const queue = [...completed];
        const workers = Array.from({ length: Math.min(4, queue.length) }, async () => {
          while (queue.length) {
            const doc = queue.shift();
            try {
              const status = await llmApi.documentChunks(doc.document_id, { statusOnly: true });
              if (!cancelled) {
                setDocumentIndexStatus((current) => ({
                  ...current,
                  [doc.document_id]: status.index_status || (status.total > 0 ? 'DONE' : 'NOT_INDEXED'),
                }));
              }
            } catch {
              if (!cancelled) {
                setDocumentIndexStatus((current) => ({ ...current, [doc.document_id]: 'UNAVAILABLE' }));
              }
            }
          }
        });
        Promise.all(workers).catch((err) => {
          if (!cancelled) setError(`Unable to check document index status: ${err.message}`);
        });
        const available = modelsData.models || [];
        setModels(available);
        setSelectedModels(available.slice(0, 1).map((m) => m.model_id));
        setCompareModel(available[1]?.model_id || '');
      })
      .catch((err) => { if (!cancelled) setError(err.message); });
    return () => { cancelled = true; };
  }, []);

  // Restore from history
  useEffect(() => {
    if (!restoredChat || !models.length) return;
    const { chat, turns: historyTurns } = restoredChat;
    setChatId(chat.chat_id || '');
    setSelectedDocs(chat.document_ids || []);
    const validModels = (chat.model_ids || []).filter((id) => models.some((m) => m.model_id === id));
    setSelectedModels(validModels.slice(0, 1).length ? validModels.slice(0, 1) : models.slice(0, 1).map((m) => m.model_id));
    setCompareModel(validModels[1] || models.find((model) => model.model_id !== validModels[0])?.model_id || '');
    setCompareMode(validModels.length > 1);
    setTurns(historyTurns || []);
    onRestoreConsumed();
  }, [restoredChat, models, onRestoreConsumed]);

  const toggleDoc = (id) =>
    setSelectedDocs((prev) =>
      prev.includes(id) ? prev.filter((d) => d !== id) : prev.length < 5 ? [...prev, id] : prev
    );

  const selectModel = (id) => {
    if (id === compareModel) setCompareModel(selectedModels[0] || '');
    setSelectedModels([id]);
  };

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
      const total = indexed.reduce((sum, doc) => sum + (doc.chunks_indexed || 0), 0);
      const reused = indexed.reduce((sum, doc) => sum + (doc.chunks_reused || 0), 0);
      setDocumentIndexStatus((current) => ({
        ...current,
        ...Object.fromEntries(indexed.map((doc) => [doc.document_id, 'DONE'])),
      }));
      setStatus(`Prepared ${indexed.length} document(s): ${total} new chunks, ${reused} reused.`);
      if (done.errors?.length) {
        setError(done.errors.map((item) => `${item.document_id}: ${item.error}`).join('; '));
      }
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
      model_ids: compareMode ? [...selectedModels, compareModel] : selectedModels,
      compare_mode: compareMode,
      mode,
    };
    if (mode === 'chat') return { ...base, question: question.trim() };
    if (mode === 'summarize_page') return { ...base, page_range: pageRange };
    if (mode === 'summarize_chapter') return { ...base, chapter_hint: chapterHint };
    return base; // summarize_doc needs nothing extra
  };

  const canSend = () => {
    if (busy || indexing || !selectedDocs.length || !selectedModels.length) return false;
    if (compareMode && (!compareModel || compareModel === selectedModels[0])) return false;
    if (mode === 'chat') return question.trim().length > 0;
    if (mode === 'summarize_page') return pageRange.trim().length > 0;
    return true;
  };

  const handleSend = async (e) => {
    e.preventDefault();
    if (!canSend()) return;
    setBusy(true);
    setError('');
    const modelName = models.find((m) => m.model_id === selectedModels[0])?.display_name || selectedModels[0];
    setStatus(compareMode ? `Comparing ${modelName} and ${models.find((model) => model.model_id === compareModel)?.display_name || compareModel}…` : `Running ${modelName}…`);
    try {
      const indexStates = await Promise.all(
        selectedDocs.map(async (documentId) => ({
          documentId,
          data: await llmApi.documentChunks(documentId, { statusOnly: true }),
        })),
      );
      const missing = indexStates.filter(({ data }) => data.index_status !== 'DONE').map(({ documentId }) => documentId);
      if (missing.length) {
        setStatus(`Preparing ${missing.length} document(s) before chat…`);
        const indexJob = await llmApi.indexDocuments(missing);
        const indexed = await waitForJob(() => llmApi.pollIndexJob(indexJob.job_id));
        if (indexed.status !== 'completed') {
          throw new Error(indexed.errors?.map((item) => item.error).filter(Boolean).join('; ') || 'Could not prepare every selected document.');
        }
        const refreshed = await Promise.all(
          missing.map((documentId) => llmApi.documentChunks(documentId, { statusOnly: true })),
        );
        if (refreshed.some((data) => data.index_status !== 'DONE')) {
          throw new Error('No searchable OCR text or tables were found in one or more selected documents.');
        }
        setDocumentIndexStatus((current) => ({
          ...current,
          ...Object.fromEntries(missing.map((documentId) => [documentId, 'DONE'])),
        }));
      }
      setStatus(compareMode ? 'Running both models on the same retrieved sources…' : `Running ${modelName}…`);
      const resp = await llmApi.chat(buildPayload());
      const done = await waitForJob(() => llmApi.pollChatJob(resp.job_id));
      const result = done.result || {};
      setChatId(result.chat_id || resp.chat_id || chatId);
      setTurns((prev) => [...prev, {
        question: result.question || question,
        answers: result.answers || [],
        mode: result.mode || mode,
      }]);
      setQuestion('');
      setStatus(
        result.status === 'complete' ? '' :
        result.status === 'partial' ? 'Some document summaries or models failed — partial results shown.' :
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
    .map((d) => d.doc_name || d.filename);
  const tagOptions = getDocumentTagOptions(documents);
  const visibleDocuments = documents.filter((doc) => {
    const savedTag = doc.doc_tag?.trim() || '';
    const matchesTag = tagFilter === 'all'
      || (tagFilter === UNTAGGED_FILTER ? !savedTag : savedTag === tagFilter);
    const searchText = `${doc.doc_name || doc.filename || ''} ${doc.document_id}`.toLowerCase();
    return matchesTag && searchText.includes(documentSearch.toLowerCase());
  });

  return (
    <div className="chat-page">
      {status && <p className="status-bar">{status}</p>}
      {error && <p className="error-bar">{error}</p>}

      <div
        className="chat-layout"
        ref={layoutRef}
        style={{ '--chat-sidebar-width': `${sidebarWidth}px`, '--chat-citation-width': `${citationWidth}px` }}
      >
        {/* ── Left sidebar ── */}
        <aside className="chat-sidebar">
          {/* Documents */}
          <div className="sidebar-card">
            <div className="sidebar-card-head">
              <h3>Documents</h3>
              <span>{selectedDocs.length}/5 selected</span>
            </div>
            <div className="sidebar-card-body">
              <div className="doc-filters">
                <select
                  aria-label="Filter documents by tag"
                  value={tagFilter}
                  onChange={(event) => setTagFilter(event.target.value)}
                >
                  {tagOptions.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
                <input
                  type="search"
                  aria-label="Search documents"
                  placeholder="Search documents…"
                  value={documentSearch}
                  onChange={(event) => setDocumentSearch(event.target.value)}
                />
              </div>
              {documents.length === 0 && <p className="doc-empty">No completed OCR documents found.</p>}
              {documents.length > 0 && visibleDocuments.length === 0 && (
                <p className="doc-empty">No documents match this tag or search.</p>
              )}
              <div className="doc-list">
                {visibleDocuments.map((doc) => (
                  <label
                    key={doc.document_id}
                    className={`doc-item ${selectedDocs.includes(doc.document_id) ? 'selected' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={selectedDocs.includes(doc.document_id)}
                      onChange={() => toggleDoc(doc.document_id)}
                      disabled={!selectedDocs.includes(doc.document_id) && selectedDocs.length >= 5}
                    />
                    <div>
                      <div className="doc-item-name">{doc.doc_name || doc.filename}</div>
                      <div className="doc-item-meta">
                        {doc.total_pages || '?'} pages{doc.doc_tag ? ` · ${doc.doc_tag}` : ''}
                      </div>
                      <span className={`doc-index-status doc-index-status-${(documentIndexStatus[doc.document_id] || 'checking').toLowerCase()}`}>
                        {documentIndexStatus[doc.document_id] === 'DONE'
                          ? 'Chunked · DONE'
                          : documentIndexStatus[doc.document_id] === 'NOT_INDEXED'
                            ? 'Not chunked'
                            : documentIndexStatus[doc.document_id] === 'UNAVAILABLE'
                              ? 'Index status unavailable'
                              : 'Checking index…'}
                      </span>
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
                {indexing ? <><span className="spinner" />Preparing…</> : 'Index / refresh selected docs'}
              </button>
              <p className="index-help">Prepared chunks are saved in DynamoDB and reused for chat until OCR changes.</p>
            </div>
          </div>

          {/* Models */}
          <div className="sidebar-card">
            <div className="sidebar-card-head">
              <h3>Models</h3>
              <span>{selectedModels.length ? '1 selected' : 'Choose one'}</span>
            </div>
            <div className="sidebar-card-body">
              <div className="model-list">
                {models.map((model) => (
                  <label
                    key={model.model_id}
                    className={`model-item ${selectedModels.includes(model.model_id) ? 'selected' : ''}`}
                  >
                    <input
                      type="radio"
                      name="chat-model"
                      checked={selectedModels.includes(model.model_id)}
                      onChange={() => selectModel(model.model_id)}
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
            <div className="compare-model-control">
              <label className="compare-mode-toggle">
                <input type="checkbox" checked={compareMode} onChange={(event) => setCompareMode(event.target.checked)} />
                Compare two models
              </label>
              {compareMode && (
                <label className="compare-model-select">
                  Second model
                  <select value={compareModel} onChange={(event) => setCompareModel(event.target.value)}>
                    <option value="">Choose a different model</option>
                    {models.filter((model) => model.model_id !== selectedModels[0]).map((model) => (
                      <option key={model.model_id} value={model.model_id}>{model.display_name}</option>
                    ))}
                  </select>
                </label>
              )}
            </div>
          </div>

          {/* New chat */}
          <button className="btn-ghost" style={{ width: '100%' }} onClick={newChat}>
            + New conversation
          </button>
        </aside>

        {/* ── Centre: chat area ── */}
        <div
          className="chat-resize-handle chat-resize-sidebar"
          role="separator"
          aria-label="Resize document and chat panels"
          aria-orientation="vertical"
          aria-valuenow={sidebarWidth}
          tabIndex={0}
          onPointerDown={(event) => startResize('sidebar', event)}
          onPointerMove={resizePane}
          onPointerUp={stopResize}
          onPointerCancel={stopResize}
          onLostPointerCapture={stopResize}
          onKeyDown={(event) => resizeWithKeyboard('sidebar', event)}
        />
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
                <div className={`msg-answers ${turn.answers?.length === 2 ? 'msg-answers-compare' : ''}`}>
                  {(turn.answers || []).map((answer) => (
                    <div className="answer-card" key={answer.model_id}>
                      <div className="answer-card-model">
                        {answer.display_name || answer.model_id}
                      </div>
                      <span className="answer-card-model-id">{answer.model_id}</span>

                      {answer.error && <p className="answer-error">{answer.error}</p>}
                      {answer.document_summaries?.length ? (
                        <div className="answer-document-list">
                          {answer.document_summaries.map((document) => (
                            <section className="answer-document" key={document.document_id}>
                              <h4 className="answer-document-title">
                                <span>
                                  {turn.mode === 'chat' ? 'Answer for' :
                                    turn.mode === 'summarize_page' ? 'Page-range summary' :
                                      turn.mode === 'summarize_chapter' ? 'Chapter-wise summary' :
                                        'Document summary'}
                                </span>
                                {document.filename}
                              </h4>
                              {document.error
                                ? <p className="answer-error">{document.error}</p>
                                : <div className="answer-text answer-markdown">
                                  <AnswerMarkdown answer={document} onCitation={setCitation} />
                                </div>
                              }
                            </section>
                          ))}
                        </div>
                      ) : !answer.error && (
                        <div className="answer-text answer-markdown">
                          <AnswerMarkdown answer={answer} onCitation={setCitation} />
                        </div>
                      )}

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
            {mode === 'summarize_chapter' && (
              <div className="sum-options">
                <label>
                  Chapter number
                  <input
                    value={chapterHint}
                    onChange={(e) => setChapterHint(e.target.value)}
                    placeholder="e.g. 2 (blank = all chapters)"
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
                      ? compareMode ? 'Compare models' : 'Send'
                      : 'Run'
                  }
                </button>
              </div>
            </form>
          </div>
        </div>

        {/* ── Right: citation panel ── */}
        <div
          className="chat-resize-handle chat-resize-citation"
          role="separator"
          aria-label="Resize chat and source viewer panels"
          aria-orientation="vertical"
          aria-valuenow={citationWidth}
          tabIndex={0}
          onPointerDown={(event) => startResize('citation', event)}
          onPointerMove={resizePane}
          onPointerUp={stopResize}
          onPointerCancel={stopResize}
          onLostPointerCapture={stopResize}
          onKeyDown={(event) => resizeWithKeyboard('citation', event)}
        />
        <CitationPanel citation={citation} onClose={() => setCitation(null)} />
      </div>
    </div>
  );
}
