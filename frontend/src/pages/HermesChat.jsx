import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import './HermesChat.css';

export function HermesChat() {
  const [messages, setMessages] = useState([]); // {role, text}
  const [pendingAction, setPendingAction] = useState(null);
  const [configured, setConfigured] = useState(true);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const bottomRef = useRef(null);

  useEffect(() => {
    api
      .hermesHistory()
      .then((r) => {
        setMessages(r.transcript || []);
        setPendingAction(r.pending_action || null);
        setConfigured(r.configured !== false);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, pendingAction]);

  const send = async (e) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    setError(null);
    setMessages((m) => [...m, { role: 'user', text }]);
    setSending(true);
    try {
      const r = await api.hermesChat(text);
      setMessages((m) => [...m, { role: 'assistant', text: r.reply }]);
      setPendingAction(r.pending_action || null);
    } catch (err) {
      setError(err.message);
    } finally {
      setSending(false);
    }
  };

  const respondToPending = async (approve) => {
    setSending(true);
    setError(null);
    try {
      const r = await api.hermesConfirm(approve);
      setMessages((m) => [...m, { role: 'assistant', text: r.reply }]);
      setPendingAction(r.pending_action || null);
    } catch (err) {
      setError(err.message);
    } finally {
      setSending(false);
    }
  };

  if (!configured) {
    return (
      <div>
        <h2>Hermes</h2>
        <div className="hermes-not-configured">
          Hermes isn't configured yet — set <code>ANTHROPIC_API_KEY</code> in Railway's environment
          variables and redeploy to enable chat.
        </div>
      </div>
    );
  }

  return (
    <div className="hermes-page">
      <h2>Hermes</h2>
      <div className="hermes-messages">
        {messages.length === 0 && (
          <div className="empty-state">Ask about your portfolio, positions, risk state, or regime.</div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`hermes-message ${m.role}`}>{m.text}</div>
        ))}
        {pendingAction && (
          <div className="hermes-pending">
            <div>
              <strong>Confirm:</strong> {pendingAction.tool_name}
              {Object.keys(pendingAction.tool_input || {}).length > 0 && (
                <div style={{ fontSize: 12, marginTop: 4 }}>
                  {JSON.stringify(pendingAction.tool_input)}
                </div>
              )}
            </div>
            <div className="hermes-pending-actions">
              <button className="button button-accent" disabled={sending} onClick={() => respondToPending(true)}>
                Confirm
              </button>
              <button className="button" disabled={sending} onClick={() => respondToPending(false)}>
                Cancel
              </button>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      {error && <div className="error-text">{error}</div>}
      <form className="hermes-input-row" onSubmit={send}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={pendingAction ? 'Resolve the pending action above first…' : 'Ask Hermes…'}
          disabled={sending || !!pendingAction}
        />
        <button className="button button-accent" type="submit" disabled={sending || !!pendingAction}>
          Send
        </button>
      </form>
    </div>
  );
}
