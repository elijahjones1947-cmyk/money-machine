const THEMES = [
  { id: 'neon', label: 'Neon' },
  { id: 'soft', label: 'Soft' },
];

export function ThemeSwitcher({ theme, onChange }) {
  return (
    <div style={{ display: 'flex', gap: 6 }}>
      {THEMES.map((t) => (
        <button
          key={t.id}
          className={`button ${theme === t.id ? 'button-accent' : ''}`}
          onClick={() => onChange(t.id)}
          style={{ padding: '6px 10px', fontSize: 12 }}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
