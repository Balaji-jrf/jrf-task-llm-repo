import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchOcrDocuments, llmFetch } from '../llmApi';
import HistoryOfLLMChat from './HistoryOfLLMChat';
import LLMComparePane from './LLMComparePane';
import './llm.css';

export default function LLMTab({ apiUrl, ocrApiUrl }) {
  const [documents, setDocuments] = useState([]);
  const [selectedDocuments, setSelectedDocuments] = useState([]);
  const [models, setModels] = useState([]);
  const [selectedModels, setSelectedModels] = useState([]);
  const [question, setQuestion] = useState('');
  const [chatId, setChatId] = useState('');
  const [turns, setTurns] = useState([]);
  const [citation, setCitation] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refreshHistory, setRefreshHistory] = useState(0);
  const answersRef = useRef(null);

  useEffect(() => {
    const answers = answersRef.current;
    if (answers) answers.scrollTo({ top: answers.scrollHeight, behavior: 'smooth' });
  }, [turns]);

  const waitForJob = async (jobPath) => {
    for (let attempt = 0; attempt < 190; attempt += 1) {
      const job = await llmFetch(apiUrl, jobPath);
      if (job.status === 'completed') return job;
      if (job.status === 'failed') throw new Error(job.error || 'The background job failed.');
      await new Promise((resolve) => setTimeout(resolve, 5000));
    }
    throw new Error('The job is still processing. Check History or poll its job ID again later.');
  };

  const loadModels = useCallback(async () => {
    const response = await llmFetch(apiUrl, 'models');
    const available = response.models || [];
    setModels(available);
    setSelectedModels(available.slice(0, 1).map((model) => model.model_id));
  }, [apiUrl]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchOcrDocuments(ocrApiUrl), loadModels()])
      .then(([ocrDocuments]) => {
        if (!cancelled) setDocuments(ocrDocuments.filter((item) => item.status === 'completed'));
      })
      .catch((loadError) => {
        if (!cancelled) setError(loadError.message);
      });
    return () => { cancelled = true; };
  }, [loadModels, ocrApiUrl]);

  const toggleDocument = (id) => {
    setSelectedDocuments((current) => (
      current.includes(id)
        ? current.filter((documentId) => documentId !== id)
        : current.length < 3 ? [...current, id] : current
    ));
  };

  const toggleModel = (id) => {
    setSelectedModels([id]);
  };

  const indexSelectedDocuments = async () => {
    setBusy(true);
    setError('');
    try {
      const response = await llmFetch(apiUrl, 'documents/index', {
        method: 'POST',
        body: JSON.stringify({ document_ids: selectedDocuments }),
      });
      setError(`Indexing job ${response.job_id} is running…`);
      const completed = await waitForJob(`documents/index/${encodeURIComponent(response.job_id)}`);
      const indexed = completed.indexed_documents || [];
      setError(`Indexed ${indexed.length} documents (${indexed.reduce((sum, item) => sum + item.chunks_indexed, 0)} text chunks).`);
    } catch (indexError) {
      setError(indexError.message);
    } finally {
      setBusy(false);
    }
  };

  const sendQuestion = async (event) => {
    event.preventDefault();
    if (!question.trim() || !selectedDocuments.length || !selectedModels.length) return;
    setBusy(true);
    setError('');
    try {
      const response = await llmFetch(apiUrl, 'chat', {
        method: 'POST',
        body: JSON.stringify({
          chat_id: chatId || undefined,
          document_ids: selectedDocuments,
          model_ids: selectedModels,
          question: question.trim(),
        }),
      });
      setChatId(response.chat_id);
      setError('Running the local Qwen model…');
      const completed = await waitForJob(`chat-jobs/${encodeURIComponent(response.job_id)}`);
      const result = completed.result;
      setChatId(result.chat_id);
      setTurns((current) => [...current, {
        question: result.question,
        answers: result.answers || [],
      }]);
      setQuestion('');
      setRefreshHistory((value) => value + 1);
      setError(result.status === 'complete' ? '' : `The Qwen request did not complete (${result.status}).`);
    } catch (chatError) {
      setError(chatError.message);
      setRefreshHistory((value) => value + 1);
    } finally {
      setBusy(false);
    }
  };

  const selectHistory = (record) => {
    const chat = record.chat;
    setChatId(chat.chat_id);
    setSelectedDocuments(chat.document_ids || []);
    const availableHistoryModels = (chat.model_ids || [])
      .filter((id) => models.some((model) => model.model_id === id));
    setSelectedModels(availableHistoryModels.length
      ? availableHistoryModels.slice(0, 1)
      : models.slice(0, 1).map((model) => model.model_id));
    setTurns(record.turns || []);
  };

  const selectedDocumentNames = useMemo(
    () => new Map(documents.map((document) => [document.document_id, document.filename])),
    [documents],
  );
  return (
    <main className="jrf-llm-workspace">
      <header className="llm-workspace-header">
        <div>
          <p className="llm-kicker">JRF TASK · DOCUMENT AI</p>
          <h1>LLM Research Chat</h1>
          <p>Ask questions about OCR documents and inspect the source pages behind answers.</p>
        </div>
        <button type="button" onClick={() => {
          setChatId('');
          setTurns([]);
          setCitation(null);
        }}>New chat</button>
      </header>

      {error && <p className="llm-status" role="status">{error}</p>}

      <section className="llm-setup">
        <div>
          <h2>1. Select OCR documents</h2>
          <p>Choose up to three existing completed OCR documents. OCR data is read-only.</p>
          <div className="llm-choice-list">
            {documents.map((document) => (
              <label key={document.document_id}>
                <input
                  type="checkbox"
                  checked={selectedDocuments.includes(document.document_id)}
                  onChange={() => toggleDocument(document.document_id)}
                />
                <span>{document.filename}</span>
              </label>
            ))}
          </div>
          <button type="button" onClick={indexSelectedDocuments} disabled={busy || !selectedDocuments.length}>
            {busy ? 'Working…' : 'Index selected OCR documents'}
          </button>
        </div>
        <div>
          <h2>2. Local chat model</h2>
          <p>This workspace uses the downloaded Qwen model on your computer.</p>
          <div className="llm-choice-list">
            {models.map((model) => (
              <label key={model.model_id}>
                <input
                  type="radio"
                  checked={selectedModels.includes(model.model_id)}
                  onChange={() => toggleModel(model.model_id)}
                />
                <span>{model.model_id}</span>
              </label>
            ))}
          </div>
        </div>
      </section>

      <div className={`llm-work-area ${citation ? 'has-compare' : ''}`}>
        {citation && (
          <LLMComparePane
            ocrApiUrl={ocrApiUrl}
            citation={citation}
            onClose={() => setCitation(null)}
          />
        )}
        <section className="llm-chat">
          <div className="llm-chat-context">
            {selectedDocuments.map((id) => selectedDocumentNames.get(id) || id).join(' · ') || 'Select documents to begin'}
          </div>
          <div className="llm-answers" ref={answersRef} aria-live="polite">
            {turns.map((turn, turnIndex) => (
              <section className="llm-turn" key={`${turn.created_at || turnIndex}-${turnIndex}`}>
                <p className="llm-question-bubble">{turn.question}</p>
                {(turn.answers || []).map((answer) => (
                  <article className="llm-answer-card" key={`${turnIndex}-${answer.model_id}`}>
                    <h3>{answer.model_id}</h3>
                    {answer.error
                      ? <p className="llm-error" role="alert">{answer.error}</p>
                      : <p className="llm-answer-text">{answer.text}</p>}
                    {answer.usage
                      && Number.isFinite(Number(answer.usage.input_tokens))
                      && Number.isFinite(Number(answer.usage.output_tokens)) && (
                      <small>
                        Tokens: {answer.usage.input_tokens || 0} input / {answer.usage.output_tokens || 0} output
                      </small>
                    )}
                    {answer.sources?.length > 0 && (
                      <small>
                        {answer.citations?.length
                          ? 'Model-cited sources'
                          : 'Retrieved sources (the model did not cite inline)'}
                      </small>
                    )}
                    <div className="llm-citations">
                      {(answer.citations?.length ? answer.citations : answer.sources || []).map((item) => (
                        <button
                          type="button"
                          key={`${answer.model_id}-${item.source_id}`}
                          onClick={() => setCitation(item)}
                        >
                          Open {item.source_id} · {item.filename} · p.{item.page}
                        </button>
                      ))}
                    </div>
                  </article>
                ))}
              </section>
            ))}
          </div>
          <form className="llm-question-form" onSubmit={sendQuestion}>
            <label htmlFor="jrf-llm-question">Ask about the selected documents</label>
            <textarea
              id="jrf-llm-question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              maxLength={4000}
              rows={3}
              placeholder="Ask a question about the selected OCR documents…"
            />
            <button type="submit" disabled={busy || !question.trim() || !selectedDocuments.length || !selectedModels.length}>
              {busy ? 'Sending…' : 'Ask Qwen'}
            </button>
          </form>
        </section>
        <HistoryOfLLMChat
          apiUrl={apiUrl}
          onSelectChat={selectHistory}
          refreshToken={refreshHistory}
        />
      </div>
    </main>
  );
}
