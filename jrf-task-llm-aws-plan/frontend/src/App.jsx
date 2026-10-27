import React, { useState } from 'react';
import './App.css';
import ChatTab from './components/ChatTab';
import HistoryTab from './components/HistoryTab';

export default function App() {
  const [tab, setTab] = useState('chat');
  const [restoredChat, setRestoredChat] = useState(null);

  const openFromHistory = (record) => {
    setRestoredChat(record);
    setTab('chat');
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark">B</div>
        <div>
          <p className="eyebrow">BREATHE · WP1 · DOCUMENT AI</p>
          <h1>Archive Chat</h1>
        </div>
        <nav className="tab-nav">
          <button className={tab === 'chat' ? 'tab active' : 'tab'} onClick={() => setTab('chat')}>
            Chat with AI
          </button>
          <button className={tab === 'history' ? 'tab active' : 'tab'} onClick={() => setTab('history')}>
            Chat History
          </button>
        </nav>
        <a
          className="back-link"
          href={import.meta.env.VITE_NLP_PIPELINE_URL || '#'}
          title="Back to NLP Pipeline"
          target="_top"
          rel="noreferrer"
        >
          ← NLP Pipeline
        </a>
        <span className="service-status"><span /> Bedrock ready</span>
      </header>

      {tab === 'chat' && (
        <ChatTab restoredChat={restoredChat} onRestoreConsumed={() => setRestoredChat(null)} />
      )}
      {tab === 'history' && <HistoryTab onOpenChat={openFromHistory} />}
    </main>
  );
}
