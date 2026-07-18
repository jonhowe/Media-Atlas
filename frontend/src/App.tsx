import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api, apiText, exportUrl, setCsrfToken } from "./api";
import type {
  AdminStatus,
  ApplicationLogEntry,
  ApplicationLogPage,
  AuthStatus,
  Health,
  MediaFile,
  MediaRoot,
  PlexLibrary,
  PlexPathMapping,
  PlexSettings,
  PlexStatus,
  PlexSyncJob,
  RetentionAction,
  RetentionAnalysisJob,
  RetentionCandidate,
  RetentionCandidatePage,
  RetentionConnection,
  RetentionPathMapping,
  RetentionSettings,
  RetentionSummary,
  ScanJob,
  Summary,
  TranscodePlan,
  TranscodePlanItem,
  TranscodeProfile,
  TranscodeRun,
  TranscodeRunItem,
  TranscodeSavingsStats
} from "./types";

type Page =
  | "dashboard"
  | "directories"
  | "scans"
  | "library"
  | "retention"
  | "candidates"
  | "reports"
  | "planner"
  | "runs"
  | "logs"
  | "status"
  | "settings";

type PlannerCategory = "Easy Win" | "Remux Only" | "Review";
type CandidateCategory = PlannerCategory | "Already Modern" | "Error";
type LogTab = "application" | "transcodes" | "scans";
type LogTarget = {
  tab: LogTab;
  runId?: number;
  itemId?: number;
  scanId?: number;
};

const plannerCategories: PlannerCategory[] = ["Easy Win", "Remux Only", "Review"];
const candidateCategories: CandidateCategory[] = [...plannerCategories, "Already Modern", "Error"];

const nav: Array<[Page, string]> = [
  ["dashboard", "Dashboard"],
  ["library", "Library"],
  ["retention", "Retention"],
  ["candidates", "Quality Candidates"],
  ["runs", "Runs"],
  ["directories", "Directories"],
  ["scans", "Scans"],
  ["reports", "Reports"],
  ["planner", "Transcode Planner"],
  ["logs", "Logs"],
  ["status", "Admin Status"],
  ["settings", "Settings"]
];

const mobileQuickNav: Array<[Page, string]> = [
  ["dashboard", "Dashboard"],
  ["library", "Library"],
  ["retention", "Retention"],
  ["runs", "Runs"]
];

const FIRST_RUN_SETUP_KEY = "media-atlas:first-run-setup-dismissed";
const THEME_STORAGE_KEY = "media-atlas:theme";
const TRANSCODE_PROFILES_URL = "https://github.com/jonhowe/Media-Atlas/blob/main/docs/TRANSCODE_PROFILES.md";

type Theme = "light" | "dark";

function getInitialTheme(): Theme {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

function SunIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.4 1.4M17.6 17.6 19 19M19 5l-1.4 1.4M6.4 17.6 5 19" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M21 14.5A8.5 8.5 0 0 1 9.5 3 7 7 0 1 0 21 14.5Z" />
    </svg>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");
  const [toast, setToast] = useState<string>("");
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [showFirstRunSetup, setShowFirstRunSetup] = useState(false);
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [plannerCategory, setPlannerCategory] = useState<PlannerCategory>("Easy Win");
  const [logTarget, setLogTarget] = useState<LogTarget>({ tab: "application" });

  useEffect(() => {
    refreshAuth();
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Theme still applies for this session when storage is unavailable.
    }
  }, [theme]);

  useEffect(() => {
    if (!auth?.authenticated || !auth.configured) return;
    let dismissed = false;
    try {
      dismissed = window.localStorage.getItem(FIRST_RUN_SETUP_KEY) === "true";
    } catch {
      dismissed = false;
    }
    if (dismissed) return;
    api<PlexSettings>("/api/plex/settings")
      .then((settings) => {
        const configured = Boolean(settings.server_url && settings.token_configured);
        setShowFirstRunSetup(!configured);
      })
      .catch(() => setShowFirstRunSetup(false));
  }, [auth]);

  async function refreshAuth() {
    try {
      const nextAuth = await api<AuthStatus>("/api/auth/me");
      setCsrfToken(nextAuth.csrf_token);
      setAuth(nextAuth);
    } catch {
      setCsrfToken(null);
      setAuth({ mode: "disabled", authenticated: true, configured: true, username: "local" });
    }
  }

  async function logout() {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
    setCsrfToken(null);
    await refreshAuth();
  }

  function dismissFirstRunSetup() {
    try {
      window.localStorage.setItem(FIRST_RUN_SETUP_KEY, "true");
    } catch {
      // Ignore storage failures; dismissal still works for this session.
    }
    setShowFirstRunSetup(false);
  }

  function toggleTheme() {
    setTheme((current) => current === "dark" ? "light" : "dark");
  }

  function changePage(nextPage: Page) {
    setPage(nextPage);
    setMobileNavOpen(false);
  }

  function openPlanner(category: PlannerCategory) {
    setPlannerCategory(category);
    changePage("planner");
  }

  function openLogs(target: LogTarget) {
    setLogTarget(target);
    changePage("logs");
  }

  const currentPageLabel = showFirstRunSetup ? "First-run setup" : nav.find(([key]) => key === page)?.[1];

  if (!auth) {
    return <Splash message="Checking access" />;
  }

  if (auth.mode !== "disabled" && !auth.authenticated) {
    return <LoginScreen auth={auth} onAuthenticated={refreshAuth} />;
  }

  return (
    <div className={`app ${mobileNavOpen ? "navOpen" : ""}`}>
      <button
        className="mobileNavBackdrop"
        type="button"
        aria-label="Close navigation"
        onClick={() => setMobileNavOpen(false)}
      />
      <aside className="sidebar" aria-label="Primary navigation">
        <div className="brand">
          <img className="brandLogo" src="/media-atlas-logo.svg" alt="" />
          <div>
            <strong>Media Atlas</strong>
            <small>Local inventory and transcodes</small>
          </div>
        </div>
        <nav>
          {nav.map(([key, label]) => (
            <button
              key={key}
              className={page === key ? "active" : ""}
              onClick={() => changePage(key)}
            >
              {label}
            </button>
          ))}
        </nav>
      </aside>
      <main>
        <header className="topbar">
          <div className="mobileTopbar">
            <button
              className="mobileMenuButton"
              type="button"
              aria-label="Open navigation"
              aria-expanded={mobileNavOpen}
              onClick={() => setMobileNavOpen(true)}
            >
              <span />
              <span />
              <span />
            </button>
            <div className="mobileBrand">
              <img className="brandLogo" src="/media-atlas-logo.svg" alt="" />
              <strong>Media Atlas</strong>
            </div>
          </div>
          <div className="pageHeading">
            <h1>{currentPageLabel}</h1>
            <p>{showFirstRunSetup ? "Configure optional Plex enrichment before your first sync." : "Scan, understand, plan, and safely run staged media conversions."}</p>
          </div>
          <div className="topbarActions">
            {toast && <div className="toast">{toast}</div>}
            <button
              className="themeToggle"
              type="button"
              aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              aria-pressed={theme === "dark"}
              title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              onClick={toggleTheme}
            >
              {theme === "dark" ? <SunIcon /> : <MoonIcon />}
            </button>
            {auth.mode !== "disabled" && <button onClick={logout}>Log out</button>}
          </div>
        </header>
        {showFirstRunSetup ? (
          <FirstRunSetup onToast={setToast} onDone={dismissFirstRunSetup} />
        ) : (
          <>
            {page === "dashboard" && <Dashboard onToast={setToast} />}
            {page === "directories" && <Directories onToast={setToast} />}
            {page === "scans" && <Scans onToast={setToast} onOpenLog={(scanId) => openLogs({ tab: "scans", scanId })} />}
            {page === "library" && <Library onToast={setToast} />}
            {page === "retention" && <Retention onToast={setToast} />}
            {page === "candidates" && <Candidates onToast={setToast} onPlanCategory={openPlanner} />}
            {page === "reports" && <Reports />}
            {page === "planner" && <Planner onToast={setToast} switchToRuns={() => setPage("runs")} initialCategory={plannerCategory} />}
            {page === "runs" && <Runs onToast={setToast} onOpenLog={(runId, itemId) => openLogs({ tab: "transcodes", runId, itemId })} />}
            {page === "logs" && <Logs onToast={setToast} initialTarget={logTarget} />}
            {page === "status" && <AdminStatusPage onToast={setToast} />}
            {page === "settings" && <Settings onToast={setToast} />}
          </>
        )}
        {!showFirstRunSetup && (
          <nav className="mobileBottomNav" aria-label="Quick navigation">
            {mobileQuickNav.map(([key, label]) => (
              <button
                key={key}
                className={page === key ? "active" : ""}
                type="button"
                onClick={() => changePage(key)}
              >
                {label}
              </button>
            ))}
            <button type="button" onClick={() => setMobileNavOpen(true)}>More</button>
          </nav>
        )}
      </main>
    </div>
  );
}

function Splash({ message }: { message: string }) {
  return <div className="splash"><strong>Media Atlas</strong><span>{message}</span></div>;
}

function LoginScreen({ auth, onAuthenticated }: { auth: AuthStatus; onAuthenticated: () => Promise<void> }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password })
      });
      await onAuthenticated();
    } catch (nextError) {
      setError(String(nextError));
    }
  }

  return (
    <div className="loginShell">
      <form className="loginPanel" onSubmit={submit}>
        <div className="brand">
          <img className="brandLogo" src="/media-atlas-logo.svg" alt="" />
          <div>
            <strong>Media Atlas</strong>
            <small>{auth.mode === "reverse_proxy_trusted" ? "Waiting for trusted proxy identity" : "Admin sign in"}</small>
          </div>
        </div>
        {auth.mode === "single_admin" ? (
          <>
            {!auth.configured && <Badge tone="bad">Admin password is not configured</Badge>}
            <label>
              Username
              <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
            </label>
            <label>
              Password
              <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" />
            </label>
            <button className="primary" disabled={!auth.configured}>Sign in</button>
          </>
        ) : (
          <p className="muted">The reverse proxy did not provide an authenticated user header.</p>
        )}
        {error && <div className="toast">{error}</div>}
      </form>
    </div>
  );
}

function FirstRunSetup({ onToast, onDone }: { onToast: (message: string) => void; onDone: () => void }) {
  const [plex, setPlex] = useState<PlexSettings | null>(null);
  const [token, setToken] = useState("");

  useEffect(() => {
    api<PlexSettings>("/api/plex/settings")
      .then((settings) => {
        setPlex({
          ...settings,
          enabled: true,
          path_mappings: settings.path_mappings.length
            ? settings.path_mappings
            : [{ plex_path_prefix: "", media_atlas_path_prefix: "/media" }]
        });
      })
      .catch((error) => onToast(String(error)));
  }, [onToast]);

  function updatePlex(next: Partial<PlexSettings>) {
    setPlex((current) => current ? { ...current, ...next } : current);
  }

  function updateMapping(index: number, key: keyof PlexPathMapping, value: string) {
    if (!plex) return;
    const mappings = [...plex.path_mappings];
    mappings[index] = { ...mappings[index], [key]: value };
    updatePlex({ path_mappings: mappings });
  }

  function addMapping() {
    if (!plex) return;
    updatePlex({ path_mappings: [...plex.path_mappings, { plex_path_prefix: "", media_atlas_path_prefix: "/media" }] });
  }

  function removeMapping(index: number) {
    if (!plex) return;
    updatePlex({ path_mappings: plex.path_mappings.filter((_, current) => current !== index) });
  }

  async function save(continueAfterSave = true) {
    if (!plex) return;
    try {
      const payload: Record<string, unknown> = {
        enabled: plex.enabled,
        server_url: plex.server_url,
        selected_library_keys: plex.selected_library_keys,
        timeout_seconds: plex.timeout_seconds,
        path_mappings: plex.path_mappings
      };
      if (token) payload.token = token;
      await api<PlexSettings>("/api/plex/settings", {
        method: "PUT",
        body: JSON.stringify(payload)
      });
      onToast("Plex setup saved.");
      if (continueAfterSave) onDone();
      return true;
    } catch (error) {
      onToast(String(error));
      return false;
    }
  }

  async function test() {
    try {
      const saved = await save(false);
      if (!saved) return;
      const result = await api<{ library_count: number }>("/api/plex/test-connection", { method: "POST", body: "{}" });
      onToast(`Connected to Plex. Found ${result.library_count} libraries.`);
    } catch (error) {
      onToast(String(error));
    }
  }

  if (!plex) return <Splash message="Loading setup" />;

  return (
    <section className="stack setupPage">
      <Panel title="Plex enrichment">
        <div className="stack">
          <p className="muted">
            Plex is optional. Configure it now to enrich library rows with title, library, collection, genre, watched state, and match status. You can change these settings later.
          </p>
          <div className="formGrid plexSetupGrid">
            <label>
              Enabled
              <select value={plex.enabled ? "true" : "false"} onChange={(event) => updatePlex({ enabled: event.target.value === "true" })}>
                <option value="true">Enabled</option>
                <option value="false">Disabled</option>
              </select>
            </label>
            <label>
              Server URL
              <input value={plex.server_url || ""} onChange={(event) => updatePlex({ server_url: event.target.value })} placeholder="http://192.168.1.106:32400" />
            </label>
            <label>
              Token {plex.token_configured && <span className="muted">{plex.token_hint}</span>}
              <input value={token} onChange={(event) => setToken(event.target.value)} placeholder={plex.token_configured ? "Leave blank to keep current token" : "Plex token"} />
            </label>
            <label>
              Timeout seconds
              <input type="number" min="1" max="120" value={plex.timeout_seconds} onChange={(event) => updatePlex({ timeout_seconds: Number(event.target.value) || 10 })} />
            </label>
          </div>
          <div>
            <h3>Path mappings</h3>
            <p className="muted">Map Plex file paths to paths Media Atlas can see. In Docker this usually maps a Plex host path such as `/mnt/media` to the container path `/media`.</p>
            <div className="mappingList">
              {plex.path_mappings.map((mapping, index) => (
                <div className="mappingRow" key={index}>
                  <input value={mapping.plex_path_prefix} onChange={(event) => updateMapping(index, "plex_path_prefix", event.target.value)} placeholder="/mnt/media" />
                  <input value={mapping.media_atlas_path_prefix} onChange={(event) => updateMapping(index, "media_atlas_path_prefix", event.target.value)} placeholder="/media" />
                  <button className="danger" onClick={() => removeMapping(index)}>Remove</button>
                </div>
              ))}
              <button onClick={addMapping}>Add mapping</button>
            </div>
          </div>
          <div className="rowActions">
            <button className="primary" onClick={() => save(true)}>Save and continue</button>
            <button onClick={test}>Save and test</button>
            <button onClick={onDone}>Skip for now</button>
          </div>
        </div>
      </Panel>
    </section>
  );
}

function Dashboard({ onToast }: { onToast: (message: string) => void }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [runs, setRuns] = useState<TranscodeRun[]>([]);
  const [scans, setScans] = useState<ScanJob[]>([]);
  const [savings, setSavings] = useState<TranscodeSavingsStats | null>(null);
  const [retention, setRetention] = useState<RetentionSummary | null>(null);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, []);

  async function refresh() {
    try {
      const [nextHealth, nextSummary, nextRuns, nextScans, nextSavings, nextRetention] = await Promise.all([
        api<Health>("/api/health"),
        api<Summary>("/api/reports/summary"),
        api<TranscodeRun[]>("/api/transcode-runs?limit=5&include_archived=true"),
        api<ScanJob[]>("/api/scans?limit=5"),
        api<TranscodeSavingsStats>("/api/transcode-runs/stats"),
        api<RetentionSummary>("/api/retention/summary")
      ]);
      setHealth(nextHealth);
      setSummary(nextSummary);
      setRuns(nextRuns);
      setScans(nextScans);
      setSavings(nextSavings);
      setRetention(nextRetention);
    } catch (error) {
      onToast(String(error));
    }
  }

  return (
    <section className="stack">
      <div className="metrics">
        <Metric label="Files" value={summary?.total_files ?? 0} />
        <Metric label="Storage" value={formatBytes(summary?.total_size_bytes ?? 0)} />
        <Metric label="Duration" value={formatDuration(summary?.total_duration_seconds ?? 0)} />
        <Metric label="System" value={health?.status ?? "checking"} />
      </div>
      <div className="grid two">
        <Panel title="Recommendation Mix">
          <ReportTable rows={summary?.by_recommendation ?? []} />
        </Panel>
        <Panel title="Tool Availability">
          <div className="statusGrid">
            <Status label="Database" ok={Boolean(health?.database_available)} />
            <Status label="ffprobe" ok={Boolean(health?.ffprobe_available)} />
            <Status label="ffmpeg" ok={Boolean(health?.ffmpeg_available)} />
          </div>
        </Panel>
        <Panel title="Plex Enrichment">
          <PlexStatusPanel status={summary?.plex} />
        </Panel>
        <Panel title="Transcode Savings">
          <TranscodeSavingsPanel stats={savings} />
        </Panel>
        <Panel title="Retention Review">
          <div className="statusGrid">
            <div className="scanStats">
              <span><strong>{retention?.candidate_count ?? 0}</strong> deletion candidates</span>
              <span><strong>{retention?.diagnostic_count ?? 0}</strong> mapping diagnostics</span>
              <span><strong>{formatBytes(retention?.total_size_bytes ?? 0)}</strong> reclaimable</span>
              <span><strong>{retention?.latest_analysis?.warnings.length ?? 0}</strong> source warnings</span>
            </div>
            <span className="muted">
              Latest analysis {formatDateTime(retention?.latest_analysis?.finished_at || retention?.latest_analysis?.created_at)}
            </span>
          </div>
        </Panel>
        <Panel title="Recent Transcode Runs">
          <RecentTranscodeRuns runs={runs} />
        </Panel>
        <Panel title="Recent Scans">
          <RecentScans scans={scans} />
        </Panel>
      </div>
    </section>
  );
}

