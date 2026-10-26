import React, { useCallback, useEffect, useState } from 'react';
import { llmFetch } from '../llmApi';

export default function HistoryOfLLMChat({
  apiUrl,
  onSelectChat,
  refreshToken,
}) {
  const [chats, setChats] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const loadChats = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await llmFetch(apiUrl, 'chats');
      setChats(data.chats || []);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, [apiUrl]);

  useEffect(() => { loadChats(); }, [loadChats, refreshToken]);

  const openChat = async (chatId) => {
    setError('');
    try {
      const data = await llmFetch(apiUrl, `chats/${encodeURIComponent(chatId)}`);
      onSelectChat(data);
    } catch (loadError) {
      setError(loadError.message);
    }
  };

  return (
    <aside className="llm-history">
      <div className="llm-history-heading">
        <h3>History of LLM Chat</h3>
        <button type="button" onClick={loadChats} disabled={loading}>Refresh</button>
      </div>
      {error && <p className="llm-error" role="alert">{error}</p>}
      {loading && <p>Loading conversations…</p>}
      {!loading && chats.length === 0 && <p>No saved conversations yet.</p>}
      <ul>
        {chats.map((chat) => (
          <li key={chat.chat_id}>
            <button type="button" onClick={() => openChat(chat.chat_id)}>
              <strong>{chat.title || 'Research conversation'}</strong>
              <span>{chat.turn_count || 0} turns · {chat.document_ids?.length || 0} documents</span>
              {chat.model_ids?.length > 0 && (
                <span className="llm-history-model-tags">
                  {chat.model_ids.map((modelId) => (
                    <em key={modelId}>{modelId}</em>
                  ))}
                </span>
              )}
              <time dateTime={chat.updated_at}>
                {new Date(chat.updated_at).toLocaleString()}
              </time>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
