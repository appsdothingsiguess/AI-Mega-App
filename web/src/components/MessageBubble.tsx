interface Props {
  role: "user" | "assistant" | "system";
  content: string;
  createdAt?: string;
}

export default function MessageBubble({ role, content, createdAt }: Props) {
  if (role === "system") return null;

  const time = createdAt
    ? new Date(createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;

  if (role === "user") {
    return (
      <div style={styles.userRow}>
        <div style={styles.userBubble}>
          <div style={styles.userContent}>{content}</div>
          {time && <div style={styles.timeUser}>{time}</div>}
        </div>
      </div>
    );
  }

  return (
    <div style={styles.assistantRow}>
      <div style={styles.assistantDot}>•</div>
      <div style={styles.assistantBody}>
        <div style={styles.assistantContent}>{formatAssistant(content)}</div>
        {time && <div style={styles.timeAssistant}>{time}</div>}
      </div>
    </div>
  );
}

/** Render line breaks; no heavy markdown parsing. */
function formatAssistant(text: string): React.ReactNode {
  return text.split("\n").map((line, i) => (
    <span key={i}>
      {line}
      {i < text.split("\n").length - 1 && <br />}
    </span>
  ));
}

const styles: Record<string, React.CSSProperties> = {
  userRow: {
    display: "flex",
    justifyContent: "flex-end",
    marginBottom: 12,
    paddingLeft: "20%",
  },
  userBubble: {
    background: "var(--user-bubble)",
    border: "1px solid var(--user-bubble-border)",
    borderRadius: "var(--radius-md)",
    padding: "10px 14px",
    maxWidth: "100%",
  },
  userContent: {
    fontSize: 14,
    lineHeight: 1.55,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
  timeUser: {
    marginTop: 4,
    fontSize: 10,
    color: "rgba(232,232,232,0.4)",
    textAlign: "right",
  },
  assistantRow: {
    display: "flex",
    alignItems: "flex-start",
    gap: 10,
    marginBottom: 16,
    paddingRight: "10%",
  },
  assistantDot: {
    flexShrink: 0,
    color: "var(--accent)",
    fontSize: 18,
    lineHeight: "1.55",
    marginTop: 2,
  },
  assistantBody: {
    flex: 1,
  },
  assistantContent: {
    fontSize: 14,
    lineHeight: 1.65,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    color: "var(--text-primary)",
  },
  timeAssistant: {
    marginTop: 4,
    fontSize: 10,
    color: "var(--text-dim)",
  },
};
