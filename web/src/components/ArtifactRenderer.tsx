import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { Highlight, themes } from "prism-react-renderer";

interface Props {
  content: string;
  isStreaming?: boolean;
}

function detectLanguage(className?: string): string {
  if (!className) return "text";
  const match = /language-(\w+)/.exec(className);
  return match?.[1] ?? "text";
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore
    }
  };

  return (
    <div style={styles.codeWrap}>
      <div style={styles.codeHeader}>
        <span style={styles.langLabel}>{language}</span>
        <button type="button" style={styles.copyBtn} onClick={handleCopy}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <Highlight theme={themes.vsDark} code={code} language={language}>
        {({ className, style, tokens, getLineProps, getTokenProps }) => (
          <pre className={className} style={{ ...style, ...styles.pre }}>
            <code>
              {tokens.map((line, i) => (
                <div key={i} {...getLineProps({ line })}>
                  {line.map((token, key) => (
                    <span key={key} {...getTokenProps({ token })} />
                  ))}
                </div>
              ))}
            </code>
          </pre>
        )}
      </Highlight>
    </div>
  );
}

export default function ArtifactRenderer({ content, isStreaming }: Props) {
  return (
    <div style={styles.root}>
      <ReactMarkdown
        components={{
          code({ className, children, ...props }) {
            const text = String(children).replace(/\n$/, "");
            const isBlock = className || text.includes("\n");
            if (!isBlock) {
              return (
                <code style={styles.inlineCode} {...props}>
                  {children}
                </code>
              );
            }
            const lang = detectLanguage(className);
            return <CodeBlock code={text} language={lang} />;
          },
          pre({ children }) {
            return <>{children}</>;
          },
          a({ href, children }) {
            return (
              <a href={href} style={styles.link} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
          p({ children }) {
            return <p style={styles.paragraph}>{children}</p>;
          },
          ul({ children }) {
            return <ul style={styles.list}>{children}</ul>;
          },
          ol({ children }) {
            return <ol style={styles.list}>{children}</ol>;
          },
          h1({ children }) {
            return <h1 style={styles.h1}>{children}</h1>;
          },
          h2({ children }) {
            return <h2 style={styles.h2}>{children}</h2>;
          },
          h3({ children }) {
            return <h3 style={styles.h3}>{children}</h3>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
      {isStreaming && <span style={styles.cursor}>▋</span>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    fontSize: 14,
    lineHeight: 1.65,
    wordBreak: "break-word",
    color: "var(--text-primary)",
  },
  paragraph: {
    margin: "0 0 0.6em",
  },
  list: {
    margin: "0 0 0.6em",
    paddingLeft: "1.4em",
  },
  h1: { fontSize: 20, fontWeight: 700, margin: "0.6em 0 0.4em" },
  h2: { fontSize: 17, fontWeight: 600, margin: "0.6em 0 0.4em" },
  h3: { fontSize: 15, fontWeight: 600, margin: "0.5em 0 0.3em" },
  link: {
    color: "var(--accent)",
    textDecoration: "underline",
  },
  inlineCode: {
    fontFamily: "var(--font-mono)",
    fontSize: "0.9em",
    background: "var(--bg-hover)",
    padding: "1px 5px",
    borderRadius: "var(--radius-sm)",
  },
  codeWrap: {
    margin: "0.5em 0",
    borderRadius: "var(--radius-md)",
    overflow: "hidden",
    border: "1px solid var(--border)",
  },
  codeHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "4px 10px",
    background: "var(--bg-sidebar)",
    borderBottom: "1px solid var(--border)",
    fontSize: 11,
  },
  langLabel: {
    color: "var(--text-dim)",
    fontFamily: "var(--font-mono)",
    textTransform: "lowercase",
  },
  copyBtn: {
    padding: "2px 8px",
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-hover)",
    color: "var(--text-muted)",
    fontSize: 11,
  },
  pre: {
    margin: 0,
    padding: "10px 12px",
    fontSize: 12,
    fontFamily: "var(--font-mono)",
    overflowX: "auto",
    background: "#1e1e1e",
  },
  cursor: {
    display: "inline-block",
    color: "var(--accent)",
    animation: "blink 1s step-end infinite",
    marginLeft: 1,
  },
};
