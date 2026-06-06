import { useCallback, useState } from "react";
import ProjectSidebar from "./components/ProjectSidebar";
import SourcesPanel from "./components/SourcesPanel";
import ChatView from "./components/ChatView";
import InstructionsPanel from "./components/InstructionsPanel";
import SettingsModal from "./components/SettingsModal";
import StatusBar from "./components/StatusBar";

export default function App() {
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [sourcesVersion, setSourcesVersion] = useState(0);
  const [threadsVersion, setThreadsVersion] = useState(0);

  const notifySourcesChanged = useCallback(
    () => setSourcesVersion((v) => v + 1),
    [],
  );
  const notifyThreadsChanged = useCallback(
    () => setThreadsVersion((v) => v + 1),
    [],
  );

  const handleSelectProject = (id: string) => {
    setSelectedProject(id);
    setThreadId(null);
  };

  return (
    <div style={styles.app}>
      <div style={styles.main}>
        <div style={styles.left}>
          <ProjectSidebar
            selectedId={selectedProject}
            onSelect={handleSelectProject}
            threadId={threadId}
            onThreadSelect={setThreadId}
            onThreadsChange={notifyThreadsChanged}
            threadsVersion={threadsVersion}
          />
          <SourcesPanel
            projectId={selectedProject}
            onSourcesChange={notifySourcesChanged}
          />
        </div>

        <div style={styles.center}>
          <ChatView
            projectId={selectedProject}
            threadId={threadId}
            sourcesVersion={sourcesVersion}
            threadsVersion={threadsVersion}
            onThreadsChange={notifyThreadsChanged}
          />
        </div>

        <div style={styles.right}>
          <InstructionsPanel projectId={selectedProject} />
          <div style={styles.settingsFooter}>
            <button
              style={styles.settingsBtn}
              onClick={() => setShowSettings(true)}
              title="Settings"
            >
              ⚙ Settings
            </button>
          </div>
        </div>
      </div>

      <StatusBar />

      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  app: {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    background: "var(--bg-app)",
    overflow: "hidden",
  },
  main: {
    display: "flex",
    flex: 1,
    overflow: "hidden",
  },
  left: {
    width: 280,
    flexShrink: 0,
    minWidth: 240,
    background: "var(--bg-sidebar)",
    borderRight: "1px solid var(--border)",
    display: "flex",
    flexDirection: "column",
    minHeight: 0,
    overflow: "hidden",
  },
  center: {
    flex: 1,
    background: "var(--bg-panel)",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
    borderRight: "1px solid var(--border)",
  },
  right: {
    width: 300,
    flexShrink: 0,
    background: "var(--bg-panel)",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  settingsFooter: {
    flexShrink: 0,
    padding: "10px 12px",
    borderTop: "1px solid var(--border)",
  },
  settingsBtn: {
    width: "100%",
    padding: "8px 0",
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-hover)",
    color: "var(--text-muted)",
    fontSize: 12,
    fontWeight: 500,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
  },
};
