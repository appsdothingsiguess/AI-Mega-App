interface Props {
  question: string;
  options: string[];
  onAnswer: (option: string) => void;
  disabled?: boolean;
}

export default function AskUserPrompt({ question, options, onAnswer, disabled }: Props) {
  if (options.length === 0) return null;

  return (
    <div style={styles.wrap}>
      <div style={styles.question}>{question}</div>
      <div style={styles.options}>
        {options.map((option) => (
          <button
            key={option}
            type="button"
            style={{
              ...styles.optionBtn,
              ...(disabled ? styles.optionBtnDisabled : {}),
            }}
            onClick={() => onAnswer(option)}
            disabled={disabled}
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    marginBottom: 8,
    padding: "10px 12px",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-sidebar)",
  },
  question: {
    fontSize: 13,
    lineHeight: 1.5,
    marginBottom: 10,
    color: "var(--text)",
  },
  options: {
    display: "flex",
    flexWrap: "wrap",
    gap: 8,
  },
  optionBtn: {
    padding: "6px 12px",
    fontSize: 12,
    background: "var(--bg-hover)",
    color: "var(--accent)",
    borderRadius: "var(--radius-sm)",
    transition: "opacity var(--transition)",
  },
  optionBtnDisabled: {
    opacity: 0.5,
    cursor: "not-allowed",
  },
};
