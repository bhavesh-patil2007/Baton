import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getToken } from "@/app/api";
import { useAuth } from "@/app/auth";
import { Button, EmptyState, ErrorState, Loading } from "@/shared/ui";
import { GitBranch, RefreshCw, Trash2, Plus, CheckCircle2, AlertCircle, Layers } from "lucide-react";

interface Source {
  source_id: string;
  title: string;
  type: string;
  acl_label: string;
  consent: boolean;
  status: string;
  chunks: number;
  note?: string | null;
  created_at?: string | null;
}

interface SourceConnection {
  id: string;
  project_id: string;
  provider: "github" | "jira";
  target_name: string;
  acl_label: string;
  status: "connected" | "syncing" | "succeeded" | "partial" | "failed";
  last_synced_at?: string | null;
  records_added: number;
  records_updated: number;
  records_skipped: number;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

const ACCEPTED = ".md,.txt,.pdf,.docx,.json,.csv";

async function uploadSource(file: File, consent: boolean, aclLabel: string): Promise<Source> {
  const form = new FormData();
  form.set("file", file);
  form.set("consent", String(consent));
  form.set("acl_label", aclLabel);
  const base = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
  const token = getToken();
  const response = await fetch(`${base}/sources`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Keep the HTTP status text when the API does not return JSON.
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as Source;
}

export function SourcesPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [aclLabel, setAclLabel] = useState("L1");
  const [consent, setConsent] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  // Live Sync Connect Modal states
  const [isConnectOpen, setIsConnectOpen] = useState(false);
  const [connectProvider, setConnectProvider] = useState<"github" | "jira">("github");
  const [connectTarget, setConnectTarget] = useState("");
  const [connectAcl, setConnectAcl] = useState("L1");
  const [connectConsent, setConnectConsent] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [activeSyncId, setActiveSyncId] = useState<string | null>(null);

  const { data, error, isLoading, refetch } = useQuery({
    queryKey: ["sources", user?.id],
    queryFn: () => api<Source[]>("/sources"),
  });

  const connectionsQuery = useQuery({
    queryKey: ["source_connections", user?.id],
    queryFn: () => api<SourceConnection[]>("/sources/connections"),
  });

  const syncMutation = useMutation({
    mutationFn: (connectionId: string) =>
      api<SourceConnection>(`/sources/connections/${connectionId}/sync`, { method: "POST" }),
    onMutate: (id) => setActiveSyncId(id),
    onSettled: () => {
      setActiveSyncId(null);
      void queryClient.invalidateQueries({ queryKey: ["source_connections"] });
      void queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (connectionId: string) =>
      api<{ status: string }>(`/sources/connections/${connectionId}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["source_connections"] });
      void queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });

  const connectMutation = useMutation({
    mutationFn: async (payload: { provider: "github" | "jira"; target_name: string; acl_label: string; consent: boolean }) => {
      const conn = await api<SourceConnection>("/sources/connections", { method: "POST", body: payload });
      await api<SourceConnection>(`/sources/connections/${conn.id}/sync`, { method: "POST" });
      return conn;
    },
    onSuccess: () => {
      setIsConnectOpen(false);
      setConnectTarget("");
      setConnectConsent(false);
      setConnectError(null);
      void queryClient.invalidateQueries({ queryKey: ["source_connections"] });
      void queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
    onError: (err) => {
      setConnectError(err instanceof Error ? err.message : "Failed to connect source.");
    },
  });

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    if (!file || !consent) return;
    setUploading(true);
    setUploadError(null);
    try {
      await uploadSource(file, consent, aclLabel);
      setFile(null);
      setConsent(false);
      formElement.reset();
      await queryClient.invalidateQueries({ queryKey: ["sources"] });
    } catch (cause) {
      setUploadError(cause instanceof Error ? cause.message : "Source upload failed.");
    } finally {
      setUploading(false);
    }
  }

  function handleConnectSubmit(e: FormEvent) {
    e.preventDefault();
    if (!connectTarget.trim() || !connectConsent) return;
    connectMutation.mutate({
      provider: connectProvider,
      target_name: connectTarget.trim(),
      acl_label: connectAcl,
      consent: connectConsent,
    });
  }

  const isApprover = user?.role === "approver";
  const connections = connectionsQuery.data ?? [];

  return (
    <section className="space-y-8 max-w-7xl mx-auto w-full pb-12 animate-in fade-in duration-300">
      {/* Header with Back Navigation and High-Contrast Typography */}
      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={() => navigate(-1)}
          className="p-2.5 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 hover:text-slate-900 shadow-sm transition-all dark:border-white/10 dark:bg-white/5 dark:text-gray-200 dark:hover:bg-white/10 cursor-pointer"
          aria-label="Go back"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
        </button>
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white tracking-tight">
            Connect Sources
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-gray-300">
            Import authorised project records. Secrets and contact details are redacted before evidence is stored.
          </p>
        </div>
      </div>

      {/* =========================================================================
          COMMITMENT 1: LIVE PROJECT SYNC (GITHUB & JIRA)
         ========================================================================= */}
      <div className="rounded-2xl border border-slate-200 dark:border-white/10 bg-white dark:bg-black/40 backdrop-blur-2xl p-6 shadow-sm dark:shadow-2xl space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-slate-100 dark:border-white/10 pb-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="p-1.5 rounded-lg bg-indigo-50 dark:bg-indigo-950/50 text-indigo-600 dark:text-indigo-400">
                <GitBranch className="w-4 h-4" />
              </span>
              <h2 className="text-lg font-bold text-slate-900 dark:text-white tracking-tight">
                Live Source Syncing (GitHub & Jira)
              </h2>
            </div>
            <p className="text-xs text-slate-500 dark:text-gray-400 mt-1">
              Read-only connectors with repeatable incremental checkpoints. No browser secrets.
            </p>
          </div>
          {isApprover && (
            <Button
              onClick={() => setIsConnectOpen(true)}
              className="inline-flex items-center gap-2 px-4 py-2 text-xs font-bold bg-indigo-600 hover:bg-indigo-700 text-white"
            >
              <Plus className="w-3.5 h-3.5" /> Connect Live Source
            </Button>
          )}
        </div>

        {/* Connections List / Grid */}
        {connectionsQuery.isLoading ? (
          <Loading label="Loading connections" />
        ) : connections.length === 0 ? (
          <div className="p-6 text-center border border-dashed border-slate-200 dark:border-white/10 rounded-xl bg-slate-50/50 dark:bg-white/[0.02]">
            <p className="text-xs text-slate-500 dark:text-gray-400 font-medium">
              No live repository or project connections active. Connect GitHub or Jira to enable incremental syncing.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
            {connections.map((conn) => {
              const isSyncing = activeSyncId === conn.id || conn.status === "syncing";
              return (
                <div
                  key={conn.id}
                  className="rounded-xl border border-slate-200 dark:border-white/10 bg-slate-50/50 dark:bg-white/[0.03] p-4 flex flex-col justify-between gap-3 transition-all hover:shadow-md"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] font-mono font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-slate-200 dark:bg-white/10 text-slate-700 dark:text-gray-200">
                          {conn.provider}
                        </span>
                        <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-purple-100 dark:bg-purple-950/60 text-purple-700 dark:text-purple-300">
                          {conn.acl_label}
                        </span>
                        <span
                          className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
                            conn.status === "succeeded"
                              ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300"
                              : conn.status === "failed"
                              ? "bg-rose-100 text-rose-800 dark:bg-rose-950/60 dark:text-rose-300"
                              : "bg-blue-100 text-blue-800 dark:bg-blue-950/60 dark:text-blue-300"
                          }`}
                        >
                          {conn.status}
                        </span>
                      </div>
                      <h3 className="font-bold text-sm text-slate-900 dark:text-white font-mono">{conn.target_name}</h3>
                    </div>

                    {isApprover && (
                      <button
                        type="button"
                        onClick={() => deleteMutation.mutate(conn.id)}
                        disabled={deleteMutation.isPending}
                        className="text-slate-400 hover:text-rose-500 transition p-1 cursor-pointer"
                        title="Disconnect source"
                        aria-label="Disconnect source"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                  </div>

                  <div className="text-xs text-slate-500 dark:text-gray-400 space-y-1 border-t border-slate-200/60 dark:border-white/5 pt-2">
                    <div className="flex justify-between">
                      <span>Last sync:</span>
                      <span className="font-mono text-slate-700 dark:text-gray-300">
                        {conn.last_synced_at ? new Date(conn.last_synced_at).toLocaleString() : "Never"}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span>Records synced:</span>
                      <span className="font-mono text-slate-700 dark:text-gray-300">
                        {conn.records_added} added, {conn.records_skipped} skipped
                      </span>
                    </div>
                    {conn.error_message && (
                      <p className="text-[11px] text-rose-500 font-medium pt-1">
                        Error: {conn.error_message}
                      </p>
                    )}
                  </div>

                  {isApprover && (
                    <div className="pt-1">
                      <Button
                        variant="quiet"
                        onClick={() => syncMutation.mutate(conn.id)}
                        disabled={isSyncing}
                        className="w-full flex items-center justify-center gap-2 py-1.5 text-xs font-semibold"
                      >
                        <RefreshCw className={`w-3.5 h-3.5 ${isSyncing ? "animate-spin text-purple-600" : ""}`} />
                        {isSyncing ? "Syncing incremental records…" : "Sync Now"}
                      </Button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Connect Source Modal */}
      {isConnectOpen && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/60 backdrop-blur-sm animate-in fade-in"
        >
          <div className="w-full max-w-md rounded-2xl border border-slate-200 dark:border-white/10 bg-white dark:bg-[#121424] p-6 shadow-2xl space-y-4">
            <div className="flex justify-between items-center border-b border-slate-100 dark:border-white/10 pb-3">
              <h3 className="font-bold text-base text-slate-900 dark:text-white">Connect Project Source</h3>
              <button
                type="button"
                onClick={() => setIsConnectOpen(false)}
                className="text-slate-400 hover:text-slate-600 dark:hover:text-gray-200 text-sm font-bold"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleConnectSubmit} className="space-y-4 text-xs">
              <div>
                <label className="block font-bold uppercase tracking-wider text-slate-700 dark:text-gray-300 mb-1.5">
                  Provider
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setConnectProvider("github")}
                    className={`py-2 px-3 rounded-xl border text-center font-bold transition ${
                      connectProvider === "github"
                        ? "border-indigo-600 bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300 dark:border-indigo-500"
                        : "border-slate-200 dark:border-white/10 text-slate-700 dark:text-gray-300"
                    }`}
                  >
                    GitHub
                  </button>
                  <button
                    type="button"
                    onClick={() => setConnectProvider("jira")}
                    className={`py-2 px-3 rounded-xl border text-center font-bold transition ${
                      connectProvider === "jira"
                        ? "border-indigo-600 bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300 dark:border-indigo-500"
                        : "border-slate-200 dark:border-white/10 text-slate-700 dark:text-gray-300"
                    }`}
                  >
                    Jira
                  </button>
                </div>
              </div>

              <div>
                <label htmlFor="connect-target" className="block font-bold uppercase tracking-wider text-slate-700 dark:text-gray-300 mb-1.5">
                  {connectProvider === "github" ? "GitHub Repository (owner/repo)" : "Jira Project Key"}
                </label>
                <input
                  id="connect-target"
                  type="text"
                  required
                  placeholder={connectProvider === "github" ? "manjirirelekar47/Baton" : "CAMPUS"}
                  value={connectTarget}
                  onChange={(e) => setConnectTarget(e.target.value)}
                  className="w-full rounded-xl border border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-black/50 p-2.5 text-xs text-slate-900 dark:text-white font-mono focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label htmlFor="connect-acl" className="block font-bold uppercase tracking-wider text-slate-700 dark:text-gray-300 mb-1.5">
                  Access Classification
                </label>
                <select
                  id="connect-acl"
                  value={connectAcl}
                  onChange={(e) => setConnectAcl(e.target.value)}
                  className="w-full rounded-xl border border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-black/50 p-2.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500"
                >
                  <option value="L1">L1 — Team-wide (Public to project)</option>
                  <option value="L2">L2 — Maintainers Only</option>
                  <option value="L3">L3 — Leadership Only</option>
                </select>
              </div>

              <label className="flex items-start gap-2 pt-1 text-slate-600 dark:text-gray-300 cursor-pointer">
                <input
                  type="checkbox"
                  required
                  checked={connectConsent}
                  onChange={(e) => setConnectConsent(e.target.checked)}
                  className="mt-0.5 rounded text-indigo-600"
                />
                <span>I confirm that I am authorised to connect and sync this GitHub repository or Jira project.</span>
              </label>

              <p className="text-[11px] leading-relaxed text-slate-500 dark:text-gray-400">
                Provider credentials stay on the API server. GitHub private repositories require GITHUB_SYNC_TOKEN.
                Jira Cloud requires JIRA_URL, JIRA_USER_EMAIL and JIRA_API_TOKEN configured by the deployment owner.
                Failed authentication is shown as a failed sync; Baton does not substitute sample records.
              </p>

              {connectError && (
                <p role="alert" className="text-rose-500 font-semibold">{connectError}</p>
              )}

              <div className="flex justify-end gap-2 pt-2 border-t border-slate-100 dark:border-white/10">
                <Button variant="quiet" type="button" onClick={() => setIsConnectOpen(false)}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={!connectTarget.trim() || !connectConsent || connectMutation.isPending}
                  className="bg-indigo-600 hover:bg-indigo-700 text-white font-bold px-4 py-2"
                >
                  {connectMutation.isPending ? "Connecting & Syncing…" : "Connect & Initial Sync"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Manual Upload Section */}
      {isApprover ? (
        <form onSubmit={handleUpload} className="space-y-4 rounded-2xl border border-slate-200 dark:border-white/10 bg-white dark:bg-black/40 backdrop-blur-2xl p-6 shadow-sm dark:shadow-2xl">
          <div className="flex items-center gap-2 border-b border-slate-100 dark:border-white/10 pb-3">
            <span className="p-1.5 rounded-lg bg-purple-50 dark:bg-purple-950/50 text-purple-600 dark:text-purple-400">
              <Layers className="w-4 h-4" />
            </span>
            <h2 className="text-base font-bold text-slate-900 dark:text-white">Direct Document Upload</h2>
          </div>
          <div>
            <label htmlFor="source-file" className="block text-xs font-bold uppercase tracking-wider text-slate-700 dark:text-gray-300">
              Source file
            </label>
            <input
              id="source-file"
              type="file"
              accept={ACCEPTED}
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="mt-2 block w-full text-xs text-slate-500 dark:text-gray-400 file:mr-4 file:rounded-xl file:border-0 file:bg-purple-50 dark:file:bg-purple-950/50 file:px-4 file:py-2 file:text-xs file:font-bold file:text-purple-700 dark:file:text-purple-300 hover:file:bg-purple-100 dark:hover:file:bg-purple-900/60 transition cursor-pointer"
            />
            <p className="mt-1.5 text-xs text-slate-500 dark:text-gray-500">MD, TXT, PDF, DOCX, Git JSON or task CSV. Maximum 10 MB.</p>
          </div>
          <div>
            <label htmlFor="source-access" className="block text-xs font-bold uppercase tracking-wider text-slate-700 dark:text-gray-300">
              Who may see this source?
            </label>
            <select
              id="source-access"
              value={aclLabel}
              onChange={(event) => setAclLabel(event.target.value)}
              className="mt-1.5 rounded-xl border border-slate-200 dark:border-white/15 bg-white dark:bg-black/60 px-3.5 py-2 text-xs font-medium text-slate-900 dark:text-white focus:outline-none focus:border-purple-500"
            >
              <option value="L1">L1 — Team-wide</option>
              <option value="L2">L2 — Maintainers</option>
              <option value="L3">L3 — Leadership-only</option>
            </select>
            <p className="mt-1 text-xs text-slate-500 dark:text-gray-500">L4 secrets are never accepted as a source.</p>
          </div>
          <label className="flex items-start gap-2.5 text-xs text-slate-700 dark:text-gray-300 cursor-pointer pt-1">
            <input
              type="checkbox"
              checked={consent}
              onChange={(event) => setConsent(event.target.checked)}
              className="mt-0.5 rounded border-slate-300 dark:border-white/20 bg-white dark:bg-black/40 text-purple-600 focus:ring-purple-500"
            />
            <span>I confirm that I am authorised to connect and process this source for this project.</span>
          </label>
          {uploadError && <p role="alert" className="text-xs font-semibold text-rose-500 dark:text-rose-400">{uploadError}</p>}
          <Button type="submit" disabled={!file || !consent || uploading} className="px-5 py-2.5 font-bold">
            {uploading ? "Uploading…" : "Upload Source"}
          </Button>
        </form>
      ) : (
        <div className="rounded-2xl border border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-black/40 backdrop-blur-2xl p-5 text-xs text-slate-600 dark:text-gray-400 shadow-sm">
          Only the project approver can connect new sources. You can inspect the sources available to your role below.
        </div>
      )}

      {/* Connected Sources Table */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-gray-900 dark:text-white tracking-tight">Connected Sources Inventory</h2>
          <Button variant="quiet" onClick={() => void refetch()}>
            Refresh
          </Button>
        </div>
        {isLoading && <Loading label="Loading sources" />}
        {error && <ErrorState title="Could not load sources" detail={(error as Error).message} onRetry={() => void refetch()} />}
        {data && data.length === 0 && (
          <EmptyState title="No sources yet">
            Connect a live repository or upload project records to begin.
          </EmptyState>
        )}
        {data && data.length > 0 && (
          <div className="overflow-x-auto rounded-2xl border border-slate-200 dark:border-white/10 bg-white dark:bg-black/40 backdrop-blur-2xl shadow-sm dark:shadow-2xl">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 dark:bg-white/[0.02] border-b border-slate-200 dark:border-white/10 text-slate-500 dark:text-gray-400 font-bold uppercase tracking-wider text-[10px]">
                <tr>
                  <th className="px-4 py-3">Source</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Access</th>
                  <th className="px-4 py-3">Chunks</th>
                  <th className="px-4 py-3">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-white/5">
                {data.map((source) => (
                  <tr key={source.source_id} className="hover:bg-slate-50 dark:hover:bg-white/[0.03] transition">
                    <td className="px-4 py-3 font-semibold text-slate-900 dark:text-white">{source.title}</td>
                    <td className="px-4 py-3 text-slate-600 dark:text-gray-300 font-mono">{source.type}</td>
                    <td className="px-4 py-3">
                      <span className="px-2.5 py-0.5 rounded-md bg-purple-50 dark:bg-purple-950/40 text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-500/20 font-bold text-[10px] font-mono">
                        {source.acl_label}
                      </span>
                    </td>
                    <td className="px-4 py-3 tabular-nums text-slate-600 dark:text-gray-300 font-mono">{source.chunks}</td>
                    <td className="px-4 py-3">
                      <span className="px-2.5 py-0.5 rounded-full bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-500/30 text-emerald-800 dark:text-emerald-300 font-semibold text-[10px]">
                        {source.status}
                        {source.note ? `: ${source.note}` : ""}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

