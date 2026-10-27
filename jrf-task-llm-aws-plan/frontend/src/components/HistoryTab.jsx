import React, { useCallback, useEffect, useState } from 'react';
import { llmApi } from '../llmApi';

const MODE_LABELS = {
  chat: 'Q&A',
  summarize_doc: 'Doc Summary',
  summarize_chunk: 'Chunk Summary',
  summarize_page: 'Page Summary',
  summarize_chapter: 'Chapter Summary',
};

export default function HistoryTab({ onOpenChat }) {
  const [chats, setChats] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await llmApi.chats();
      setChats(data.chats || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const open = async (chatId) => {
    try {
      const data = await llmApi.chatDetail(chatId);
      onOpenChat(data);
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <div className="history-page">
      <div className="history-page-head">
        <div>
          <p className="eyebrow">Archive Chat · History</p>
          <h2>Chat History</h2>
        </div>
        <button className="btn-ghost" onClick={load} disabled={loading}>
          {loading ? <><span className="spinner" />Loading…</> : 'Refresh'}
        </button>
      </div>

      {error && <p className="error-bar">{error}</p>}

      {!loading && chats.length === 0 && (
        <p className="history-empty">No saved conversations yet. Start a chat to see history here.</p>
      )}

      <div className="history-grid">
        {chats.map((chat) => (
          <div className="history-card" key={chat.chat_id} onClick={() => open(chat.chat_id)}>
            <div className="hcard-top">
              <span className="hcard-title">{chat.title || 'Research conversation'}</span>
              {chat.mode && (
                <span className="hcard-mode">{MODE_LABELS[chat.mode] || chat.mode}</span>
              )}
            </div>
            <div className="hcard-meta">
              <span>{chat.turn_count || 0} turns · {chat.document_ids?.length || 0} docs</span>
              <span>{chat.updated_at ? new Date(chat.updated_at * 1000).toLocaleDateString() : ''}</span>
            </div>
            {chat.model_ids?.length > 0 && (
              <div className="hcard-models">
                {chat.model_ids.map((id) => (
                  <span className="hcard-model-tag" key={id}>{id.split('.').pop()}</span>
                ))}
              </div>
            )}
            <button
              className="hcard-open-btn"
              onClick={(e) => { e.stopPropagation(); open(chat.chat_id); }}
            >
              Open conversation →
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
