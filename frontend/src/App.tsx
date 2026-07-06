import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, apiText, exportUrl } from "./api";
import type {
  AdminStatus,
  AuthStatus,
  Health,
  MediaFile,
  MediaRoot,
  PlexLibrary,
  PlexPathMapping,
  PlexSettings,
  PlexStatus,
  PlexSyncJob,
  ScanJob,
  Summary,
  TranscodePlan,
  TranscodePlanItem,
  TranscodeProfile,
  TranscodeRun,
  TranscodeRunItem
} from "./types";

type Page =
  | "dashboard"
  | "directories"
  | "scans"
  | "library"
  | "candidates"
  | "reports"
  | "planner"
  | "runs"
  | "status"
  | "settings";

const nav: Array<[Page, string]> = [
  ["dashboard", "Dashboard"],
  ["directories", "Directories"],
  ["scans", "Scans"],
  ["library", "Library"],
  ["candidates", "Candidates"],
  ["reports", "Reports"],
  ["planner", "Transcode Planner"],
  ["runs", "Transcode Runs"],
  ["status", "Admin Status"],
  ["settings", "Settings"]
];

const FIRST_RUN_SETUP_KEY = "media-atlas:first-run-setup-dismissed";
const TRANSCODE_PROFILES_URL = "https://github.com/jonhowe/Media-Atlas/blob/main/docs/TRANSCODE_PROFILES.md";

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");
  const [toast, setToast] = useState<string>("");
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [showFirstRunSetup, setShowFirstRunSetup] = useState(false);

  useEffect(() => {
    refreshAuth();
  }, []);

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
      setAuth(await api<AuthStatus>("/api/auth/me"));
    } catch {
      setAuth({ mode: "disabled", authenticated: true, configured: true, username: "local" });
    }
  }

  async function logout() {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
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

  if (!auth) {
    return <Splash message="Checking access" />;
  }

  if (auth.mode !== "disabled" && !auth.authenticated) {
    return <LoginScreen auth={auth} onAuthenticated={refreshAuth} />;
  }

  return (
    <div className="app">
      <aside className="sidebar">
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
              onClick={() => setPage(key)}
            >
              {label}
            </button>
          ))}
        </nav>
      </aside>
      <main>
        <header className="topbar">
          <div>
            <h1>{showFirstRunSetup ? "First-run setup" : nav.find(([key]) => key === page)?.[1]}</h1>
            <p>{showFirstRunSetup ? "Configure optional Plex enrichment before your first sync." : "Scan, understand, plan, and safely run staged media conversions."}</p>
          </div>
          <div className="topbarActions">
            {toast && <div className="toast">{toast}</div>}
            {auth.mode !== "disabled" && <button onClick={logout}>Log out</button>}
          </div>
        </header>
        {showFirstRunSetup ? (
          <FirstRunSetup onToast={setToast} onDone={dismissFirstRunSetup} />
        ) : (
          <>
            {page === "dashboard" && <Dashboard onToast={setToast} />}
            {page === "directories" && <Directories onToast={setToast} />}
            {page === "scans" && <Scans onToast={setToast} />}
            {page === "library" && <Library onToast={setToast} />}
            {page === "candidates" && <Candidates onToast={setToast} />}
            {page === "reports" && <Reports />}
            {page === "planner" && <Planner onToast={setToast} switchToRuns={() => setPage("runs")} />}
            {page === "runs" && <Runs onToast={setToast} />}
            {page === "status" && <AdminStatusPage onToast={setToast} />}
            {page === "settings" && <Settings onToast={setToast} />}
          </>
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

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, []);

  async function refresh() {
    try {
      const [nextHealth, nextSummary, nextRuns, nextScans] = await Promise.all([
        api<Health>("/api/health"),
        api<Summary>("/api/reports/summary"),
        api<TranscodeRun[]>("/api/transcode-runs?limit=5"),
        api<ScanJob[]>("/api/scans?limit=5")
      ]);
      setHealth(nextHealth);
      setSummary(nextSummary);
      setRuns(nextRuns);
      setScans(nextScans);
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
        <Panel title="Recent Transcode Runs">
          <CompactList rows={runs} primary="name" secondary="status" />
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
        <table>
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
                <td>{root.name}</td>
                <td className="path">{root.path}</td>
                <td><Badge tone={root.enabled ? "good" : "muted"}>{root.enabled ? "Enabled" : "Disabled"}</Badge></td>
                <td>{root.last_scanned_at || "Never"}</td>
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

function Scans({ onToast }: { onToast: (message: string) => void }) {
  const [scans, setScans] = useState<ScanJob[]>([]);
  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => window.clearInterval(timer);
  }, []);

  async function refresh() {
    setScans(await api<ScanJob[]>("/api/scans?limit=25"));
  }

  async function start() {
    try {
      await api("/api/scans", { method: "POST", body: "{}" });
      await refresh();
      onToast("Scan started.");
    } catch (error) {
      onToast(String(error));
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

  return (
    <section className="stack">
      <div className="toolbar">
        <button className="primary" onClick={start}>Start scan</button>
      </div>
      <Panel title="Scan Jobs">
        <table>
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
                  <td>{scan.id}</td>
                  <td>
                    <StatusBadge status={scan.status} />
                    {scan.files_failed > 0 && <div className="muted">{scan.files_failed} failed</div>}
                  </td>
                  <td>
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
                    <div className="scanStats">
                      <span><strong>{scan.total_files_discovered}</strong> discovered</span>
                      <span><strong>{progress.completed}</strong> processed</span>
                      <span><strong>{scan.files_probed}</strong> probed</span>
                      <span><strong>{scan.files_skipped}</strong> skipped</span>
                    </div>
                  </td>
                  <td>
                    <div className="scanTiming">
                      <span>Created {formatDateTime(scan.created_at)}</span>
                      <span>Started {formatDateTime(scan.started_at)}</span>
                      <span>{timing.label} {timing.value}</span>
                    </div>
                  </td>
                  <td>{scan.message}</td>
                  <td className="path">{scan.current_path}</td>
                  <td className="rowActions">
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

function Candidates({ onToast }: { onToast: (message: string) => void }) {
  const [category, setCategory] = useState("Easy Win");
  return (
    <section className="stack">
      <div className="tabs">
        {["Easy Win", "Remux Only", "Review", "Already Modern", "Error"].map((item) => (
          <button key={item} className={category === item ? "active" : ""} onClick={() => setCategory(item)}>{item}</button>
        ))}
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

function Planner({ onToast, switchToRuns }: { onToast: (message: string) => void; switchToRuns: () => void }) {
  const [profiles, setProfiles] = useState<TranscodeProfile[]>([]);
  const [plans, setPlans] = useState<TranscodePlan[]>([]);
  const [files, setFiles] = useState<MediaFile[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [profileId, setProfileId] = useState<number>(0);
  const [name, setName] = useState("MVP transcode plan");
  const [showArchived, setShowArchived] = useState(false);

  useEffect(() => {
    refresh();
  }, [showArchived]);

  async function refresh() {
    const [nextProfiles, nextPlans, candidates] = await Promise.all([
      api<TranscodeProfile[]>("/api/transcode-profiles"),
      api<TranscodePlan[]>(`/api/transcode-plans${showArchived ? "?include_archived=true" : ""}`),
      api<{ items: MediaFile[] }>("/api/media?recommendation_category=Easy%20Win&page_size=100&sort=size_bytes&direction=desc")
    ]);
    setProfiles(nextProfiles);
    setPlans(nextPlans);
    setFiles(candidates.items);
    setProfileId((current) => current || defaultProfileId(nextProfiles));
  }

  function toggleFile(id: number) {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  }

  async function create() {
    try {
      const plan = await api<TranscodePlan>("/api/transcode-plans", {
        method: "POST",
        body: JSON.stringify({ name, profile_id: profileId, file_ids: Array.from(selected) })
      });
      setSelected(new Set());
      await refresh();
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
      await refresh();
      onToast("Transcode plan archived.");
    } catch (error) {
      onToast(String(error));
    }
  }

  async function unarchivePlan(plan: TranscodePlan) {
    try {
      await api(`/api/transcode-plans/${plan.id}/unarchive`, { method: "POST", body: "{}" });
      await refresh();
      onToast("Transcode plan restored.");
    } catch (error) {
      onToast(String(error));
    }
  }

  async function deletePlan(plan: TranscodePlan) {
    if (!window.confirm(`Delete "${plan.name}"? This removes the plan and its planned items. Source media and staged outputs are untouched.`)) return;
    try {
      await api(`/api/transcode-plans/${plan.id}`, { method: "DELETE" });
      await refresh();
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
        <table>
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
                <td>
                  <strong>{plan.name}</strong>
                  <div className="muted">
                    {plan.profile_name || "Unknown profile"} · {plan.status}
                    {plan.archived_at ? ` · archived ${formatDateTime(plan.archived_at)}` : ""}
                  </div>
                </td>
                <td>{formatDateTime(plan.created_at)}</td>
                <td>
                  <div className="planFiles">
                    <strong>{plan.item_count || 0} files</strong>
                    {(plan.sample_items || []).map((item) => (
                      <span key={item.id} className="path">{planItemName(item)}</span>
                    ))}
                    {planRemainingCount(plan) > 0 && <span className="muted">+ {planRemainingCount(plan)} more</span>}
                  </div>
                </td>
                <td>
                  <PlanRunSummary plan={plan} />
                </td>
                <td className="rowActions">
                  <a className="button" href={`/api/transcode-plans/${plan.id}/download.csv`}>CSV</a>
                  <a className="button" href={`/api/transcode-plans/${plan.id}/download.sh`}>Script</a>
                  {plan.archived_at ? (
                    <button onClick={() => unarchivePlan(plan)}>Unarchive</button>
                  ) : (
                    <>
                      <button onClick={() => startRun(plan)}>Start run</button>
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
      <Panel title="Create Plan From Easy Wins">
        <div className="panelIntro">
          <p className="muted">Choose candidate files and a staged-output profile.</p>
          <a className="button" href={TRANSCODE_PROFILES_URL} target="_blank" rel="noreferrer">Profile guide</a>
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
        <table>
          <thead>
            <tr>
              <th></th>
              <th>File</th>
              <th>Size</th>
              <th>Video</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {files.map((file) => (
              <tr key={file.id}>
                <td><input type="checkbox" checked={selected.has(file.id)} onChange={() => toggleFile(file.id)} /></td>
                <td>{file.filename}</td>
                <td>{formatBytes(file.size_bytes)}</td>
                <td>{file.resolution_bucket} {file.primary_video_codec}</td>
                <td>{file.recommendation_summary}</td>
              </tr>
            ))}
          </tbody>
        </table>
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

function defaultProfileId(profiles: TranscodeProfile[]) {
  return profiles.find((profile) => profile.command_template === "hevc_archive_fast")?.id
    || profiles.find((profile) => profile.command_template !== "manual_review")?.id
    || 0;
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
    && !item.published_at;
}

function Runs({ onToast }: { onToast: (message: string) => void }) {
  const [runs, setRuns] = useState<TranscodeRun[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selected, setSelected] = useState<TranscodeRun | null>(null);
  const [log, setLog] = useState("");

  useEffect(() => {
    refreshRuns();
    const timer = window.setInterval(refreshRuns, 2000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    refreshRun(selectedId);
    const timer = window.setInterval(() => refreshRun(selectedId), 2000);
    return () => window.clearInterval(timer);
  }, [selectedId]);

  async function refreshRuns() {
    setRuns(await api<TranscodeRun[]>("/api/transcode-runs"));
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

  async function showLog(runId: number, itemId: number) {
    setLog(await apiText(`/api/transcode-runs/${runId}/items/${itemId}/log`));
  }

  async function publishItem(runId: number, item: TranscodeRunItem) {
    const firstConfirmed = window.confirm(
      `Publish this staged output to the original location?\n\nOriginal live file:\n${item.source_path}\n\nStaged output:\n${item.target_path}\n\nMedia Atlas will move the original file into transcode backup storage, then copy the staged output into the original path.`
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
      await api<TranscodeRunItem>(`/api/transcode-runs/${runId}/items/${item.id}/publish`, {
        method: "POST",
        body: JSON.stringify({
          source_path: item.source_path,
          target_path: item.target_path,
          confirmation_text: phrase
        })
      });
      await refreshRun(runId);
      onToast("Published staged output. Original file was backed up.");
    } catch (error) {
      onToast(String(error));
      await refreshRun(runId);
    }
  }

  return (
    <section className="stack">
      <Panel title="Runs">
        <table>
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
                <td>
                  <strong>{run.name}</strong>
                  <div className="muted">Created {formatDateTime(run.created_at)}</div>
                </td>
                <td><StatusBadge status={run.status} /></td>
                <td>{formatDateTime(run.started_at)}</td>
                <td>{formatStopDateTime(run.finished_at, run.status)}</td>
                <td>{formatRunDuration(run)}</td>
                <td>
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
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      {selected && (
        <Panel title={`Run ${selected.id}: ${selected.name}`}>
          <p>{selected.message}</p>
          <div className="metrics compact">
            <Metric label="Created" value={formatDateTime(selected.created_at)} />
            <Metric label="Started" value={formatDateTime(selected.started_at)} />
            <Metric label="Stopped" value={formatStopDateTime(selected.finished_at, selected.status)} />
            <Metric label="Duration" value={formatRunDuration(selected)} />
          </div>
          <table>
            <thead>
              <tr>
                <th>Item</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Timing</th>
                <th>Target</th>
                <th>Verification</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {selected.items?.map((item) => (
                <tr key={item.id}>
                  <td>{item.id}</td>
                  <td><StatusBadge status={item.status} /></td>
                  <td>
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
                    <div className="scanTiming">
                      <span>Started {formatDateTime(item.started_at)}</span>
                      <span>Stopped {formatStopDateTime(item.finished_at, item.status)}</span>
                      <span>Duration {formatItemDuration(item)}</span>
                    </div>
                  </td>
                  <td className="path">
                    <strong>Staged</strong>
                    <span>{item.target_path}</span>
                    <strong>Original</strong>
                    <span>{item.source_path}</span>
                  </td>
                  <td>
                    <div className="statusGrid">
                      <span>{item.verification_status} {item.verification_message}</span>
                      {item.publish_status && (
                        <span>
                          <StatusBadge status={item.publish_status} /> {item.publish_message}
                        </span>
                      )}
                      {item.published_at && <span className="muted">Published {formatDateTime(item.published_at)}</span>}
                      {item.published_backup_path && <span className="muted">Backup {item.published_backup_path}</span>}
                    </div>
                  </td>
                  <td className="rowActions">
                    <button onClick={() => showLog(selected.id, item.id)}>Log</button>
                    {canPublishItem(item) && <button className="danger" onClick={() => publishItem(selected.id, item)}>Publish</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {log && <pre className="log">{log}</pre>}
        </Panel>
      )}
    </section>
  );
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
            <button onClick={runRetention}>Run retention cleanup</button>
          </div>
          <pre className="json">{JSON.stringify({ auth: status.auth, retention: status.retention, migrations: readiness.migrations }, null, 2)}</pre>
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
      <Panel title="Runtime Settings">
        <pre className="json">{JSON.stringify(settings, null, 2)}</pre>
      </Panel>
    </section>
  );
}

function MediaTable({ files, onOpen }: { files: MediaFile[]; onOpen: (id: number) => void }) {
  return (
    <Panel title="Media Files">
      <table>
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
              <td>
                <strong>{file.filename}</strong>
                <div className="path">{file.path}</div>
              </td>
              <td>{file.root_name}</td>
              <td>{formatBytes(file.size_bytes)}</td>
              <td>
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
              <td>{file.resolution_bucket || "Unknown"} {file.primary_video_codec || ""} {file.is_hdr && <Badge tone="warn">HDR</Badge>}</td>
              <td>{file.primary_audio_codec || "Unknown"} {file.audio_stream_count > 1 && `+${file.audio_stream_count - 1}`}</td>
              <td>{file.bitrate_mbps ? `${file.bitrate_mbps} Mbps` : "Unknown"}</td>
              <td>
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
  const tone = ["succeeded", "verified", "ok", "published"].includes(status)
    ? "good"
    : ["failed", "error", "verification_failed"].includes(status)
      ? "bad"
      : ["running", "queued"].includes(status)
        ? "info"
        : ["interrupted", "degraded"].includes(status)
          ? "warn"
          : "muted";
  return <Badge tone={tone}>{status}</Badge>;
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
  let next = value;
  let index = 0;
  while (next >= 1024 && index < units.length - 1) {
    next /= 1024;
    index += 1;
  }
  return `${next.toFixed(index ? 1 : 0)} ${units[index]}`;
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