function Directories({ onToast }: { onToast: (message: string) => void }) {
  const [roots, setRoots] = useState<MediaRoot[]>([]);
  const [name, setName] = useState("Movies");
  const [path, setPath] = useState("");
  const [browsePath, setBrowsePath] = useState("");
  const [browser, setBrowser] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    refresh();
  }, []);

  async function refresh() {
    setRoots(await api<MediaRoot[]>("/api/roots"));
  }

  async function addRoot() {
    try {
      await api("/api/roots", {
        method: "POST",
        body: JSON.stringify({ name, path, enabled: true })
      });
      setPath("");
      await refresh();
      onToast("Directory added.");
    } catch (error) {
      onToast(String(error));
    }
  }

  async function toggle(root: MediaRoot) {
    await api(`/api/roots/${root.id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !root.enabled })
    });
    await refresh();
  }

  async function remove(root: MediaRoot) {
    if (!window.confirm(`Remove ${root.name} from Media Atlas? Inventory rows for this root are removed, but media files are untouched.`)) {
      return;
    }
    await api(`/api/roots/${root.id}`, { method: "DELETE" });
    await refresh();
  }

  async function browse(nextPath?: string) {
    try {
      const url = nextPath || browsePath ? `/api/directory-browser?path=${encodeURIComponent(nextPath || browsePath)}` : "/api/directory-browser";
      const result = await api<Record<string, any>>(url);
      setBrowser(result);
      setBrowsePath(result.path);
    } catch (error) {
      onToast(String(error));
    }
  }

  return (
    <section className="stack">
      <Panel title="Add Media Root">
        <div className="formGrid">
          <label>
            Name
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label>
            Path
            <input value={path} onChange={(event) => setPath(event.target.value)} placeholder="/Volumes/Media/Movies" />
          </label>
          <button onClick={addRoot}>Add root</button>
        </div>
      </Panel>
      <Panel title="Directory Browser">
        <div className="toolbar">
          <input value={browsePath} onChange={(event) => setBrowsePath(event.target.value)} placeholder="Browse from an allowed path" />
          <button onClick={() => browse()}>Browse</button>
          {browser?.parent && <button onClick={() => browse(browser.parent)}>Up</button>}
          {browser?.path && <button onClick={() => setPath(browser.path)}>Use current path</button>}
        </div>
        {browser && (
          <div className="directoryList">
            {browser.directories.map((item: any) => (
              <button key={item.path} onClick={() => browse(item.path)}>
                {item.name}
                {!item.readable && <span className="muted"> unreadable</span>}
              </button>
            ))}
          </div>
        )}
      </Panel>
      <Panel title="Configured Roots">
        <table className="directoriesTable mobileStackTable">
          <thead>
            <tr>
              <th>Name</th>
              <th>Path</th>
              <th>Status</th>
              <th>Last scanned</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {roots.map((root) => (
              <tr key={root.id}>
                <td className="mobileStackPrimary"><strong>{root.name}</strong></td>
                <td className="path">
                  <span className="mobileCellLabel">Path</span>
                  {root.path}
                </td>
                <td>
                  <span className="mobileCellLabel">Status</span>
                  <Badge tone={root.enabled ? "good" : "muted"}>{root.enabled ? "Enabled" : "Disabled"}</Badge>
                </td>
                <td>
                  <span className="mobileCellLabel">Last scanned</span>
                  {root.last_scanned_at || "Never"}
                </td>
                <td className="rowActions">
                  <button onClick={() => toggle(root)}>{root.enabled ? "Disable" : "Enable"}</button>
                  <button className="danger" onClick={() => remove(root)}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </section>
  );
}

function Scans({
  onToast,
  onOpenLog
}: {
  onToast: (message: string) => void;
  onOpenLog: (scanId: number) => void;
}) {
  const [scans, setScans] = useState<ScanJob[]>([]);
  const [isStarting, setIsStarting] = useState(false);
  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => window.clearInterval(timer);
  }, []);

  async function refresh() {
    setScans(await api<ScanJob[]>("/api/scans?limit=25"));
  }

  async function start() {
    const hadActiveScan = scans.some((scan) => ["queued", "running"].includes(scan.status));
    try {
      setIsStarting(true);
      const scan = await api<ScanJob>("/api/scans", { method: "POST", body: "{}" });
      await refresh();
      onToast(hadActiveScan || scan.status === "running" ? "Scan already running." : "Scan queued.");
    } catch (error) {
      onToast(String(error));
    } finally {
      setIsStarting(false);
    }
  }

  async function cancel(id: number) {
    await api(`/api/scans/${id}/cancel`, { method: "POST", body: "{}" });
    await refresh();
  }

  async function retry(id: number) {
    await api(`/api/scans/${id}/retry`, { method: "POST", body: "{}" });
    await refresh();
    onToast("Scan retry queued.");
  }

  const hasActiveScan = scans.some((scan) => ["queued", "running"].includes(scan.status));

  return (
    <section className="stack">
      <div className="toolbar">
        <button className="primary" onClick={start} disabled={isStarting || hasActiveScan}>
          {isStarting ? "Starting..." : hasActiveScan ? "Scan running" : "Start scan"}
        </button>
      </div>
      <Panel title="Scan Jobs">
        <table className="scansTable mobileStackTable">
          <thead>
            <tr>
              <th>ID</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Files</th>
              <th>Timing</th>
              <th>Message</th>
              <th>Current file</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {scans.map((scan) => {
              const progress = scanProgress(scan);
              const timing = scanTiming(scan);
              return (
                <tr key={scan.id}>
                  <td className="mobileStackPrimary"><strong>Scan #{scan.id}</strong></td>
                  <td>
                    <span className="mobileCellLabel">Status</span>
                    <StatusBadge status={scan.status} />
                    {scan.files_failed > 0 && <div className="muted">{scan.files_failed} failed</div>}
                  </td>
                  <td>
                    <span className="mobileCellLabel">Progress</span>
                    <div className="scanProgressCell">
                      <div className="progressHeader">
                        <strong>{progress.percent}%</strong>
                        <span>{progress.completed} / {progress.total || "?"} files</span>
                      </div>
                      <Progress value={progress.percent} />
                      <div className="muted">
                        {scan.files_probed} probed, {scan.files_skipped} skipped, {scan.files_failed} failed
                      </div>
                    </div>
                  </td>
                  <td>
                    <span className="mobileCellLabel">Files</span>
                    <div className="scanStats">
                      <span><strong>{scan.total_files_discovered}</strong> discovered</span>
                      <span><strong>{progress.completed}</strong> processed</span>
                      <span><strong>{scan.files_probed}</strong> probed</span>
                      <span><strong>{scan.files_skipped}</strong> skipped</span>
                    </div>
                  </td>
                  <td>
                    <span className="mobileCellLabel">Timing</span>
                    <div className="scanTiming">
                      <span>Created {formatDateTime(scan.created_at)}</span>
                      <span>Started {formatDateTime(scan.started_at)}</span>
                      <span>{timing.label} {timing.value}</span>
                    </div>
                  </td>
                  <td>
                    <span className="mobileCellLabel">Message</span>
                    {scan.message}
                  </td>
                  <td className="path">
                    <span className="mobileCellLabel">Current file</span>
                    {scan.current_path}
                  </td>
                  <td className="rowActions">
                    <button onClick={() => onOpenLog(scan.id)}>Logs</button>
                    {["queued", "running"].includes(scan.status) && <button onClick={() => cancel(scan.id)}>Cancel</button>}
                    {["failed", "canceled", "interrupted"].includes(scan.status) && <button onClick={() => retry(scan.id)}>Retry</button>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Panel>
    </section>
  );
}

function scanProgress(scan: ScanJob) {
  const completed = scan.files_probed + scan.files_skipped + scan.files_failed;
  const total = scan.total_files_discovered;
  const terminal = ["succeeded", "failed", "canceled"].includes(scan.status);
  const percent = total > 0
    ? Math.min(100, Math.round((completed / total) * 100))
    : terminal
      ? 100
      : 0;
  return { completed, total, percent };
}

function scanTiming(scan: ScanJob) {
  const start = scan.started_at || scan.created_at;
  const end = scan.finished_at || (["queued", "running"].includes(scan.status) ? new Date().toISOString() : undefined);
  if (!start || !end) {
    return { label: "Elapsed", value: "Unknown" };
  }
  const elapsedMs = Math.max(0, new Date(end).getTime() - new Date(start).getTime());
  return {
    label: scan.finished_at ? "Duration" : "Elapsed",
    value: formatElapsed(elapsedMs)
  };
}

function RecentTranscodeRuns({ runs }: { runs: TranscodeRun[] }) {
  if (!runs.length) {
    return <p className="muted">No transcode runs yet.</p>;
  }
  return (
    <div className="recentScans">
      {runs.map((run) => (
        <div className="recentScan" key={run.id}>
          <div className="recentScanHeader">
            <strong>{run.name || `Run #${run.id}`}</strong>
            <div className="rowActions">
              <StatusBadge status={run.status} />
              {run.archived_at && <Badge>Archived</Badge>}
            </div>
          </div>
          <div className="progressHeader">
            <strong>{Math.round(run.progress_percent || 0)}%</strong>
            <span>{run.completed_items} / {run.total_items} items</span>
          </div>
          <Progress value={run.progress_percent || 0} />
          <div className="scanStats">
            <span><strong>{run.failed_items}</strong> failed</span>
            <span><strong>{run.canceled_items}</strong> canceled</span>
            <span><strong>{formatRunDuration(run)}</strong> duration</span>
            <span><strong>{formatDateTime(run.created_at)}</strong> created</span>
          </div>
          {run.archived_at && <div className="muted">Archived {formatDateTime(run.archived_at)}</div>}
          {run.message && <div className="muted">{run.message}</div>}
        </div>
      ))}
    </div>
  );
}

function RecentScans({ scans }: { scans: ScanJob[] }) {
  if (!scans.length) {
    return <p className="muted">No scans yet.</p>;
  }
  return (
    <div className="recentScans">
      {scans.map((scan) => {
        const progress = scanProgress(scan);
        const timing = scanTiming(scan);
        return (
          <div className="recentScan" key={scan.id}>
            <div className="recentScanHeader">
              <strong>Scan #{scan.id}</strong>
              <StatusBadge status={scan.status} />
            </div>
            <div className="progressHeader">
              <strong>{progress.percent}%</strong>
              <span>{progress.completed} / {progress.total || "?"} files</span>
            </div>
            <Progress value={progress.percent} />
            <div className="scanStats">
              <span><strong>{scan.files_probed}</strong> probed</span>
              <span><strong>{scan.files_skipped}</strong> skipped</span>
              <span><strong>{scan.files_failed}</strong> failed</span>
              <span><strong>{timing.value}</strong> {timing.label.toLowerCase()}</span>
            </div>
            <div className="muted">{scan.message || "No message"}</div>
            {scan.current_path && <div className="path">{scan.current_path}</div>}
          </div>
        );
      })}
    </div>
  );
}

function Library({ onToast }: { onToast: (message: string) => void }) {
  const [files, setFiles] = useState<MediaFile[]>([]);
  const [total, setTotal] = useState(0);
  const [libraries, setLibraries] = useState<PlexLibrary[]>([]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [plexMatched, setPlexMatched] = useState("");
  const [plexLibrary, setPlexLibrary] = useState("");
  const [plexType, setPlexType] = useState("");
  const [plexYear, setPlexYear] = useState("");
  const [plexCollection, setPlexCollection] = useState("");
  const [plexGenre, setPlexGenre] = useState("");
  const [plexLabel, setPlexLabel] = useState("");
  const [plexWatched, setPlexWatched] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<MediaFile | null>(null);

  useEffect(() => {
    refresh();
  }, [query, category, plexMatched, plexLibrary, plexType, plexYear, plexCollection, plexGenre, plexLabel, plexWatched, page]);

  useEffect(() => {
    api<PlexLibrary[]>("/api/plex/libraries").then(setLibraries).catch(() => setLibraries([]));
  }, []);

  async function refresh() {
    try {
      const params = new URLSearchParams({ page: String(page), page_size: "50", sort: "size_bytes", direction: "desc" });
      if (query) params.set("query", query);
      if (category) params.set("recommendation_category", category);
      if (plexMatched) params.set("plex_matched", plexMatched);
      if (plexLibrary) params.set("plex_library", plexLibrary);
      if (plexType) params.set("plex_type", plexType);
      if (plexYear) params.set("plex_year", plexYear);
      if (plexCollection) params.set("plex_collection", plexCollection);
      if (plexGenre) params.set("plex_genre", plexGenre);
      if (plexLabel) params.set("plex_label", plexLabel);
      if (plexWatched) params.set("plex_watched", plexWatched);
      const result = await api<{ items: MediaFile[]; total: number }>(`/api/media?${params}`);
      setFiles(result.items);
      setTotal(result.total);
    } catch (error) {
      onToast(String(error));
    }
  }

  async function openDetail(id: number) {
    setSelected(await api<MediaFile>(`/api/media/${id}`));
  }

  return (
    <section className="stack">
      <div className="toolbar wrap">
        <input value={query} onChange={(event) => { setPage(1); setQuery(event.target.value); }} placeholder="Search path, filename, Plex title, show, recommendation" />
        <select value={category} onChange={(event) => { setPage(1); setCategory(event.target.value); }}>
          <option value="">All recommendations</option>
          {["Easy Win", "Remux Only", "Review", "Already Modern", "Skip", "Error", "Missing"].map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
        <select value={plexMatched} onChange={(event) => { setPage(1); setPlexMatched(event.target.value); }}>
          <option value="">All Plex matches</option>
          <option value="true">Plex matched</option>
          <option value="false">Plex unmatched</option>
        </select>
        <select value={plexLibrary} onChange={(event) => { setPage(1); setPlexLibrary(event.target.value); }}>
          <option value="">All Plex libraries</option>
          {libraries.map((library) => <option key={library.section_key} value={library.section_key}>{library.title}</option>)}
        </select>
        <select value={plexType} onChange={(event) => { setPage(1); setPlexType(event.target.value); }}>
          <option value="">All Plex types</option>
          <option value="movie">Movies</option>
          <option value="episode">Episodes</option>
        </select>
        <input value={plexYear} onChange={(event) => { setPage(1); setPlexYear(event.target.value); }} placeholder="Plex year" />
        <input value={plexCollection} onChange={(event) => { setPage(1); setPlexCollection(event.target.value); }} placeholder="Collection" />
        <input value={plexGenre} onChange={(event) => { setPage(1); setPlexGenre(event.target.value); }} placeholder="Genre" />
        <input value={plexLabel} onChange={(event) => { setPage(1); setPlexLabel(event.target.value); }} placeholder="Label" />
        <select value={plexWatched} onChange={(event) => { setPage(1); setPlexWatched(event.target.value); }}>
          <option value="">Any watched state</option>
          <option value="true">Watched</option>
          <option value="false">Unwatched</option>
        </select>
        <a className="button" href={exportUrl("all-files.csv")}>Export all files</a>
      </div>
      <MediaTable files={files} onOpen={openDetail} />
      <Pager page={page} total={total} pageSize={50} onPage={setPage} />
      {selected && <DetailDrawer file={selected} onClose={() => setSelected(null)} />}
    </section>
  );
}

function Candidates({
  onToast,
  onPlanCategory
}: {
  onToast: (message: string) => void;
  onPlanCategory: (category: PlannerCategory) => void;
}) {
  const [category, setCategory] = useState<CandidateCategory>("Easy Win");
  return (
    <section className="stack">
      <div className="panelIntro">
        <div className="tabs">
          {candidateCategories.map((item) => (
            <button key={item} className={category === item ? "active" : ""} onClick={() => setCategory(item)}>{item}</button>
          ))}
        </div>
        {isPlannerCategory(category) && <button className="primary" onClick={() => onPlanCategory(category)}>Plan this category</button>}
      </div>
      <CandidateList category={category} onToast={onToast} />
    </section>
  );
}

function CandidateList({ category, onToast }: { category: string; onToast: (message: string) => void }) {
  const [files, setFiles] = useState<MediaFile[]>([]);
  useEffect(() => {
    api<{ items: MediaFile[] }>(`/api/media?recommendation_category=${encodeURIComponent(category)}&page_size=100&sort=size_bytes&direction=desc`)
      .then((result) => setFiles(result.items))
      .catch((error) => onToast(String(error)));
  }, [category, onToast]);
  return <MediaTable files={files} onOpen={() => undefined} />;
}

function Retention({ onToast }: { onToast: (message: string) => void }) {
  const [summary, setSummary] = useState<RetentionSummary | null>(null);
  const [jobs, setJobs] = useState<RetentionAnalysisJob[]>([]);
  const [candidates, setCandidates] = useState<RetentionCandidatePage | null>(null);
  const [connections, setConnections] = useState<RetentionConnection[]>([]);
  const [actions, setActions] = useState<RetentionAction[]>([]);
  const [profiles, setProfiles] = useState<TranscodeProfile[]>([]);
  const [status, setStatus] = useState("active");
  const [mediaType, setMediaType] = useState("all");
  const [connectionId, setConnectionId] = useState("");
  const [query, setQuery] = useState("");
  const [page, setCandidatePage] = useState(1);
  const [selected, setSelected] = useState<RetentionCandidate | null>(null);
  const [transcodeCandidate, setTranscodeCandidate] = useState<RetentionCandidate | null>(null);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, [status, mediaType, connectionId, query, page]);

  async function refresh() {
    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: "50",
        status,
        media_type: mediaType,
        query
      });
      if (connectionId) params.set("connection_id", connectionId);
      const [nextSummary, nextJobs, nextCandidates, nextConnections, nextActions, nextProfiles] = await Promise.all([
        api<RetentionSummary>("/api/retention/summary"),
        api<RetentionAnalysisJob[]>("/api/retention/analyses?limit=20"),
        api<RetentionCandidatePage>(`/api/retention/candidates?${params}`),
        api<RetentionConnection[]>("/api/retention/connections"),
        api<RetentionAction[]>("/api/retention/actions?limit=50"),
        api<TranscodeProfile[]>("/api/transcode-profiles")
      ]);
      setSummary(nextSummary);
      setJobs(nextJobs);
      setCandidates(nextCandidates);
      setConnections(nextConnections);
      setActions(nextActions);
      setProfiles(nextProfiles);
      if (selected) {
        const nextSelected = await api<RetentionCandidate>(`/api/retention/candidates/${selected.id}`);
        setSelected(nextSelected);
      }
    } catch (error) {
      onToast(String(error));
    }
  }

  async function startAnalysis() {
    try {
      await api<RetentionAnalysisJob>("/api/retention/analyses", { method: "POST", body: "{}" });
      await refresh();
      onToast("Retention analysis queued.");
    } catch (error) {
      onToast(String(error));
    }
  }

  async function cancelAnalysis(jobId: number) {
    try {
      await api(`/api/retention/analyses/${jobId}/cancel`, { method: "POST", body: "{}" });
      await refresh();
    } catch (error) {
      onToast(String(error));
    }
  }

  async function retryAnalysis(jobId: number) {
    try {
      await api(`/api/retention/analyses/${jobId}/retry`, { method: "POST", body: "{}" });
      await refresh();
      onToast("Retention analysis retry queued.");
    } catch (error) {
      onToast(String(error));
    }
  }

  async function openDetail(candidateId: number) {
    try {
      setSelected(await api<RetentionCandidate>(`/api/retention/candidates/${candidateId}`));
    } catch (error) {
      onToast(String(error));
    }
  }

  async function openTranscode(candidate: RetentionCandidate) {
    try {
      const detail = candidate.files
        ? candidate
        : await api<RetentionCandidate>(`/api/retention/candidates/${candidate.id}`);
      setTranscodeCandidate(detail);
    } catch (error) {
      onToast(String(error));
    }
  }

  async function deleteCandidate(candidate: RetentionCandidate) {
    if (!window.confirm(
      `Delete the entire ${candidate.media_type === "tv" ? "series" : "movie"} copy from ${candidate.connection_name}? This removes managed files through ${candidate.service_type}.`
    )) return;
    const expected = `DELETE ${candidate.title}`;
    const confirmation = window.prompt(`Type ${expected} to continue.`);
    if (confirmation === null) return;
    try {
      await api(`/api/retention/candidates/${candidate.id}/delete`, {
        method: "POST",
        body: JSON.stringify({ confirmation_text: confirmation })
      });
      setSelected(null);
      await refresh();
      onToast("Deletion completed through the owning service.");
    } catch (error) {
      onToast(String(error));
    }
  }

  async function retrySeerr(actionId: number) {
    try {
      await api(`/api/retention/actions/${actionId}/retry-seerr`, { method: "POST", body: "{}" });
      await refresh();
      onToast("Seerr reconciliation completed.");
    } catch (error) {
      onToast(String(error));
    }
  }

  const latest = summary?.latest_analysis;
  return (
    <section className="stack">
      <div className="metrics">
        <Metric label="Deletion candidates" value={summary?.candidate_count ?? 0} />
        <Metric label="Reclaimable" value={formatBytes(summary?.total_size_bytes ?? 0)} />
        <Metric label="Mapping diagnostics" value={summary?.diagnostic_count ?? 0} />
        <Metric label="Latest analysis" value={formatDateTime(latest?.finished_at || latest?.created_at)} />
      </div>

      {!summary?.configured && (
        <Panel title="Retention setup required">
          <p className="muted">Configure and enable one Seerr connection and at least one Sonarr or Radarr connection in Settings. Plex must also be enabled.</p>
        </Panel>
      )}

      {latest?.warnings.length ? (
        <Panel title="Source warnings">
          <div className="warningList">
            {latest.warnings.map((warning, index) => (
              <div key={`${warning.source}-${index}`}>
                <Badge tone="warn">{warning.source}</Badge> {warning.message}
              </div>
            ))}
          </div>
        </Panel>
      ) : null}

      <Panel title="Latest run">
        <div className="panelIntro">
          <div className="statusGrid">
            {latest ? (
              <>
                <div className="progressHeader">
                  <StatusBadge status={latest.status} />
                  <strong>{Math.round(latest.progress_percent || 0)}%</strong>
                </div>
                <Progress value={latest.progress_percent} />
                <span className="muted">{latest.message || latest.error_message}</span>
              </>
            ) : <span className="muted">No retention analysis has run.</span>}
          </div>
          <div className="rowActions">
            <button className="primary" disabled={Boolean(latest && ["queued", "running"].includes(latest.status))} onClick={startAnalysis}>Run analysis</button>
            {latest && ["queued", "running"].includes(latest.status) && <button onClick={() => cancelAnalysis(latest.id)}>Cancel</button>}
            {latest && ["failed", "canceled", "interrupted"].includes(latest.status) && <button onClick={() => retryAnalysis(latest.id)}>Retry</button>}
            <a className="button" href={exportUrl("retention-candidates.csv")}>Export CSV</a>
          </div>
        </div>
      </Panel>

      <Panel title="Candidates">
        <div className="toolbar retentionFilters">
          <input value={query} onChange={(event) => { setCandidatePage(1); setQuery(event.target.value); }} placeholder="Search title" />
          <select value={status} onChange={(event) => { setCandidatePage(1); setStatus(event.target.value); }}>
            <option value="active">Deletion candidates</option>
            <option value="diagnostic">Mapping diagnostics</option>
            <option value="all">All results</option>
          </select>
          <select value={mediaType} onChange={(event) => { setCandidatePage(1); setMediaType(event.target.value); }}>
            <option value="all">Movies and shows</option>
            <option value="movie">Movies</option>
            <option value="tv">Shows</option>
          </select>
          <select value={connectionId} onChange={(event) => { setCandidatePage(1); setConnectionId(event.target.value); }}>
            <option value="">All instances</option>
            {connections.filter((item) => item.service_type !== "seerr").map((connection) => (
              <option key={connection.id} value={connection.id}>{connection.name}</option>
            ))}
          </select>
        </div>
        <table className="retentionCandidatesTable mobileStackTable">
          <thead><tr><th>Title</th><th>Reason and requester</th><th>Storage</th><th>Eligibility</th><th>Coverage</th><th>Instance</th><th></th></tr></thead>
          <tbody>
            {candidates?.items.map((candidate) => (
              <tr key={candidate.id}>
                <td className="mobileStackPrimary">
                  <strong>{candidate.title}{candidate.year ? ` (${candidate.year})` : ""}</strong>
                  <span className="muted">{candidate.media_type === "tv" ? "Whole series" : "Movie"}{candidate.is_4k ? " · 4K" : ""}</span>
                </td>
                <td>
                  <span className="mobileCellLabel">Reason</span>
                  <span>{candidate.reason}</span>
                  <div className="muted">Requested by {candidate.requesters.join(", ")}</div>
                </td>
                <td><span className="mobileCellLabel">Storage</span><strong>{formatBytes(candidate.size_bytes)}</strong><div className="muted">{candidate.file_count} files</div></td>
                <td><span className="mobileCellLabel">Eligibility</span>{eligibilityAgeDays(candidate.eligible_since)} days<div className="muted">Since {formatDateTime(candidate.eligible_since)}</div></td>
                <td>
                  <span className="mobileCellLabel">Coverage</span>
                  <Badge tone={candidate.status === "active" ? "good" : "warn"}>{candidate.matched_file_count}/{candidate.file_count} mapped</Badge>
                  <div className="muted">Zero qualifying plays</div>
                </td>
                <td><span className="mobileCellLabel">Instance</span>{candidate.connection_name}<div className="muted">{candidate.service_type}</div></td>
                <td className="rowActions">
                  <button onClick={() => openDetail(candidate.id)}>Details</button>
                  {candidate.available_actions.includes("transcode_plan") && <button onClick={() => openTranscode(candidate)}>Transcode plan</button>}
                  {candidate.available_actions.includes("delete") && <button className="danger" onClick={() => deleteCandidate(candidate)}>Delete</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!candidates?.items.length && <p className="muted">No candidates match these filters.</p>}
        <Pager page={page} total={candidates?.total ?? 0} pageSize={50} onPage={setCandidatePage} />
      </Panel>

      <Panel title="Analysis history">
        <table className="mobileStackTable retentionJobsTable">
          <thead><tr><th>Run</th><th>Status</th><th>Results</th><th>Timing</th><th>Message</th><th></th></tr></thead>
          <tbody>{jobs.map((job) => (
            <tr key={job.id}>
              <td className="mobileStackPrimary"><strong>#{job.id}</strong><div className="muted">{job.trigger_type}</div></td>
              <td><span className="mobileCellLabel">Status</span><StatusBadge status={job.status} /></td>
              <td><span className="mobileCellLabel">Results</span>{job.candidate_count} candidates · {formatBytes(job.total_size_bytes)}<div className="muted">{job.diagnostic_count} diagnostics · {job.warnings.length} warnings</div></td>
              <td><span className="mobileCellLabel">Timing</span>{formatDateTime(job.created_at)}<div className="muted">Finished {formatDateTime(job.finished_at)}</div></td>
              <td><span className="mobileCellLabel">Message</span>{job.error_message || job.message}</td>
              <td className="rowActions">
                {["queued", "running"].includes(job.status) && <button onClick={() => cancelAnalysis(job.id)}>Cancel</button>}
                {["failed", "canceled", "interrupted"].includes(job.status) && <button onClick={() => retryAnalysis(job.id)}>Retry</button>}
              </td>
            </tr>
          ))}</tbody>
        </table>
      </Panel>

      <Panel title="Action history">
        <table className="mobileStackTable retentionActionsTable">
          <thead><tr><th>Media</th><th>Action</th><th>Status</th><th>Requested by</th><th>Time</th><th></th></tr></thead>
          <tbody>{actions.map((action) => (
            <tr key={action.id}>
              <td className="mobileStackPrimary"><strong>{action.title}</strong><div className="muted">{action.connection_name}</div></td>
              <td><span className="mobileCellLabel">Action</span>{formatStatusLabel(action.action_type)}</td>
              <td><span className="mobileCellLabel">Status</span><StatusBadge status={action.status} />{action.error_message && <div className="muted">{action.error_message}</div>}</td>
              <td><span className="mobileCellLabel">Requested by</span>{action.requested_by || "local"}</td>
              <td><span className="mobileCellLabel">Time</span>{formatDateTime(action.finished_at || action.created_at)}</td>
              <td>{action.action_type === "delete" && action.status === "succeeded_with_warning" && <button onClick={() => retrySeerr(action.id)}>Retry Seerr</button>}</td>
            </tr>
          ))}</tbody>
        </table>
        {!actions.length && <p className="muted">No retention actions have been taken.</p>}
      </Panel>

      {selected && (
        <RetentionDetailDrawer
          candidate={selected}
          onClose={() => setSelected(null)}
          onTranscode={openTranscode}
          onDelete={deleteCandidate}
        />
      )}
      {transcodeCandidate && (
        <RetentionTranscodeDialog
          candidate={transcodeCandidate}
          profiles={profiles}
          onClose={() => setTranscodeCandidate(null)}
          onCreated={async (planId) => {
            setTranscodeCandidate(null);
            await refresh();
            onToast(`Created transcode plan ${planId}. No transcode was started.`);
          }}
          onToast={onToast}
        />
      )}
    </section>
  );
}

function RetentionDetailDrawer({
  candidate,
  onClose,
  onTranscode,
  onDelete
}: {
  candidate: RetentionCandidate;
  onClose: () => void;
  onTranscode: (candidate: RetentionCandidate) => void;
  onDelete: (candidate: RetentionCandidate) => void;
}) {
  return (
    <div className="drawerBackdrop" onClick={onClose}>
      <aside className="drawer" onClick={(event) => event.stopPropagation()}>
        <div className="drawerHeader">
          <div><h2>{candidate.title}{candidate.year ? ` (${candidate.year})` : ""}</h2><p className="muted">{candidate.connection_name} · {candidate.media_type === "tv" ? "whole series" : "movie"}</p></div>
          <button onClick={onClose}>Close</button>
        </div>
        <div className="metrics compact">
          <Metric label="Exact disk size" value={formatBytes(candidate.size_bytes)} />
          <Metric label="Eligibility age" value={`${eligibilityAgeDays(candidate.eligible_since)} days`} />
          <Metric label="Managed files" value={candidate.file_count} />
          <Metric label="Plex coverage" value={`${candidate.matched_file_count}/${candidate.file_count}`} />
        </div>
        <Panel title="Decision evidence">
          <div className="statusGrid">
            <p>{candidate.reason}</p>
            <div><strong>Requesters</strong><p>{candidate.requesters.join(", ")}</p></div>
            <div><strong>Eligibility date</strong><p>{formatDateTime(candidate.eligible_since)}</p></div>
            <div><strong>Play evidence</strong><p>No mapped Plex item has a play or last-viewed timestamp at or after the eligibility date.</p></div>
          </div>
        </Panel>
        <Panel title="Managed files">
          <div className="retentionFileList">
            {candidate.files?.map((file) => (
              <div key={file.id}>
                <div><strong>{file.filename || file.path.split("/").pop()}</strong><div className="path">{file.path}</div></div>
                <div><Badge tone={file.match_status === "matched" ? "good" : "warn"}>{file.match_status}</Badge><div>{formatBytes(file.size_bytes)}</div></div>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Request history">
          <div className="compactList">
            {candidate.requests.map((request, index) => (
              <div key={`${request.id}-${index}`}><strong>{request.requester}</strong><span>{formatDateTime(request.created_at)}{request.is_4k ? " · 4K" : ""}</span></div>
            ))}
          </div>
        </Panel>
        <div className="rowActions retentionDrawerActions">
          {candidate.available_actions.includes("transcode_plan") && <button className="primary" onClick={() => onTranscode(candidate)}>Create transcode plan</button>}
          {candidate.available_actions.includes("delete") && <button className="danger" onClick={() => onDelete(candidate)}>Delete through {candidate.service_type}</button>}
        </div>
      </aside>
    </div>
  );
}

function RetentionTranscodeDialog({
  candidate,
  profiles,
  onClose,
  onCreated,
  onToast
}: {
  candidate: RetentionCandidate;
  profiles: TranscodeProfile[];
  onClose: () => void;
  onCreated: (planId: number) => Promise<void>;
  onToast: (message: string) => void;
}) {
  const eligibleFiles = (candidate.files || []).filter((file) => file.media_atlas_file_id);
  const preferred = eligibleFiles.filter((file) => isPlannerCategory(file.recommendation_category));
  const availableCategories = plannerCategories.filter((item) => preferred.some((file) => file.recommendation_category === item));
  const initialCategory = availableCategories[0] || "Review";
  const [category, setCategory] = useState<PlannerCategory>(initialCategory);
  const categoryFiles = preferred.filter((file) => file.recommendation_category === category);
  const [profileId, setProfileId] = useState(defaultProfileId(profiles, initialCategory));
  const [name, setName] = useState(`Retention review: ${candidate.title}`);
  const [selectedFiles, setSelectedFiles] = useState<Set<number>>(
    new Set(preferred.filter((file) => file.recommendation_category === initialCategory).map((file) => Number(file.media_atlas_file_id)))
  );

  function toggle(fileId: number) {
    const next = new Set(selectedFiles);
    next.has(fileId) ? next.delete(fileId) : next.add(fileId);
    setSelectedFiles(next);
  }

  function changeCategory(nextCategory: PlannerCategory) {
    setCategory(nextCategory);
    setProfileId(suggestedProfileId(profiles, nextCategory));
    setSelectedFiles(new Set(
      preferred
        .filter((file) => file.recommendation_category === nextCategory)
        .map((file) => Number(file.media_atlas_file_id))
    ));
  }

  async function submit() {
    try {
      const result = await api<{ plan: TranscodePlan }>(`/api/retention/candidates/${candidate.id}/transcode-plan`, {
        method: "POST",
        body: JSON.stringify({ profile_id: profileId, name, file_ids: Array.from(selectedFiles) })
      });
      await onCreated(result.plan.id);
    } catch (error) {
      onToast(String(error));
    }
  }

  return (
    <div className="dialogBackdrop" onClick={onClose}>
      <section className="dialogPanel" role="dialog" aria-modal="true" aria-labelledby="retention-transcode-title" onClick={(event) => event.stopPropagation()}>
        <div className="drawerHeader"><div><h2 id="retention-transcode-title">Create transcode plan</h2><p className="muted">{candidate.title} · creating this plan does not start a transcode.</p></div><button onClick={onClose}>Close</button></div>
        {availableCategories.length > 0 && (
          <div className="tabs">
            {availableCategories.map((item) => <button key={item} className={category === item ? "active" : ""} onClick={() => changeCategory(item)}>{item}</button>)}
          </div>
        )}
        <div className="formGrid retentionTranscodeForm">
          <label>Plan name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>Profile<select value={profileId} onChange={(event) => setProfileId(Number(event.target.value))}>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>
        </div>
        {profileGuidanceWarning(category, selectedProfile(profiles, profileId)) && (
          <div className="guidanceWarning" role="status"><strong>Profile guidance</strong><span>{profileGuidanceWarning(category, selectedProfile(profiles, profileId))}</span></div>
        )}
        <div className="checkList retentionTranscodeFiles">
          {categoryFiles.map((file) => (
            <label className="checkRow" key={file.id}>
              <input type="checkbox" checked={selectedFiles.has(Number(file.media_atlas_file_id))} onChange={() => toggle(Number(file.media_atlas_file_id))} />
              <span><strong>{file.filename || file.path}</strong><span className="muted">{file.recommendation_category || "Uncategorized"} · {formatBytes(file.size_bytes)}</span></span>
            </label>
          ))}
        </div>
        {!preferred.length && <p className="muted">No present, successfully probed files in this candidate have an Easy Win, Remux Only, or Review recommendation, so a plan cannot be created.</p>}
        <div className="rowActions"><button className="primary" disabled={!profileId || !selectedFiles.size} onClick={submit}>Create plan</button><button onClick={onClose}>Cancel</button></div>
      </section>
    </div>
  );
}

function Reports() {
  const [summary, setSummary] = useState<Summary | null>(null);
  useEffect(() => {
    api<Summary>("/api/reports/summary").then(setSummary);
  }, []);
  return (
    <section className="stack">
      <div className="toolbar wrap">
        {["all-files.csv", "transcode-candidates.csv", "scan-errors.csv", "summary-by-codec.csv", "summary-by-container.csv", "summary-by-resolution.csv", "largest-files.csv"].map((name) => (
          <a key={name} className="button" href={exportUrl(name)}>{name}</a>
        ))}
      </div>
      <div className="grid two">
        <Panel title="By Video Codec"><ReportTable rows={summary?.by_video_codec ?? []} /></Panel>
        <Panel title="By Container"><ReportTable rows={summary?.by_container ?? []} /></Panel>
        <Panel title="By Resolution"><ReportTable rows={summary?.by_resolution ?? []} /></Panel>
        <Panel title="By Audio Codec"><ReportTable rows={summary?.by_audio_codec ?? []} /></Panel>
      </div>
    </section>
  );
}

function Planner({
  onToast,
  switchToRuns,
  initialCategory
}: {
  onToast: (message: string) => void;
  switchToRuns: () => void;
  initialCategory: PlannerCategory;
}) {
  const [profiles, setProfiles] = useState<TranscodeProfile[]>([]);
  const [plans, setPlans] = useState<TranscodePlan[]>([]);
  const [files, setFiles] = useState<MediaFile[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [profileId, setProfileId] = useState<number>(0);
  const [name, setName] = useState("Transcode plan");
  const [showArchived, setShowArchived] = useState(false);
  const [category, setCategory] = useState<PlannerCategory>(initialCategory);
  const [query, setQuery] = useState("");
  const [candidatePage, setCandidatePage] = useState(1);
  const [candidateTotal, setCandidateTotal] = useState(0);
  const [loadingCandidates, setLoadingCandidates] = useState(false);

  useEffect(() => {
    refreshMetadata();
  }, [showArchived]);

  useEffect(() => {
    refreshCandidates();
  }, [category, query, candidatePage]);

  async function refreshMetadata() {
    try {
      const [nextProfiles, nextPlans] = await Promise.all([
        api<TranscodeProfile[]>("/api/transcode-profiles"),
        api<TranscodePlan[]>(`/api/transcode-plans${showArchived ? "?include_archived=true" : ""}`)
      ]);
      setProfiles(nextProfiles);
      setPlans(nextPlans);
      setProfileId((current) => current || suggestedProfileId(nextProfiles, category));
    } catch (error) {
      onToast(String(error));
    }
  }

  async function refreshCandidates() {
    const params = new URLSearchParams({
      recommendation_category: category,
      page: String(candidatePage),
      page_size: "50",
      sort: "size_bytes",
      direction: "desc"
    });
    if (query.trim()) params.set("query", query.trim());
    try {
      setLoadingCandidates(true);
      const candidates = await api<{ items: MediaFile[]; total: number }>(`/api/media?${params}`);
      setFiles(candidates.items);
      setCandidateTotal(candidates.total);
    } catch (error) {
      onToast(String(error));
    } finally {
      setLoadingCandidates(false);
    }
  }

  function toggleFile(id: number) {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  }

  function toggleVisibleFiles() {
    const next = new Set(selected);
    const allVisibleSelected = files.length > 0 && files.every((file) => next.has(file.id));
    for (const file of files) {
      allVisibleSelected ? next.delete(file.id) : next.add(file.id);
    }
    setSelected(next);
  }

  function changeCategory(nextCategory: PlannerCategory) {
    if (nextCategory === category) return;
    if (selected.size && !window.confirm(
      `Switch to ${nextCategory}? Your ${selected.size} selected ${category} file${selected.size === 1 ? "" : "s"} will be cleared so a plan cannot mix recommendation categories.`
    )) return;
    setCategory(nextCategory);
    setSelected(new Set());
    setQuery("");
    setCandidatePage(1);
    setProfileId(suggestedProfileId(profiles, nextCategory));
  }

  async function create() {
    try {
      const plan = await api<TranscodePlan>("/api/transcode-plans", {
        method: "POST",
        body: JSON.stringify({ name, profile_id: profileId, file_ids: Array.from(selected) })
      });
      setSelected(new Set());
      await refreshMetadata();
      onToast(`Created plan ${plan.id}.`);
    } catch (error) {
      onToast(String(error));
    }
  }

  async function startRun(plan: TranscodePlan) {
    try {
      await api("/api/transcode-runs", {
        method: "POST",
        body: JSON.stringify({ plan_id: plan.id, name: `Run ${plan.name}` })
      });
      onToast("Transcode run queued.");
      switchToRuns();
    } catch (error) {
      onToast(String(error));
    }
  }

  async function archivePlan(plan: TranscodePlan) {
    if (!window.confirm(`Archive "${plan.name}"? It will be hidden from the default planner view, but run history is preserved.`)) return;
    try {
      await api(`/api/transcode-plans/${plan.id}/archive`, { method: "POST", body: "{}" });
      await refreshMetadata();
      onToast("Transcode plan archived.");
    } catch (error) {
      onToast(String(error));
    }
  }

  async function unarchivePlan(plan: TranscodePlan) {
    try {
      await api(`/api/transcode-plans/${plan.id}/unarchive`, { method: "POST", body: "{}" });
      await refreshMetadata();
      onToast("Transcode plan restored.");
    } catch (error) {
      onToast(String(error));
    }
  }

  async function deletePlan(plan: TranscodePlan) {
    if (!window.confirm(`Delete "${plan.name}"? This removes the plan and its planned items. Source media and staged outputs are untouched.`)) return;
    try {
      await api(`/api/transcode-plans/${plan.id}`, { method: "DELETE" });
      await refreshMetadata();
      onToast("Transcode plan deleted.");
    } catch (error) {
      onToast(String(error));
    }
  }

  return (
    <section className="stack">
      <Panel title="Existing Plans">
        <div className="panelIntro">
          <p className="muted">Review planned files and run history before starting another staged transcode run.</p>
          <div className="rowActions">
            <label className="inlineCheck">
              <input type="checkbox" checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} />
              Show archived
            </label>
            <a className="button" href={TRANSCODE_PROFILES_URL} target="_blank" rel="noreferrer">Profile guide</a>
          </div>
        </div>
        <table className="plannerPlansTable mobileStackTable">
          <thead>
            <tr>
              <th>Plan</th>
              <th>Created</th>
              <th>Files involved</th>
              <th>Run history</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {plans.map((plan) => (
              <tr key={plan.id}>
                <td className="mobileStackPrimary">
                  <strong>{plan.name}</strong>
                  <div className="muted">
                    {plan.profile_name || "Unknown profile"} · {plan.status}
                    {plan.archived_at ? ` · archived ${formatDateTime(plan.archived_at)}` : ""}
                  </div>
                </td>
                <td>
                  <span className="mobileCellLabel">Created</span>
                  {formatDateTime(plan.created_at)}
                </td>
                <td>
                  <span className="mobileCellLabel">Files involved</span>
                  <div className="planFiles">
                    <strong>{plan.item_count || 0} files</strong>
                    <span className="muted">{plan.runnable_item_count || 0} runnable</span>
                    {(plan.sample_items || []).map((item) => (
                      <span key={item.id} className="path">{planItemName(item)}</span>
                    ))}
                    {planRemainingCount(plan) > 0 && <span className="muted">+ {planRemainingCount(plan)} more</span>}
                  </div>
                </td>
                <td>
                  <span className="mobileCellLabel">Run history</span>
                  <PlanRunSummary plan={plan} />
                </td>
                <td className="rowActions">
                  <a className="button" href={`/api/transcode-plans/${plan.id}/download.csv`}>CSV</a>
                  <a className="button" href={`/api/transcode-plans/${plan.id}/download.sh`}>Script</a>
                  {plan.archived_at ? (
                    <button onClick={() => unarchivePlan(plan)}>Unarchive</button>
                  ) : (
                    <>
                      <button
                        disabled={!plan.runnable_item_count}
                        title={!plan.runnable_item_count ? "This review-only plan has no generated commands." : "Start a staged transcode run"}
                        onClick={() => startRun(plan)}
                      >
                        {plan.runnable_item_count ? "Start run" : "Review only"}
                      </button>
                      <button onClick={() => archivePlan(plan)}>Archive</button>
                    </>
                  )}
                  {!plan.run_count && <button className="danger" onClick={() => deletePlan(plan)}>Delete</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <Panel title="Create Plan">
        <div className="panelIntro">
          <p className="muted">Choose one recommendation category, select files, and review the suggested staged-output profile.</p>
          <a className="button" href={TRANSCODE_PROFILES_URL} target="_blank" rel="noreferrer">Profile guide</a>
        </div>
        <div className="tabs plannerCategoryTabs">
          {plannerCategories.map((item) => (
            <button key={item} className={category === item ? "active" : ""} onClick={() => changeCategory(item)}>{item}</button>
          ))}
        </div>
        <div className="formGrid">
          <label>
            Plan name
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label>
            Profile
            <select value={profileId} onChange={(event) => setProfileId(Number(event.target.value))}>
              {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
            </select>
          </label>
          <button disabled={!selected.size || !profileId} onClick={create}>Create plan</button>
        </div>
        {selectedProfile(profiles, profileId)?.description && (
          <p className="muted">{selectedProfile(profiles, profileId)?.description}</p>
        )}
        {profileGuidanceWarning(category, selectedProfile(profiles, profileId)) && (
          <div className="guidanceWarning" role="status">
            <strong>Profile guidance</strong>
            <span>{profileGuidanceWarning(category, selectedProfile(profiles, profileId))}</span>
          </div>
        )}
        <div className="toolbar plannerCandidateToolbar">
          <input
            value={query}
            onChange={(event) => { setCandidatePage(1); setQuery(event.target.value); }}
            placeholder={`Search ${category} files`}
          />
          <button disabled={!files.length} onClick={toggleVisibleFiles}>
            {files.length > 0 && files.every((file) => selected.has(file.id)) ? "Deselect this page" : "Select this page"}
          </button>
          <button disabled={!selected.size} onClick={() => setSelected(new Set())}>Clear selection</button>
          <span className="muted">{selected.size} selected · {candidateTotal} matching</span>
        </div>
        <table className="plannerCandidateTable mobileStackTable">
          <thead>
            <tr>
              <th aria-label="Select"></th>
              <th>File</th>
              <th>Size</th>
              <th>Video</th>
              <th>Category</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {files.map((file) => (
              <tr key={file.id}>
                <td className="candidateSelectCell">
                  <input
                    type="checkbox"
                    aria-label={`Select ${file.filename}`}
                    checked={selected.has(file.id)}
                    onChange={() => toggleFile(file.id)}
                  />
                </td>
                <td className="candidateFileCell mobileStackPrimary">
                  <strong>{file.filename}</strong>
                  <div className="path">{file.path}</div>
                </td>
                <td>
                  <span className="mobileCellLabel">Size</span>
                  {formatBytes(file.size_bytes)}
                </td>
                <td>
                  <span className="mobileCellLabel">Video</span>
                  {file.resolution_bucket} {file.primary_video_codec}
                </td>
                <td>
                  <span className="mobileCellLabel">Category</span>
                  <Badge tone={toneFor(file.recommendation_category)}>{file.recommendation_category || "Unknown"}</Badge>
                </td>
                <td className="candidateReasonCell">
                  <span className="mobileCellLabel">Reason</span>
                  <strong>{file.recommendation_summary}</strong>
                  {file.recommendation_reasons?.length ? <div className="muted">{file.recommendation_reasons.join(" ")}</div> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {loadingCandidates && <p className="muted">Loading candidates…</p>}
        {!loadingCandidates && !files.length && <p className="muted">No {category} files match this search.</p>}
        <Pager page={candidatePage} total={candidateTotal} pageSize={50} onPage={setCandidatePage} />
      </Panel>
    </section>
  );
}

function PlanRunSummary({ plan }: { plan: TranscodePlan }) {
  if (!plan.run_count || !plan.latest_run) {
    return (
      <div className="planRunSummary">
        <Badge>Never run</Badge>
        <span className="muted">No transcode run has been started from this plan.</span>
      </div>
    );
  }
  return (
    <div className="planRunSummary">
      <div>
        <StatusBadge status={plan.latest_run.status} />
        <span className="muted"> latest run #{plan.latest_run.id}</span>
      </div>
      <span className="muted">Created {formatDateTime(plan.latest_run.created_at)}</span>
      <span className="muted">Started {formatDateTime(plan.latest_run.started_at)}</span>
      <span className="muted">Stopped {formatStopDateTime(plan.latest_run.finished_at, plan.latest_run.status)}</span>
      <span className="muted">{plan.latest_run.completed_items} complete, {plan.latest_run.failed_items} failed, {plan.latest_run.canceled_items} canceled</span>
      {plan.run_count > 1 && <span className="muted">{plan.run_count} total runs</span>}
    </div>
  );
}

function defaultProfileId(profiles: TranscodeProfile[], category: PlannerCategory = "Easy Win") {
  return suggestedProfileId(profiles, category)
    || profiles.find((profile) => profile.command_template !== "manual_review")?.id
    || 0;
}

function suggestedProfileId(profiles: TranscodeProfile[], category: PlannerCategory) {
  const commandTemplate = category === "Remux Only"
    ? "remux_mkv"
    : category === "Review"
      ? "manual_review"
      : "hevc_archive_fast";
  return profiles.find((profile) => profile.command_template === commandTemplate)?.id || 0;
}

function profileGuidanceWarning(category: PlannerCategory, profile?: TranscodeProfile) {
  if (!profile) return "";
  if (category === "Remux Only" && profile.command_template !== "remux_mkv") {
    return profile.command_template === "manual_review"
      ? "This profile creates a tracking plan without a runnable command."
      : "This profile re-encodes media even though the recommendation says a stream-copy remux may be enough.";
  }
  if (category === "Easy Win" && profile.command_template === "remux_mkv") {
    return "A remux changes only the container; it will not address the legacy codec or high-bitrate signal behind this recommendation.";
  }
  if (category === "Easy Win" && profile.command_template === "manual_review") {
    return "This profile creates a tracking plan without a runnable command.";
  }
  if (category === "Review" && profile.command_template !== "manual_review") {
    return "Review files have complex streams or quality considerations. Inspect each warning and validate staged output before publishing.";
  }
  return "";
}

function isPlannerCategory(category: string | null | undefined): category is PlannerCategory {
  return plannerCategories.includes(category as PlannerCategory);
}

function selectedProfile(profiles: TranscodeProfile[], profileId: number) {
  return profiles.find((profile) => profile.id === profileId);
}

function planItemName(item: TranscodePlanItem) {
  if (item.filename) return item.filename;
  const parts = item.source_path.split(/[\\/]/);
  return parts[parts.length - 1] || item.source_path;
}

function planRemainingCount(plan: TranscodePlan) {
  const itemCount = plan.item_count || 0;
  const shown = plan.sample_items?.length || 0;
  return Math.max(0, itemCount - shown);
}

function canPublishItem(item: TranscodeRunItem) {
  return item.status === "succeeded"
    && item.verification_status === "verified"
    && !item.published_at
    && !isPublishingItem(item);
}

function isPublishingItem(item: TranscodeRunItem) {
  return item.publish_status === "queued" || item.publish_status === "running";
}

function canCleanupRun(run: TranscodeRun) {
  return Boolean(run.items?.some((item) => item.published_at && item.validated_at && item.cleanup_status !== "cleaned"));
}

function canCleanupItem(item: TranscodeRunItem) {
  return Boolean(item.published_at)
    && Boolean(item.validated_at)
    && item.cleanup_status !== "cleaned"
    && item.cleanup_status !== "running";
}

function canValidateItem(item: TranscodeRunItem) {
  return Boolean(item.published_at) && !item.validated_at && item.cleanup_status !== "cleaned";
}

function canArchiveRun(run: TranscodeRun) {
  return !["queued", "running"].includes(run.status);
}

function summarizeRunSavings(run: TranscodeRun) {
  let sourceSize = 0;
  let outputSize = 0;
  let measuredItems = 0;
  for (const item of run.items || []) {
    if (item.status !== "succeeded" || item.source_size_bytes == null || item.output_size_bytes == null) {
      continue;
    }
    sourceSize += item.source_size_bytes;
    outputSize += item.output_size_bytes;
    measuredItems += 1;
  }
  return {
    sourceSize,
    outputSize,
    measuredItems,
    saved: sourceSize - outputSize
  };
}
function Runs({
  onToast,
  onOpenLog
}: {
  onToast: (message: string) => void;
  onOpenLog: (runId: number, itemId: number) => void;
}) {
  const [runs, setRuns] = useState<TranscodeRun[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selected, setSelected] = useState<TranscodeRun | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [stats, setStats] = useState<TranscodeSavingsStats | null>(null);

  useEffect(() => {
    refreshRuns();
    const timer = window.setInterval(refreshRuns, 2000);
    return () => window.clearInterval(timer);
  }, [showArchived]);

  useEffect(() => {
    if (!selectedId) return;
    refreshRun(selectedId);
    const timer = window.setInterval(() => refreshRun(selectedId), 2000);
    return () => window.clearInterval(timer);
  }, [selectedId]);

  async function refreshRuns() {
    const [nextRuns, nextStats] = await Promise.all([
      api<TranscodeRun[]>(`/api/transcode-runs?limit=50${showArchived ? "&include_archived=true" : ""}`),
      api<TranscodeSavingsStats>("/api/transcode-runs/stats")
    ]);
    setRuns(nextRuns);
    setStats(nextStats);
  }

  async function refreshRun(id: number) {
    setSelected(await api<TranscodeRun>(`/api/transcode-runs/${id}`));
  }

  async function cancel(id: number) {
    await api(`/api/transcode-runs/${id}/cancel`, { method: "POST", body: "{}" });
    onToast("Cancel requested.");
  }

  async function retry(id: number) {
    await api(`/api/transcode-runs/${id}/retry`, { method: "POST", body: "{}" });
    onToast("Retry queued.");
  }

  async function archiveRun(id: number) {
    await api(`/api/transcode-runs/${id}/archive`, { method: "POST", body: "{}" });
    onToast("Transcode run archived.");
    await refreshRuns();
    if (selectedId === id) await refreshRun(id);
  }

  async function unarchiveRun(id: number) {
    await api(`/api/transcode-runs/${id}/unarchive`, { method: "POST", body: "{}" });
    onToast("Transcode run restored.");
    await refreshRuns();
    if (selectedId === id) await refreshRun(id);
  }

  async function cleanupRun(run: TranscodeRun) {
    const firstConfirmed = window.confirm(
      `Clean up staged outputs and backup files for validated published items in run #${run.id}?\n\nThis deletes staged transcode outputs and original-file backups only for items you have marked validated. The run will be archived only if every item in the run has been published, validated, and cleaned.`
    );
    if (!firstConfirmed) return;
    const phrase = window.prompt(
      `Final confirmation required.\n\nType DELETE ARTIFACTS to permanently delete eligible staged outputs and backups for validated published items in run #${run.id}.`
    );
    if (phrase !== "DELETE ARTIFACTS") {
      onToast("Cleanup canceled.");
      return;
    }
    try {
      const updated = await api<TranscodeRun>(`/api/transcode-runs/${run.id}/cleanup`, {
        method: "POST",
        body: JSON.stringify({ confirmation_text: phrase, archive_run: true })
      });
      setSelected(updated);
      await refreshRuns();
      onToast(updated.cleanup_summary?.run_archived ? "Cleanup completed and run archived." : "Cleanup completed for validated published items. Run remains visible.");
    } catch (error) {
      onToast(String(error));
      await refreshRun(run.id);
    }
  }

  async function cleanupItem(runId: number, item: TranscodeRunItem) {
    const firstConfirmed = window.confirm(
      `Clean up artifacts for item #${item.id}?\n\nThis deletes the staged output and recorded original-file backup for this published item only. Only do this after you have validated the published media.`
    );
    if (!firstConfirmed) return;
    const phrase = window.prompt(
      `Final confirmation required.\n\nType DELETE ARTIFACTS to permanently delete artifacts for item #${item.id}.`
    );
    if (phrase !== "DELETE ARTIFACTS") {
      onToast("Cleanup canceled.");
      return;
    }
    try {
      const cleaned = await api<TranscodeRunItem>(`/api/transcode-runs/${runId}/items/${item.id}/cleanup`, {
        method: "POST",
        body: JSON.stringify({ confirmation_text: phrase })
      });
      await refreshRun(runId);
      await refreshRuns();
      onToast(cleaned.cleanup_status === "failed" ? "Item cleanup failed. Check the item details." : "Item artifacts cleaned up.");
    } catch (error) {
      onToast(String(error));
      await refreshRun(runId);
    }
  }

  async function validateItem(runId: number, item: TranscodeRunItem) {
    const firstConfirmed = window.confirm(
      `Mark item #${item.id} as validated?\n\nOnly do this after you have checked the published media in your library and are comfortable allowing cleanup of its staged output and backup.`
    );
    if (!firstConfirmed) return;
    const phrase = window.prompt(
      `Final confirmation required.\n\nType VALIDATED to record validation for item #${item.id}.`
    );
    if (phrase !== "VALIDATED") {
      onToast("Validation canceled.");
      return;
    }
    try {
      await api<TranscodeRunItem>(`/api/transcode-runs/${runId}/items/${item.id}/validate`, {
        method: "POST",
        body: JSON.stringify({ confirmation_text: phrase })
      });
      await refreshRun(runId);
      await refreshRuns();
      onToast("Published item marked validated.");
    } catch (error) {
      onToast(String(error));
      await refreshRun(runId);
    }
  }

  async function publishItem(runId: number, item: TranscodeRunItem) {
    const sizeSummary = [
      item.source_size_bytes != null ? `Original size: ${formatBytes(item.source_size_bytes)}` : null,
      item.output_size_bytes != null ? `Staged size: ${formatBytes(item.output_size_bytes)}` : null,
      item.source_size_bytes != null && item.output_size_bytes != null
        ? `Estimated change: ${formatSignedBytes(item.source_size_bytes - item.output_size_bytes)}`
        : null
    ].filter(Boolean).join("\n");
    const firstConfirmed = window.confirm(
      `Publish this staged output to the original location?\n\nOriginal live file:\n${item.source_path}\n\nStaged output:\n${item.target_path}\n\n${sizeSummary ? `${sizeSummary}\n\n` : ""}Media Atlas will move the original file into transcode backup storage, then copy the staged output into the original path.`
    );
    if (!firstConfirmed) return;
    const phrase = window.prompt(
      `Final confirmation required.\n\nThis replaces the live source file at:\n${item.source_path}\n\nType REPLACE to continue.`
    );
    if (phrase !== "REPLACE") {
      onToast("Publish canceled.");
      return;
    }
    try {
      onToast("Publish started. Progress will update in the run details.");
      await api<TranscodeRunItem>(`/api/transcode-runs/${runId}/items/${item.id}/publish`, {
        method: "POST",
        body: JSON.stringify({
          source_path: item.source_path,
          target_path: item.target_path,
          confirmation_text: phrase
        })
      });
      await refreshRun(runId);
      await refreshRuns();
      onToast("Published staged output. Original file was backed up.");
    } catch (error) {
      onToast(String(error));
      await refreshRun(runId);
    }
  }

  const selectedSavings = selected ? summarizeRunSavings(selected) : null;

  return (
    <section className="stack">
      <Panel title="Transcode Savings">
        <TranscodeSavingsPanel stats={stats} />
      </Panel>
      <Panel title="Runs">
        <div className="panelIntro">
          <p className="muted">Archived runs are hidden by default.</p>
          <label className="inlineCheck">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(event) => setShowArchived(event.target.checked)}
            />
            Show archived
          </label>
        </div>
        <table className="transcodeRunsTable mobileStackTable">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Started</th>
              <th>Stopped</th>
              <th>Duration</th>
              <th>Progress</th>
              <th>Items</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td className="mobileStackPrimary">
                  <strong>{run.name}</strong>
                  <div className="muted">Created {formatDateTime(run.created_at)}</div>
                  {run.archived_at && <div className="muted">Archived {formatDateTime(run.archived_at)}</div>}
                </td>
                <td>
                  <span className="mobileCellLabel">Status</span>
                  <StatusBadge status={run.status} />
                </td>
                <td>
                  <span className="mobileCellLabel">Started</span>
                  {formatDateTime(run.started_at)}
                </td>
                <td>
                  <span className="mobileCellLabel">Stopped</span>
                  {formatStopDateTime(run.finished_at, run.status)}
                </td>
                <td>
                  <span className="mobileCellLabel">Duration</span>
                  {formatRunDuration(run)}
                </td>
                <td>
                  <span className="mobileCellLabel">Progress</span>
                  <div className="scanProgressCell">
                    <div className="progressHeader">
                      <strong>{Math.round(run.progress_percent || 0)}%</strong>
                      {run.current_item_id && <span>item #{run.current_item_id}</span>}
                    </div>
                    <Progress value={run.progress_percent} />
                    {run.message && <div className="muted">{run.message}</div>}
                  </div>
                </td>
                <td>
                  <span className="mobileCellLabel">Items</span>
                  <div className="scanStats">
                    <span><strong>{run.completed_items}</strong> complete</span>
                    <span><strong>{run.failed_items}</strong> failed</span>
                    <span><strong>{run.canceled_items}</strong> canceled</span>
                    <span><strong>{run.total_items}</strong> total</span>
                  </div>
                </td>
                <td className="rowActions">
                  <button onClick={() => setSelectedId(run.id)}>Open</button>
                  {["queued", "running"].includes(run.status) && <button onClick={() => cancel(run.id)}>Cancel</button>}
                  {["failed", "canceled", "interrupted"].includes(run.status) && <button onClick={() => retry(run.id)}>Retry</button>}
                  {canArchiveRun(run) && (run.archived_at ? (
                    <button onClick={() => unarchiveRun(run.id)}>Unarchive</button>
                  ) : (
                    <button onClick={() => archiveRun(run.id)}>Archive</button>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      {selected && (
        <Panel title={`Run ${selected.id}: ${selected.name}`}>
          <div className="panelIntro">
            <p>{selected.message}</p>
            <div className="rowActions">
              {canCleanupRun(selected) && <button className="danger" onClick={() => cleanupRun(selected)}>Clean up validated items</button>}
              {canArchiveRun(selected) && (selected.archived_at ? (
                <button onClick={() => unarchiveRun(selected.id)}>Unarchive</button>
              ) : (
                <button onClick={() => archiveRun(selected.id)}>Archive</button>
              ))}
            </div>
          </div>
          <div className="metrics compact">
            <Metric label="Created" value={formatDateTime(selected.created_at)} />
            <Metric label="Started" value={formatDateTime(selected.started_at)} />
            <Metric label="Stopped" value={formatStopDateTime(selected.finished_at, selected.status)} />
            <Metric label="Duration" value={formatRunDuration(selected)} />
            <Metric label="Archived" value={formatNullableDateTime(selected.archived_at)} />
            <Metric label="Before" value={formatBytes(selectedSavings?.sourceSize || 0)} />
            <Metric label="After" value={formatBytes(selectedSavings?.outputSize || 0)} />
            <Metric label="Saved" value={formatSignedBytes(selectedSavings?.saved || 0)} />
            <Metric label="Measured Items" value={`${selectedSavings?.measuredItems || 0}`} />
          </div>
          <table className="transcodeRunItemsTable mobileStackTable">
            <thead>
              <tr>
                <th>Item</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Timing</th>
                <th>Target</th>
                <th>Verification</th>
                <th>Publish</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {selected.items?.map((item) => (
                <tr key={item.id}>
                  <td className="mobileStackPrimary"><strong>Item #{item.id}</strong></td>
                  <td>
                    <span className="mobileCellLabel">Status</span>
                    <StatusBadge status={item.status} />
                  </td>
                  <td>
                    <span className="mobileCellLabel">Progress</span>
                    <div className="scanProgressCell">
                      <div className="progressHeader">
                        <strong>{Math.round(item.progress_percent || 0)}%</strong>
                        {item.speed && <span>{item.speed}</span>}
                      </div>
                      <Progress value={item.progress_percent} />
                      {item.time_seconds != null && <div className="muted">encoded {formatElapsed(item.time_seconds * 1000)}</div>}
                    </div>
                  </td>
                  <td>
                    <span className="mobileCellLabel">Timing</span>
                    <div className="scanTiming">
                      <span>Started {formatDateTime(item.started_at)}</span>
                      <span>Stopped {formatStopDateTime(item.finished_at, item.status)}</span>
                      <span>Duration {formatItemDuration(item)}</span>
                    </div>
                  </td>
                  <td className="path">
                    <span className="mobileCellLabel">Target</span>
                    <strong>Staged</strong>
                    <span>{item.target_path}</span>
                    <strong>Original</strong>
                    <span>{item.source_path}</span>
                    <div className="scanTiming">
                      {item.source_size_bytes != null && <span>Before {formatBytes(item.source_size_bytes)}</span>}
                      {item.output_size_bytes != null && <span>After {formatBytes(item.output_size_bytes)}</span>}
                      {item.source_size_bytes != null && item.output_size_bytes != null && (
                        <span>Saved {formatSignedBytes(item.source_size_bytes - item.output_size_bytes)}</span>
                      )}
                    </div>
                  </td>
                  <td>
                    <span className="mobileCellLabel">Verification</span>
                    <div className="statusGrid">
                      <span>{item.verification_status} {item.verification_message}</span>
                    </div>
                  </td>
                  <td>
                    <span className="mobileCellLabel">Publish</span>
                    <div className="scanProgressCell">
                      {item.publish_status ? (
                        <>
                          <div className="progressHeader">
                            <StatusBadge status={item.publish_status} />
                            <strong>{Math.round(item.publish_progress_percent || 0)}%</strong>
                          </div>
                          <Progress value={item.publish_progress_percent || 0} />
                          <div className="scanTiming">
                            {item.publish_step && <span>Step {formatStatusLabel(item.publish_step)}</span>}
                            {item.publish_message && <span>{item.publish_message}</span>}
                            <span>
                              {formatBytes(item.publish_bytes_done || 0)} / {formatBytes(item.publish_bytes_total || 0)}
                            </span>
                            <span>Started {formatDateTime(item.publish_started_at)}</span>
                            <span>Stopped {formatStopDateTime(item.publish_finished_at, item.publish_status || undefined)}</span>
                            <span>Duration {formatPublishDuration(item)}</span>
                          </div>
                          {item.published_at && <span className="muted">Published {formatDateTime(item.published_at)}</span>}
                          {item.validated_at ? (
                            <span className="muted">Validated {formatDateTime(item.validated_at)}</span>
                          ) : item.published_at ? (
                            <span className="muted">Validation pending</span>
                          ) : null}
                          {item.validation_message && <span className="muted">{item.validation_message}</span>}
                          {item.published_backup_path && <span className="muted">Backup {item.published_backup_path}</span>}
                          {item.cleanup_status && (
                            <div className="scanTiming">
                              <span>Cleanup <StatusBadge status={item.cleanup_status} /></span>
                              {item.cleanup_message && <span>{item.cleanup_message}</span>}
                              {item.cleanup_finished_at && <span>Cleaned {formatDateTime(item.cleanup_finished_at)}</span>}
                              {item.staged_deleted_at && <span>Staged deleted {formatDateTime(item.staged_deleted_at)}</span>}
                              {item.backup_deleted_at && <span>Backup deleted {formatDateTime(item.backup_deleted_at)}</span>}
                            </div>
                          )}
                        </>
                      ) : (
                        <span className="muted">Not published</span>
                      )}
                    </div>
                  </td>
                  <td className="rowActions">
                    <button onClick={() => onOpenLog(selected.id, item.id)}>Log</button>
                    {canPublishItem(item) && <button className="danger" onClick={() => publishItem(selected.id, item)}>Publish</button>}
                    {canValidateItem(item) && <button onClick={() => validateItem(selected.id, item)}>Mark validated</button>}
                    {canCleanupItem(item) && <button className="danger" onClick={() => cleanupItem(selected.id, item)}>Clean up</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </section>
  );
}

function Logs({
  onToast,
  initialTarget
}: {
  onToast: (message: string) => void;
  initialTarget: LogTarget;
}) {
  const [tab, setTab] = useState<LogTab>(initialTarget.tab);
  return (
    <section className="stack">
      <div className="tabs logTabs" aria-label="Log source">
        <button className={tab === "application" ? "active" : ""} onClick={() => setTab("application")}>Application</button>
        <button className={tab === "transcodes" ? "active" : ""} onClick={() => setTab("transcodes")}>Transcodes</button>
        <button className={tab === "scans" ? "active" : ""} onClick={() => setTab("scans")}>Scans</button>
      </div>
      {tab === "application" && <ApplicationLogs onToast={onToast} />}
      {tab === "transcodes" && <TranscodeLogs onToast={onToast} initialTarget={initialTarget} />}
      {tab === "scans" && <ScanLogs onToast={onToast} initialTarget={initialTarget} />}
    </section>
  );
}

function ApplicationLogs({ onToast }: { onToast: (message: string) => void }) {
  const [result, setResult] = useState<ApplicationLogPage>({ items: [], limit: 200, truncated: false });
  const [level, setLevel] = useState("");
  const [loggerPrefix, setLoggerPrefix] = useState("");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(200);
  const [polling, setPolling] = useState(true);
  const [followLatest, setFollowLatest] = useState(true);
  const viewerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    refresh();
    if (!polling) return;
    const timer = window.setInterval(refresh, 3000);
    return () => window.clearInterval(timer);
  }, [level, loggerPrefix, query, limit, polling]);

  useEffect(() => {
    if (followLatest && viewerRef.current) {
      viewerRef.current.scrollTop = viewerRef.current.scrollHeight;
    }
  }, [result, followLatest]);

  async function refresh() {
    const params = new URLSearchParams({ limit: String(limit) });
    if (level) params.set("level", level);
    if (loggerPrefix.trim()) params.set("logger", loggerPrefix.trim());
    if (query.trim()) params.set("query", query.trim());
    try {
      setResult(await api<ApplicationLogPage>(`/api/logs/application?${params}`));
    } catch (error) {
      onToast(String(error));
    }
  }

  return (
    <Panel title="Application Logs">
      <div className="toolbar logFilters">
        <select value={level} onChange={(event) => setLevel(event.target.value)} aria-label="Filter by log level">
          <option value="">All levels</option>
          <option value="debug">Debug</option>
          <option value="info">Info</option>
          <option value="warning">Warning</option>
          <option value="error">Error</option>
          <option value="critical">Critical</option>
        </select>
        <input value={loggerPrefix} onChange={(event) => setLoggerPrefix(event.target.value)} placeholder="Logger prefix" />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search messages and context" />
        <select value={limit} onChange={(event) => setLimit(Number(event.target.value))} aria-label="Log entry limit">
          <option value={100}>Latest 100</option>
          <option value={200}>Latest 200</option>
          <option value={500}>Latest 500</option>
        </select>
        <button onClick={refresh}>Refresh</button>
      </div>
      <div className="panelIntro logOptions">
        <span className="muted">
          {result.items.length} entries{result.truncated ? " · older history was not loaded" : ""}
        </span>
        <div className="rowActions">
          <label className="inlineCheck"><input type="checkbox" checked={polling} onChange={(event) => setPolling(event.target.checked)} />Auto refresh</label>
          <label className="inlineCheck"><input type="checkbox" checked={followLatest} onChange={(event) => setFollowLatest(event.target.checked)} />Follow latest</label>
        </div>
      </div>
      <div className="applicationLogViewer" ref={viewerRef} aria-live="polite">
        {result.items.map((entry, index) => (
          <div className={`applicationLogLine level-${entry.level}`} key={`${entry.timestamp}-${entry.request_id || entry.logger}-${index}`}>
            <div className="applicationLogMeta">
              <time dateTime={entry.timestamp}>{formatDateTime(entry.timestamp)}</time>
              <Badge tone={logLevelTone(entry.level)}>{entry.level}</Badge>
              <span>{entry.logger}</span>
            </div>
            <div className="applicationLogMessage">{entry.message}</div>
            {applicationLogContext(entry) && <div className="applicationLogContext">{applicationLogContext(entry)}</div>}
            {entry.exception && <pre>{entry.exception}</pre>}
          </div>
        ))}
        {!result.items.length && <p className="muted">No application log entries match these filters.</p>}
      </div>
    </Panel>
  );
}

function TranscodeLogs({
  onToast,
  initialTarget
}: {
  onToast: (message: string) => void;
  initialTarget: LogTarget;
}) {
  const [runs, setRuns] = useState<TranscodeRun[]>([]);
  const [runId, setRunId] = useState(initialTarget.tab === "transcodes" ? initialTarget.runId || 0 : 0);
  const [itemId, setItemId] = useState(initialTarget.tab === "transcodes" ? initialTarget.itemId || 0 : 0);
  const [run, setRun] = useState<TranscodeRun | null>(null);
  const [log, setLog] = useState("");
  const [polling, setPolling] = useState(true);
  const [followLatest, setFollowLatest] = useState(true);
  const viewerRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    refreshRuns();
    const timer = window.setInterval(refreshRuns, 5000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!runId) return;
    refreshRun(runId);
    if (!polling) return;
    const timer = window.setInterval(() => refreshRun(runId), 2000);
    return () => window.clearInterval(timer);
  }, [runId, polling]);

  useEffect(() => {
    if (!runId || !itemId) {
      setLog("");
      return;
    }
    refreshLog(runId, itemId);
    if (!polling) return;
    const timer = window.setInterval(() => refreshLog(runId, itemId), 2000);
    return () => window.clearInterval(timer);
  }, [runId, itemId, polling]);

  useEffect(() => {
    if (followLatest && viewerRef.current) {
      viewerRef.current.scrollTop = viewerRef.current.scrollHeight;
    }
  }, [log, followLatest]);

  async function refreshRuns() {
    try {
      const nextRuns = await api<TranscodeRun[]>("/api/transcode-runs?limit=200&include_archived=true");
      setRuns(nextRuns);
      setRunId((current) => current || nextRuns[0]?.id || 0);
    } catch (error) {
      onToast(String(error));
    }
  }

  async function refreshRun(id: number) {
    try {
      const nextRun = await api<TranscodeRun>(`/api/transcode-runs/${id}`);
      setRun(nextRun);
      setItemId((current) => {
        if (current && nextRun.items?.some((item) => item.id === current)) return current;
        if (nextRun.current_item_id && nextRun.items?.some((item) => item.id === nextRun.current_item_id)) return nextRun.current_item_id;
        return nextRun.items?.[0]?.id || 0;
      });
    } catch (error) {
      onToast(String(error));
    }
  }

  async function refreshLog(nextRunId = runId, nextItemId = itemId) {
    if (!nextRunId || !nextItemId) return;
    try {
      setLog(await apiText(`/api/transcode-runs/${nextRunId}/items/${nextItemId}/log`));
    } catch (error) {
      onToast(String(error));
    }
  }

  const item = run?.items?.find((candidate) => candidate.id === itemId);
  return (
    <Panel title="Transcode Logs">
      <div className="toolbar logSourceSelectors">
        <label>
          Run
          <select value={runId} onChange={(event) => { setRunId(Number(event.target.value)); setItemId(0); }}>
            {!runs.length && <option value={0}>No transcode runs</option>}
            {runs.map((candidate) => <option key={candidate.id} value={candidate.id}>#{candidate.id} · {candidate.name} · {formatStatusLabel(candidate.status)}</option>)}
          </select>
        </label>
        <label>
          Item
          <select value={itemId} onChange={(event) => setItemId(Number(event.target.value))} disabled={!run?.items?.length}>
            {!run?.items?.length && <option value={0}>No run items</option>}
            {run?.items?.map((candidate) => <option key={candidate.id} value={candidate.id}>#{candidate.id} · {planPathName(candidate.source_path)} · {formatStatusLabel(candidate.status)}</option>)}
          </select>
        </label>
        <button onClick={() => { if (runId) refreshRun(runId); refreshLog(); }}>Refresh</button>
      </div>
      <div className="panelIntro logOptions">
        <div className="statusGrid">
          {run && <div><StatusBadge status={run.status} /> <span className="muted">Run #{run.id} · {run.message}</span></div>}
          {item && <span className="path">{item.source_path}</span>}
        </div>
        <div className="rowActions">
          <label className="inlineCheck"><input type="checkbox" checked={polling} onChange={(event) => setPolling(event.target.checked)} />Auto refresh</label>
          <label className="inlineCheck"><input type="checkbox" checked={followLatest} onChange={(event) => setFollowLatest(event.target.checked)} />Follow latest</label>
        </div>
      </div>
      <pre className="log logViewer" ref={viewerRef}>{log || (item ? "No transcode output has been recorded for this item yet." : "Select a transcode run and item.")}</pre>
    </Panel>
  );
}

function ScanLogs({
  onToast,
  initialTarget
}: {
  onToast: (message: string) => void;
  initialTarget: LogTarget;
}) {
  const [scans, setScans] = useState<ScanJob[]>([]);
  const [scanId, setScanId] = useState(initialTarget.tab === "scans" ? initialTarget.scanId || 0 : 0);
  const [scan, setScan] = useState<ScanJob | null>(null);
  const [polling, setPolling] = useState(true);

  useEffect(() => {
    refreshScans();
    const timer = window.setInterval(refreshScans, 5000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!scanId) return;
    refreshScan(scanId);
    if (!polling) return;
    const timer = window.setInterval(() => refreshScan(scanId), 2000);
    return () => window.clearInterval(timer);
  }, [scanId, polling]);

  async function refreshScans() {
    try {
      const nextScans = await api<ScanJob[]>("/api/scans?limit=100");
      setScans(nextScans);
      setScanId((current) => current || nextScans[0]?.id || 0);
    } catch (error) {
      onToast(String(error));
    }
  }

  async function refreshScan(id: number) {
    try {
      setScan(await api<ScanJob>(`/api/scans/${id}`));
    } catch (error) {
      onToast(String(error));
    }
  }

  const progress = scan ? scanProgress(scan) : null;
  return (
    <Panel title="Scan Logs">
      <div className="toolbar logSourceSelectors">
        <label>
          Scan
          <select value={scanId} onChange={(event) => setScanId(Number(event.target.value))}>
            {!scans.length && <option value={0}>No scans</option>}
            {scans.map((candidate) => <option key={candidate.id} value={candidate.id}>#{candidate.id} · {formatStatusLabel(candidate.status)} · {formatDateTime(candidate.created_at)}</option>)}
          </select>
        </label>
        <button onClick={() => scanId && refreshScan(scanId)}>Refresh</button>
        <label className="inlineCheck"><input type="checkbox" checked={polling} onChange={(event) => setPolling(event.target.checked)} />Auto refresh</label>
      </div>
      {scan && (
        <div className="scanLogSummary">
          <div className="progressHeader"><StatusBadge status={scan.status} /><strong>{progress?.percent || 0}%</strong></div>
          <Progress value={progress?.percent || 0} />
          <span>{scan.message}</span>
          {scan.current_path && <span className="path">Current: {scan.current_path}</span>}
          <span className="muted">{scan.files_probed} probed · {scan.files_skipped} skipped · {scan.files_failed} failed · {scan.total_files_discovered} discovered</span>
        </div>
      )}
      <div className="scanErrorList">
        {scan?.errors?.map((error) => (
          <article key={error.id}>
            <div className="panelIntro">
              <div><Badge tone="bad">{error.error_type}</Badge> <span className="muted">{formatDateTime(error.created_at)}</span></div>
              {error.ffprobe_exit_code != null && <span>ffprobe exit {error.ffprobe_exit_code}</span>}
            </div>
            <div className="path">{error.path}</div>
            <p>{error.error_message}</p>
            {error.stderr && <pre className="log">{error.stderr}</pre>}
          </article>
        ))}
        {scan && !scan.errors?.length && <p className="muted">No errors were recorded for this scan.</p>}
        {!scan && <p className="muted">Select a scan to inspect its status and errors.</p>}
      </div>
    </Panel>
  );
}

function applicationLogContext(entry: ApplicationLogEntry) {
  return [
    entry.method && entry.path ? `${entry.method} ${entry.path}` : entry.path,
    entry.status_code != null ? `status ${entry.status_code}` : null,
    entry.duration_ms != null ? `${entry.duration_ms} ms` : null,
    entry.request_id ? `request ${entry.request_id}` : null,
    entry.job_id != null ? `job #${entry.job_id}` : null,
    entry.run_id != null ? `run #${entry.run_id}` : null
  ].filter(Boolean).join(" · ");
}

function logLevelTone(level: string): "good" | "warn" | "bad" | "muted" {
  if (["error", "critical"].includes(level)) return "bad";
  if (level === "warning") return "warn";
  if (level === "info") return "good";
  return "muted";
}

function planPathName(path: string) {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

function AdminStatusPage({ onToast }: { onToast: (message: string) => void }) {
  const [status, setStatus] = useState<AdminStatus | null>(null);
  const [stats, setStats] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 10000);
    return () => window.clearInterval(timer);
  }, []);

  async function refresh() {
    try {
      const [nextStatus, nextStats] = await Promise.all([
        api<AdminStatus>("/api/admin/status"),
        api<Record<string, any>>("/api/admin/stats")
      ]);
      setStatus(nextStatus);
      setStats(nextStats);
    } catch (error) {
      onToast(String(error));
    }
  }

  async function runRetention() {
    try {
      const result = await api<Record<string, number>>("/api/admin/retention/run", { method: "POST", body: "{}" });
      onToast(`Retention complete: ${result.logs_removed || 0} log files removed.`);
      await refresh();
    } catch (error) {
      onToast(String(error));
    }
  }

  if (!status) {
    return <Panel title="Admin Status"><p className="muted">Loading status.</p></Panel>;
  }

  const readiness = status.readiness;
  const runtimeConfig = status.runtime_config;
  return (
    <section className="stack">
      <div className="metrics">
        <Metric label="Readiness" value={<StatusBadge status={readiness.status} />} />
        <Metric label="Database" value={readiness.database.ok ? "Available" : "Unavailable"} />
        <Metric label="Migrations" value={readiness.migrations.ok ? "OK" : "Failed"} />
        <Metric label="Plex" value={stats?.plex?.configured ? "Configured" : "Optional"} />
      </div>
      {readiness.config_warnings.length > 0 && (
        <Panel title="Config Warnings">
          <ul>{readiness.config_warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </Panel>
      )}
      <Panel title="Runtime Configuration">
        <div className="statusGrid">
          <div className="storageRow">
            <strong>Version</strong>
            <span>{status.version.version}</span>
            <span>Image</span>
            <span className="path">{status.version.image_tag}</span>
          </div>
          <div className="storageRow">
            <strong>Build</strong>
            <span className="path">{status.version.build_date}</span>
            <span>Git SHA</span>
            <span className="path">{status.version.git_sha}</span>
          </div>
          <div className="storageRow">
            <strong>Bind address</strong>
            <span className="path">{runtimeConfig.host}:{runtimeConfig.port}</span>
            <span>LAN mode</span>
            <Badge tone={runtimeConfig.allow_lan ? "info" : "muted"}>{runtimeConfig.allow_lan ? "Enabled" : "Disabled"}</Badge>
          </div>
          <div className="storageRow">
            <strong>Auth mode</strong>
            <span>{runtimeConfig.auth.mode}</span>
            <span>No-auth LAN acknowledged</span>
            <Badge tone={runtimeConfig.operations.acknowledge_auth_disabled_lan ? "good" : "warn"}>
              {runtimeConfig.operations.acknowledge_auth_disabled_lan ? "True" : "False"}
            </Badge>
          </div>
          <div className="storageRow">
            <strong>Unsafe bind failure</strong>
            <span>{runtimeConfig.operations.fail_unsafe_bind ? "Enabled" : "Disabled"}</span>
            <span>Allowed origins</span>
            <span className="path">{runtimeConfig.operations.allowed_origins.join(", ") || "None"}</span>
          </div>
        </div>
      </Panel>
      <div className="grid two">
        <Panel title="Storage">
          <div className="statusGrid">
            {Object.entries(status.storage).map(([key, value]) => (
              <div className="storageRow" key={key}>
                <strong>{key}</strong>
                <span className="path">{value.path}</span>
                <span>{formatBytes(value.free_bytes || 0)} free</span>
                <Badge tone={value.ok ? "good" : "bad"}>{value.ok ? "OK" : "Low or unavailable"}</Badge>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Tools And Paths">
          <div className="statusGrid">
            {Object.entries(readiness.tools).map(([key, value]) => (
              <div className="storageRow" key={key}>
                <strong>{key}</strong>
                <Badge tone={value.available ? "good" : "bad"}>{value.available ? "Available" : "Missing"}</Badge>
                <span className="muted">{value.version || value.command}</span>
              </div>
            ))}
            {Object.entries(readiness.paths).map(([key, value]) => (
              <div className="storageRow" key={key}>
                <strong>{key}</strong>
                <span className="path">{value.path}</span>
                <Badge tone={value.writable ? "good" : "bad"}>{value.writable ? "Writable" : "Blocked"}</Badge>
              </div>
            ))}
          </div>
        </Panel>
      </div>
      <Panel title="Job State">
        <div className="jobStateGrid">
          {Object.entries(readiness.jobs).map(([group, counts]) => (
            <div key={group}>
              <strong>{group}</strong>
              <div className="scanStats">
                {Object.entries(counts).length ? Object.entries(counts).map(([jobStatus, count]) => (
                  <span key={jobStatus}><strong>{count}</strong> {jobStatus}</span>
                )) : <span className="muted">No jobs</span>}
              </div>
            </div>
          ))}
        </div>
      </Panel>
      <div className="grid two">
        <Panel title="Recent Failures">
          <FailureList title="Scans" rows={status.recent_failures.scans} />
          <FailureList title="Transcodes" rows={status.recent_failures.transcodes} />
          <FailureList title="Plex Syncs" rows={status.recent_failures.plex_syncs} />
        </Panel>
        <Panel title="Maintenance">
          <div className="rowActions">
            <a className="button" href="/api/admin/database-backup">Download database backup</a>
            <a className="button" href="/api/admin/diagnostics">Download diagnostics</a>
            <button onClick={runRetention}>Run retention cleanup</button>
          </div>
          <pre className="json">{JSON.stringify({ version: status.version, auth: status.auth, runtime_config: runtimeConfig, retention: status.retention, migrations: readiness.migrations }, null, 2)}</pre>
        </Panel>
      </div>
    </section>
  );
}

function FailureList({ title, rows }: { title: string; rows: Array<{ id: number; status: string; message?: string | null; error_message?: string | null }> }) {
  return (
    <div className="failureList">
      <h3>{title}</h3>
      {rows.length ? rows.map((row) => (
        <div key={`${title}-${row.id}`} className="failureRow">
          <strong>#{row.id}</strong>
          <StatusBadge status={row.status} />
          <span className="muted">{row.error_message || row.message || "No message"}</span>
        </div>
      )) : <p className="muted">No recent failures.</p>}
    </div>
  );
}

function Settings({ onToast }: { onToast: (message: string) => void }) {
  const [settings, setSettings] = useState<Record<string, any> | null>(null);
  const [plex, setPlex] = useState<PlexSettings | null>(null);
  const [libraries, setLibraries] = useState<PlexLibrary[]>([]);
  const [jobs, setJobs] = useState<PlexSyncJob[]>([]);
  const [token, setToken] = useState("");
  useEffect(() => {
    refresh();
  }, []);

  async function refresh() {
    const [runtimeSettings, plexSettings, plexLibraries, syncJobs] = await Promise.all([
      api<Record<string, any>>("/api/settings"),
      api<PlexSettings>("/api/plex/settings"),
      api<PlexLibrary[]>("/api/plex/libraries"),
      api<PlexSyncJob[]>("/api/plex/sync-jobs")
    ]);
    setSettings(runtimeSettings);
    setPlex(plexSettings);
    setLibraries(plexLibraries);
    setJobs(syncJobs);
  }

  async function savePlex() {
    if (!plex) return;
    try {
      const payload: Record<string, unknown> = {
        enabled: plex.enabled,
        server_url: plex.server_url,
        selected_library_keys: plex.selected_library_keys,
        timeout_seconds: plex.timeout_seconds,
        path_mappings: plex.path_mappings
      };
      if (token) payload.token = token;
      const result = await api<PlexSettings>("/api/plex/settings", {
        method: "PUT",
        body: JSON.stringify(payload)
      });
      setToken("");
      setPlex(result);
      onToast("Plex settings saved.");
    } catch (error) {
      onToast(String(error));
    }
  }

  async function testPlex() {
    try {
      const result = await api<{ library_count: number }>("/api/plex/test-connection", { method: "POST", body: "{}" });
      onToast(`Connected to Plex. Found ${result.library_count} libraries.`);
    } catch (error) {
      onToast(String(error));
    }
  }

  async function refreshLibraries() {
    try {
      setLibraries(await api<PlexLibrary[]>("/api/plex/libraries?refresh=true"));
      onToast("Plex libraries refreshed.");
    } catch (error) {
      onToast(String(error));
    }
  }

  async function startSync() {
    try {
      await api<PlexSyncJob>("/api/plex/sync", { method: "POST", body: "{}" });
      await refresh();
      onToast("Plex sync queued.");
    } catch (error) {
      onToast(String(error));
    }
  }

  async function cancelSync(jobId: number) {
    await api(`/api/plex/sync-jobs/${jobId}/cancel`, { method: "POST", body: "{}" });
    await refresh();
  }

  async function retrySync(jobId: number) {
    await api(`/api/plex/sync-jobs/${jobId}/retry`, { method: "POST", body: "{}" });
    await refresh();
    onToast("Plex sync retry queued.");
  }

  function updatePlex(next: Partial<PlexSettings>) {
    setPlex((current) => current ? { ...current, ...next } : current);
  }

  function toggleLibrary(key: string) {
    if (!plex) return;
    const selected = new Set(plex.selected_library_keys);
    selected.has(key) ? selected.delete(key) : selected.add(key);
    updatePlex({ selected_library_keys: Array.from(selected) });
  }

  function updateMapping(index: number, key: keyof PlexPathMapping, value: string) {
    if (!plex) return;
    const mappings = [...plex.path_mappings];
    mappings[index] = { ...mappings[index], [key]: value };
    updatePlex({ path_mappings: mappings });
  }

  function addMapping() {
    if (!plex) return;
    updatePlex({
      path_mappings: [...plex.path_mappings, { plex_path_prefix: "", media_atlas_path_prefix: "/media" }]
    });
  }

  function removeMapping(index: number) {
    if (!plex) return;
    updatePlex({ path_mappings: plex.path_mappings.filter((_, current) => current !== index) });
  }

  return (
    <section className="stack">
      <Panel title="Plex">
        {plex && (
          <div className="stack">
            <div className="formGrid plexSettingsGrid">
              <label>
                Enabled
                <select value={plex.enabled ? "true" : "false"} onChange={(event) => updatePlex({ enabled: event.target.value === "true" })}>
                  <option value="true">Enabled</option>
                  <option value="false">Disabled</option>
                </select>
              </label>
              <label>
                Server URL
                <input value={plex.server_url || ""} onChange={(event) => updatePlex({ server_url: event.target.value })} placeholder="http://192.168.1.106:32400" />
              </label>
              <label>
                Token {plex.token_configured && <span className="muted">{plex.token_hint}</span>}
                <input value={token} onChange={(event) => setToken(event.target.value)} placeholder={plex.token_configured ? "Leave blank to keep current token" : "Plex token"} />
              </label>
              <label>
                Timeout seconds
                <input type="number" min="1" max="120" value={plex.timeout_seconds} onChange={(event) => updatePlex({ timeout_seconds: Number(event.target.value) || 10 })} />
              </label>
            </div>
            <div className="rowActions">
              <button className="primary" onClick={savePlex}>Save Plex settings</button>
              <button onClick={testPlex}>Test connection</button>
              <button onClick={refreshLibraries}>Refresh libraries</button>
              <button onClick={startSync}>Start sync</button>
            </div>
            <div className="grid two">
              <div>
                <h3>Libraries</h3>
                <div className="checkList">
                  {libraries.length ? libraries.map((library) => (
                    <label key={library.section_key} className="checkRow">
                      <input
                        type="checkbox"
                        checked={plex.selected_library_keys.includes(library.section_key)}
                        onChange={() => toggleLibrary(library.section_key)}
                      />
                      <span>{library.title} <span className="muted">{library.type}</span></span>
                    </label>
                  )) : <p className="muted">No Plex libraries stored yet.</p>}
                </div>
              </div>
              <div>
                <h3>Path mappings</h3>
                <div className="mappingList">
                  {plex.path_mappings.map((mapping, index) => (
                    <div className="mappingRow" key={index}>
                      <input value={mapping.plex_path_prefix} onChange={(event) => updateMapping(index, "plex_path_prefix", event.target.value)} placeholder="/mnt/media" />
                      <input value={mapping.media_atlas_path_prefix} onChange={(event) => updateMapping(index, "media_atlas_path_prefix", event.target.value)} placeholder="/media" />
                      <button className="danger" onClick={() => removeMapping(index)}>Remove</button>
                    </div>
                  ))}
                  <button onClick={addMapping}>Add mapping</button>
                </div>
              </div>
            </div>
          </div>
        )}
      </Panel>
      <Panel title="Recent Plex Sync Jobs">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Matches</th>
              <th>Message</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>{job.id}</td>
                <td><StatusBadge status={job.status} /></td>
                <td>
                  <div className="progressHeader">
                    <strong>{plexSyncPercent(job)}%</strong>
                    <span>{job.processed_items} / {job.total_items || "?"}</span>
                  </div>
                  <Progress value={plexSyncPercent(job)} />
                </td>
                <td>{job.matched_files} matched, {job.unmatched_files} file gaps, {job.unmatched_parts} Plex gaps</td>
                <td>{job.error_message || job.message}</td>
                <td className="rowActions">
                  {["queued", "running"].includes(job.status) && <button onClick={() => cancelSync(job.id)}>Cancel</button>}
                  {["failed", "canceled", "interrupted"].includes(job.status) && <button onClick={() => retrySync(job.id)}>Retry</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <RetentionSettingsPanel onToast={onToast} />
      <Panel title="Runtime Settings">
        <pre className="json">{JSON.stringify(settings, null, 2)}</pre>
      </Panel>
    </section>
  );
}

function RetentionSettingsPanel({ onToast }: { onToast: (message: string) => void }) {
  const [retentionSettings, setRetentionSettings] = useState<RetentionSettings | null>(null);
  const [connections, setConnections] = useState<RetentionConnection[]>([]);
  const [serviceType, setServiceType] = useState<"seerr" | "sonarr" | "radarr">("radarr");
  const [name, setName] = useState("Radarr");
  const [serverUrl, setServerUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [seerrServiceId, setSeerrServiceId] = useState("");

  useEffect(() => {
    refresh();
  }, []);

  async function refresh() {
    try {
      const [nextSettings, nextConnections] = await Promise.all([
        api<RetentionSettings>("/api/retention/settings"),
        api<RetentionConnection[]>("/api/retention/connections")
      ]);
      setRetentionSettings(nextSettings);
      setConnections(nextConnections);
    } catch (error) {
      onToast(String(error));
    }
  }

  async function saveSettings() {
    if (!retentionSettings) return;
    try {
      setRetentionSettings(await api<RetentionSettings>("/api/retention/settings", {
        method: "PUT",
        body: JSON.stringify(retentionSettings)
      }));
      onToast("Retention settings saved.");
    } catch (error) {
      onToast(String(error));
    }
  }

  async function addConnection() {
    try {
      await api<RetentionConnection>("/api/retention/connections", {
        method: "POST",
        body: JSON.stringify({
          service_type: serviceType,
          name,
          server_url: serverUrl,
          api_key: apiKey,
          enabled: true,
          seerr_service_id: seerrServiceId ? Number(seerrServiceId) : null,
          path_mappings: []
        })
      });
      setServerUrl("");
      setApiKey("");
      setSeerrServiceId("");
      await refresh();
      onToast(`${name} connection added.`);
    } catch (error) {
      onToast(String(error));
    }
  }

  function changeService(value: "seerr" | "sonarr" | "radarr") {
    setServiceType(value);
    setName(value === "seerr" ? "Seerr" : value === "sonarr" ? "Sonarr" : "Radarr");
  }

  return (
    <Panel title="Media retention review">
      <div className="stack retentionSettings">
        <p className="muted">Retention analysis uses direct REST APIs. API keys are write-only after saving. Add the Seerr service ID to each Arr connection so standard and 4K copies map to the correct instance.</p>
        {retentionSettings && (
          <div className="formGrid retentionScheduleGrid">
            <label>Minimum unwatched days<input type="number" min="1" max="3650" value={retentionSettings.minimum_unwatched_days} onChange={(event) => setRetentionSettings({ ...retentionSettings, minimum_unwatched_days: Number(event.target.value) || 90 })} /></label>
            <label>Daily schedule<select value={retentionSettings.schedule_enabled ? "true" : "false"} onChange={(event) => setRetentionSettings({ ...retentionSettings, schedule_enabled: event.target.value === "true" })}><option value="false">Disabled</option><option value="true">Enabled</option></select></label>
            <label>Server-local time<input type="time" value={retentionSettings.schedule_time} onChange={(event) => setRetentionSettings({ ...retentionSettings, schedule_time: event.target.value })} /></label>
            <label>API timeout seconds<input type="number" min="1" max="120" value={retentionSettings.timeout_seconds} onChange={(event) => setRetentionSettings({ ...retentionSettings, timeout_seconds: Number(event.target.value) || 20 })} /></label>
            <button className="primary" onClick={saveSettings}>Save schedule</button>
          </div>
        )}
        <div>
          <h3>Connections</h3>
          <div className="retentionConnectionList">
            {connections.map((connection) => (
              <RetentionConnectionEditor key={`${connection.id}-${connection.updated_at}`} connection={connection} onChanged={refresh} onToast={onToast} />
            ))}
            {!connections.length && <p className="muted">No retention sources configured.</p>}
          </div>
        </div>
        <div>
          <h3>Add connection</h3>
          <div className="formGrid retentionConnectionForm">
            <label>Service<select value={serviceType} onChange={(event) => changeService(event.target.value as "seerr" | "sonarr" | "radarr")}><option value="seerr">Seerr</option><option value="sonarr">Sonarr</option><option value="radarr">Radarr</option></select></label>
            <label>Name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
            <label>Server URL<input value={serverUrl} onChange={(event) => setServerUrl(event.target.value)} placeholder="http://service:port" /></label>
            <label>API key<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></label>
            {serviceType !== "seerr" && <label>Seerr service ID<input type="number" value={seerrServiceId} onChange={(event) => setSeerrServiceId(event.target.value)} placeholder="Required for multiple instances" /></label>}
            <button className="primary" disabled={!name || !serverUrl || !apiKey} onClick={addConnection}>Add connection</button>
          </div>
        </div>
      </div>
    </Panel>
  );
}

function RetentionConnectionEditor({
  connection,
  onChanged,
  onToast
}: {
  connection: RetentionConnection;
  onChanged: () => Promise<void>;
  onToast: (message: string) => void;
}) {
  const [draft, setDraft] = useState(connection);
  const [apiKey, setApiKey] = useState("");

  function update(next: Partial<RetentionConnection>) {
    setDraft((current) => ({ ...current, ...next }));
  }

  function updateMapping(index: number, key: keyof RetentionPathMapping, value: string) {
    const mappings = [...draft.path_mappings];
    mappings[index] = { ...mappings[index], [key]: value };
    update({ path_mappings: mappings });
  }

  async function save() {
    try {
      const payload: Record<string, unknown> = {
        name: draft.name,
        server_url: draft.server_url,
        enabled: draft.enabled,
        seerr_service_id: draft.seerr_service_id,
        path_mappings: draft.path_mappings
      };
      if (apiKey) payload.api_key = apiKey;
      await api(`/api/retention/connections/${connection.id}`, { method: "PATCH", body: JSON.stringify(payload) });
      setApiKey("");
      await onChanged();
      onToast(`${draft.name} saved.`);
    } catch (error) {
      onToast(String(error));
    }
  }

  async function test() {
    try {
      if (apiKey) await save();
      const result = await api<{ version?: string | null }>(`/api/retention/connections/${connection.id}/test`, { method: "POST", body: "{}" });
      onToast(`Connected to ${draft.name}${result.version ? ` ${result.version}` : ""}.`);
    } catch (error) {
      onToast(String(error));
    }
  }

  async function remove() {
    if (!window.confirm(`Remove ${draft.name}? Connections with analysis history must be disabled instead.`)) return;
    try {
      await api(`/api/retention/connections/${connection.id}`, { method: "DELETE" });
      await onChanged();
      onToast(`${draft.name} removed.`);
    } catch (error) {
      onToast(String(error));
    }
  }

  return (
    <div className="retentionConnectionCard">
      <div className="panelIntro">
        <div><strong>{draft.name}</strong> <Badge tone={draft.enabled ? "good" : "muted"}>{draft.service_type}</Badge></div>
        <span className="muted">API key {draft.api_key_configured ? draft.api_key_hint : "not configured"}</span>
      </div>
      <div className="formGrid retentionConnectionEditGrid">
        <label>Name<input value={draft.name} onChange={(event) => update({ name: event.target.value })} /></label>
        <label>Server URL<input value={draft.server_url} onChange={(event) => update({ server_url: event.target.value })} /></label>
        <label>Enabled<select value={draft.enabled ? "true" : "false"} onChange={(event) => update({ enabled: event.target.value === "true" })}><option value="true">Enabled</option><option value="false">Disabled</option></select></label>
        {draft.service_type !== "seerr" && <label>Seerr service ID<input type="number" value={draft.seerr_service_id ?? ""} onChange={(event) => update({ seerr_service_id: event.target.value ? Number(event.target.value) : null })} /></label>}
        <label>Replace API key<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="Leave blank to keep current key" /></label>
      </div>
      {draft.service_type !== "seerr" && (
        <div>
          <strong>Service path mappings</strong>
          <p className="muted">Map paths reported by this Arr instance into the Media Atlas/Plex normalized path namespace.</p>
          <div className="mappingList">
            {draft.path_mappings.map((mapping, index) => (
              <div className="mappingRow" key={index}>
                <input value={mapping.source_path_prefix} onChange={(event) => updateMapping(index, "source_path_prefix", event.target.value)} placeholder="/arr/media" />
                <input value={mapping.media_atlas_path_prefix} onChange={(event) => updateMapping(index, "media_atlas_path_prefix", event.target.value)} placeholder="/media" />
                <button className="danger" onClick={() => update({ path_mappings: draft.path_mappings.filter((_, current) => current !== index) })}>Remove</button>
              </div>
            ))}
            <button onClick={() => update({ path_mappings: [...draft.path_mappings, { source_path_prefix: "", media_atlas_path_prefix: "/media" }] })}>Add mapping</button>
          </div>
        </div>
      )}
      <div className="rowActions"><button className="primary" onClick={save}>Save</button><button onClick={test}>Test</button><button className="danger" onClick={remove}>Remove</button></div>
    </div>
  );
}

function MediaTable({ files, onOpen }: { files: MediaFile[]; onOpen: (id: number) => void }) {
  return (
    <Panel title="Media Files">
      <table className="mediaFilesTable mobileStackTable">
        <thead>
          <tr>
            <th>File</th>
            <th>Root</th>
            <th>Size</th>
            <th>Plex</th>
            <th>Video</th>
            <th>Audio</th>
            <th>Bitrate</th>
            <th>Recommendation</th>
          </tr>
        </thead>
        <tbody>
          {files.map((file) => (
            <tr key={file.id} onClick={() => onOpen(file.id)}>
              <td className="mobileStackPrimary">
                <strong>{file.filename}</strong>
                <div className="path">{file.path}</div>
              </td>
              <td>
                <span className="mobileCellLabel">Root</span>
                {file.root_name}
              </td>
              <td>
                <span className="mobileCellLabel">Size</span>
                {formatBytes(file.size_bytes)}
              </td>
              <td>
                <span className="mobileCellLabel">Plex</span>
                {file.plex?.match_status === "matched" ? (
                  <>
                    <strong>{plexDisplayTitle(file)}</strong>
                    <div className="muted">
                      {file.plex.library_section_title || "Plex"} {file.plex.year ? `· ${file.plex.year}` : ""} {file.plex.watched ? "· watched" : ""}
                    </div>
                  </>
                ) : (
                  <Badge tone="muted">Unmatched</Badge>
                )}
              </td>
              <td>
                <span className="mobileCellLabel">Video</span>
                {file.resolution_bucket || "Unknown"} {file.primary_video_codec || ""} {file.is_hdr && <Badge tone="warn">HDR</Badge>}
              </td>
              <td>
                <span className="mobileCellLabel">Audio</span>
                {file.primary_audio_codec || "Unknown"} {file.audio_stream_count > 1 && `+${file.audio_stream_count - 1}`}
              </td>
              <td>
                <span className="mobileCellLabel">Bitrate</span>
                {file.bitrate_mbps ? `${file.bitrate_mbps} Mbps` : "Unknown"}
              </td>
              <td>
                <span className="mobileCellLabel">Recommendation</span>
                <Badge tone={toneFor(file.recommendation_category)}>{file.recommendation_category || "Unknown"}</Badge>
                <div className="muted">{file.recommendation_summary}</div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function DetailDrawer({ file, onClose }: { file: MediaFile; onClose: () => void }) {
  return (
    <div className="drawerBackdrop" onClick={onClose}>
      <aside className="drawer" onClick={(event) => event.stopPropagation()}>
        <div className="drawerHeader">
          <div>
            <h2>{file.filename}</h2>
            <p className="path">{file.path}</p>
          </div>
          <button onClick={onClose}>Close</button>
        </div>
        <div className="metrics compact">
          <Metric label="Size" value={formatBytes(file.size_bytes)} />
          <Metric label="Duration" value={formatDuration(file.duration_seconds || 0)} />
          <Metric label="Video" value={`${file.resolution_bucket || "?"} ${file.primary_video_codec || ""}`} />
          <Metric label="Audio" value={file.primary_audio_codec || "Unknown"} />
        </div>
        <Panel title="Recommendation">
          <p>{file.recommendation_summary}</p>
          <ul>{file.recommendation_reasons?.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          <ul>{file.recommendation_warnings?.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </Panel>
        <Panel title="Plex Metadata">
          {file.plex?.match_status === "matched" ? (
            <div className="detailGrid">
              <Metric label="Title" value={plexDisplayTitle(file)} />
              <Metric label="Library" value={file.plex.library_section_title || "Unknown"} />
              <Metric label="Type" value={file.plex.type || "Unknown"} />
              <Metric label="Year" value={file.plex.year || "Unknown"} />
              <Metric label="Watched" value={file.plex.watched ? "Yes" : "No"} />
              <Metric label="Rating Key" value={file.plex.rating_key || "Unknown"} />
              <div>
                <strong>Collections</strong>
                <p className="muted">{formatList(file.plex.collections)}</p>
              </div>
              <div>
                <strong>Genres</strong>
                <p className="muted">{formatList(file.plex.genres)}</p>
              </div>
              <div>
                <strong>Labels</strong>
                <p className="muted">{formatList(file.plex.labels)}</p>
              </div>
              <div>
                <strong>Plex path</strong>
                <p className="path">{file.plex.file_path}</p>
              </div>
              {file.plex.summary && <p>{file.plex.summary}</p>}
            </div>
          ) : (
            <p className="muted">No matched Plex item for this file.</p>
          )}
        </Panel>
        <Panel title="Streams">
          <pre className="json">{JSON.stringify(file.streams, null, 2)}</pre>
        </Panel>
        <Panel title="Raw ffprobe JSON">
          <pre className="json">{file.raw_probe_json ? JSON.stringify(JSON.parse(file.raw_probe_json), null, 2) : "No raw probe JSON stored."}</pre>
        </Panel>
      </aside>
    </div>
  );
}

function PlexStatusPanel({ status }: { status?: PlexStatus }) {
  if (!status) {
    return <p className="muted">Plex status unavailable.</p>;
  }
  return (
    <div className="statusGrid">
      <Badge tone={status.configured ? "good" : "muted"}>{status.configured ? "Configured" : "Not configured"}</Badge>
      <div className="scanStats">
        <span><strong>{status.matched_count}</strong> matched</span>
        <span><strong>{status.unmatched_file_count}</strong> unmatched files</span>
        <span><strong>{status.unmatched_part_count}</strong> unmatched Plex parts</span>
      </div>
      {status.last_sync && (
        <div className="muted">
          Last sync #{status.last_sync.id}: {status.last_sync.status} · {formatDateTime(status.last_sync.finished_at || status.last_sync.started_at || status.last_sync.created_at)}
        </div>
      )}
      {status.latest_error && <div className="muted">{status.latest_error}</div>}
    </div>
  );
}

function TranscodeSavingsPanel({ stats }: { stats: TranscodeSavingsStats | null }) {
  if (!stats) {
    return <p className="muted">Transcode savings unavailable.</p>;
  }
  const savedTone = stats.total_space_saved_bytes >= 0 ? "good" : "warn";
  return (
    <div className="statusGrid">
      <Badge tone={savedTone}>{formatSignedBytes(stats.total_space_saved_bytes)} saved</Badge>
      <div className="scanStats">
        <span><strong>{stats.runs_started}</strong> runs started</span>
        <span><strong>{stats.runs_succeeded}</strong> runs succeeded</span>
        <span><strong>{stats.items_succeeded}</strong> items transcoded</span>
        <span><strong>{stats.items_published}</strong> published</span>
        <span><strong>{stats.items_validated}</strong> validated</span>
        <span><strong>{stats.items_cleaned}</strong> cleaned</span>
        <span><strong>{stats.items_with_size_comparison}</strong> measured</span>
      </div>
      <div className="scanTiming">
        <span>Total runtime {formatElapsed(stats.total_runtime_seconds * 1000)}</span>
        <span>Before {formatBytes(stats.total_source_size_bytes)}</span>
        <span>After {formatBytes(stats.total_output_size_bytes)}</span>
        <span>Savings {stats.savings_percent.toFixed(1)}%</span>
      </div>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Status({ label, ok }: { label: string; ok: boolean }) {
  return <Badge tone={ok ? "good" : "bad"}>{label}: {ok ? "Available" : "Missing"}</Badge>;
}

function Badge({ tone = "muted", children }: { tone?: string; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

function StatusBadge({ status }: { status: string }) {
  const tone = ["succeeded", "succeeded_with_warnings", "succeeded_with_warning", "verified", "ok", "published", "cleaned"].includes(status)
    ? "good"
    : ["failed", "error", "verification_failed"].includes(status)
      ? "bad"
      : ["running", "queued"].includes(status)
        ? "info"
        : ["interrupted", "degraded"].includes(status)
          ? "warn"
          : "muted";
  return <Badge tone={tone}>{formatStatusLabel(status)}</Badge>;
}

function Progress({ value }: { value: number }) {
  return <div className="progress"><span style={{ width: `${Math.max(0, Math.min(100, value || 0))}%` }} /></div>;
}

function plexDisplayTitle(file: MediaFile) {
  if (!file.plex) return "Unmatched";
  if (file.plex.show_title) {
    const season = file.plex.season_number ?? "?";
    const episode = file.plex.episode_number ?? "?";
    return `${file.plex.show_title} S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")} · ${file.plex.title || file.filename}`;
  }
  return file.plex.title || file.filename;
}

function formatList(values?: string[]) {
  return values?.length ? values.join(", ") : "None";
}

function plexSyncPercent(job: PlexSyncJob) {
  if (job.total_items > 0) {
    return Math.min(100, Math.round((job.processed_items / job.total_items) * 100));
  }
  return ["succeeded", "failed", "canceled"].includes(job.status) ? 100 : 0;
}

function ReportTable({ rows }: { rows: Array<{ label: string; file_count: number; total_size_bytes: number }> }) {
  return (
    <table>
      <thead><tr><th>Label</th><th>Files</th><th>Storage</th></tr></thead>
      <tbody>{rows.map((row) => <tr key={row.label}><td>{row.label}</td><td>{row.file_count}</td><td>{formatBytes(row.total_size_bytes)}</td></tr>)}</tbody>
    </table>
  );
}

function CompactList({ rows, primary, secondary }: { rows: any[]; primary: string; secondary: string }) {
  if (!rows.length) return <p className="muted">No entries yet.</p>;
  return (
    <div className="compactList">
      {rows.map((row) => (
        <div key={row.id}>
          <strong>{row[primary] || `#${row.id}`}</strong>
          <StatusBadge status={row[secondary]} />
        </div>
      ))}
    </div>
  );
}

function Pager({ page, total, pageSize, onPage }: { page: number; total: number; pageSize: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="pager">
      <button disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <span>Page {page} of {pages} ({total} files)</span>
      <button disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </div>
  );
}

function toneFor(category?: string | null) {
  if (category === "Easy Win") return "good";
  if (category === "Review") return "warn";
  if (category === "Error" || category === "Missing") return "bad";
  if (category === "Remux Only") return "info";
  return "muted";
}

function formatBytes(value: number) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let next = Math.abs(value);
  let index = 0;
  while (next >= 1024 && index < units.length - 1) {
    next /= 1024;
    index += 1;
  }
  const prefix = value < 0 ? "-" : "";
  return `${prefix}${next.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatSignedBytes(value: number) {
  if (!value) return "0 B";
  return `${value > 0 ? "+" : ""}${formatBytes(value)}`;
}

function formatDuration(value: number) {
  if (!value) return "0h";
  const hours = value / 3600;
  if (hours > 24) return `${(hours / 24).toFixed(1)}d`;
  return `${hours.toFixed(1)}h`;
}

function formatDateTime(value?: string | null) {
  if (!value) return "Not started";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}

function eligibilityAgeDays(value: string) {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return 0;
  return Math.max(0, Math.floor((Date.now() - timestamp) / 86_400_000));
}

function formatNullableDateTime(value?: string | null) {
  return value ? formatDateTime(value) : "Not archived";
}

function formatStopDateTime(value?: string | null, status?: string) {
  if (value) return formatDateTime(value);
  if (status === "running") return "Running";
  if (status === "queued") return "Not started";
  return "Not stopped";
}

function formatRunDuration(run: TranscodeRun) {
  return formatDurationBetween(run.started_at, run.finished_at, run.status === "running");
}

function formatItemDuration(item: TranscodeRunItem) {
  return formatDurationBetween(item.started_at, item.finished_at, item.status === "running");
}

function formatPublishDuration(item: TranscodeRunItem) {
  return formatDurationBetween(
    item.publish_started_at,
    item.publish_finished_at,
    item.publish_status === "running" || item.publish_status === "queued"
  );
}

function formatDurationBetween(start?: string | null, end?: string | null, live = false) {
  if (!start) return "Not started";
  const startMs = new Date(start).getTime();
  if (Number.isNaN(startMs)) return "Unknown";
  const endMs = end ? new Date(end).getTime() : live ? Date.now() : undefined;
  if (!endMs || Number.isNaN(endMs)) return "Not stopped";
  return formatElapsed(Math.max(0, endMs - startMs));
}

function formatElapsed(milliseconds: number) {
  const seconds = Math.floor(milliseconds / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function formatStatusLabel(value: string) {
  return value.replace(/_/g, " ");
}
