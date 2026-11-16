import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getToken } from "@/app/api";
import { useAuth } from "@/app/auth";
import { Button, EmptyState, ErrorState, Loading } from "@/shared/ui";

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

interface DriftAlert {
  alert_id: string;
  title: string;
  source_type: string;
  acl_label: string;
  previous_chunks: number;
  current_chunks: number;
  status: "open" | "resolved";
  detected_at: string;
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
  const [captureTitle, setCaptureTitle] = useState("");
  const [captureBody, setCaptureBody] = useState("");
  const [captureAcl, setCaptureAcl] = useState("L1");
  const [captureConsent, setCaptureConsent] = useState(false);
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [captureSaved, setCaptureSaved] = useState(false);
  const [savingCapture, setSavingCapture] = useState(false);
  const [driftActionError, setDriftActionError] = useState<string | null>(null);
  const { data, error, isLoading, refetch } = useQuery({
    queryKey: ["sources", user?.id],
    queryFn: () => api<Source[]>("/sources"),
  });
  const { data: driftAlerts = [], error: driftError, isLoading: driftLoading, refetch: refetchDrift } = useQuery({
    queryKey: ["drift-alerts", user?.id],
    queryFn: async () => {
      if (user?.role === "approver") await api<{ baselined: number; changed: number }>("/drift/scan", { method: "POST" });
      return api<DriftAlert[]>("/drift/alerts");
    },
    enabled: Boolean(user),
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
      await queryClient.invalidateQueries({ queryKey: ["drift-alerts"] });
    } catch (cause) {
      setUploadError(cause instanceof Error ? cause.message : "Source upload failed.");
    } finally {
      setUploading(false);
    }
  }

  async function handleCapture(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSavingCapture(true);
    setCaptureError(null);
    setCaptureSaved(false);
    try {
      await api<Source>("/capture", {
        method: "POST",
        body: { title: captureTitle, body: captureBody, acl_label: captureAcl, consent: captureConsent },
      });
      setCaptureTitle("");
      setCaptureBody("");
      setCaptureConsent(false);
      setCaptureSaved(true);
      await queryClient.invalidateQueries({ queryKey: ["sources"] });
      await queryClient.invalidateQueries({ queryKey: ["drift-alerts"] });
    } catch (cause) {
      setCaptureError(cause instanceof Error ? cause.message : "Could not save this note.");
    } finally {
      setSavingCapture(false);
    }
  }

  async function handleResolveAlert(alertId: string) {
    setDriftActionError(null);
    try {
      await api<void>(`/drift/alerts/${encodeURIComponent(alertId)}/resolve`, { method: "POST", body: {} });
      await refetchDrift();
    } catch (cause) {
      setDriftActionError(cause instanceof Error ? cause.message : "Could not resolve this alert.");
    }
  }

  return (
    <section className="space-y-6 max-w-7xl mx-auto w-full pb-12 animate-in fade-in duration-300">
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

      {user?.role === "approver" ? (
        <form onSubmit={handleUpload} className="space-y-4 rounded-2xl border border-slate-200 dark:border-white/10 bg-white dark:bg-black/40 backdrop-blur-2xl p-6 shadow-sm dark:shadow-2xl">
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

      {user?.role === "approver" && (
        <form onSubmit={handleCapture} className="space-y-4 rounded-2xl border border-purple-200 dark:border-purple-500/20 bg-purple-50/50 dark:bg-purple-950/10 p-6 shadow-sm">
          <div>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white">Capture a decision or handover note</h2>
            <p className="mt-1 text-xs text-slate-600 dark:text-gray-400">Notes are redacted, split into evidence chunks, and included in Baton’s normal source and verification workflow.</p>
          </div>
          <div>
            <label htmlFor="capture-title" className="block text-xs font-bold uppercase tracking-wider text-slate-700 dark:text-gray-300">Title</label>
            <input id="capture-title" value={captureTitle} onChange={(event) => setCaptureTitle(event.target.value)} minLength={3} maxLength={120} required className="mt-1.5 w-full rounded-xl border border-slate-200 dark:border-white/15 bg-white dark:bg-black/40 px-3.5 py-2.5 text-sm text-slate-900 dark:text-white" placeholder="e.g. Retry worker decision" />
          </div>
          <div>
            <label htmlFor="capture-body" className="block text-xs font-bold uppercase tracking-wider text-slate-700 dark:text-gray-300">What should the next team know?</label>
            <textarea id="capture-body" value={captureBody} onChange={(event) => setCaptureBody(event.target.value)} minLength={10} maxLength={8000} required rows={5} className="mt-1.5 w-full resize-y rounded-xl border border-slate-200 dark:border-white/15 bg-white dark:bg-black/40 px-3.5 py-2.5 text-sm text-slate-900 dark:text-white" placeholder="Record the decision, owner, reason, or next action…" />
          </div>
          <div className="flex flex-wrap items-end gap-4">
            <label className="text-xs font-bold text-slate-700 dark:text-gray-300">Visibility
              <select value={captureAcl} onChange={(event) => setCaptureAcl(event.target.value)} className="mt-1 block rounded-xl border border-slate-200 dark:border-white/15 bg-white dark:bg-black/40 px-3 py-2 text-xs font-medium text-slate-900 dark:text-white">
                <option value="L1">L1 — Team-wide</option><option value="L2">L2 — Maintainers</option><option value="L3">L3 — Leadership-only</option>
              </select>
            </label>
            <label className="flex items-start gap-2 text-xs text-slate-700 dark:text-gray-300">
              <input type="checkbox" checked={captureConsent} onChange={(event) => setCaptureConsent(event.target.checked)} className="mt-0.5" />
              <span>I’m authorised to capture and process this project information.</span>
            </label>
            <Button type="submit" disabled={!captureConsent || savingCapture} className="px-5 py-2.5 font-bold">{savingCapture ? "Saving…" : "Save note"}</Button>
          </div>
          {captureError && <p role="alert" className="text-xs font-semibold text-rose-600">{captureError}</p>}
          {captureSaved && <p role="status" className="text-xs font-semibold text-emerald-700 dark:text-emerald-300">Note captured and added to Connected Sources.</p>}
        </form>
      )}

      <section className="space-y-3 rounded-2xl border border-amber-200 dark:border-amber-500/20 bg-amber-50/50 dark:bg-amber-950/10 p-5">
        <div className="flex items-center justify-between gap-3">
          <div><h2 className="text-lg font-bold text-gray-900 dark:text-white">Source change alerts</h2><p className="text-xs text-slate-600 dark:text-gray-400">Re-import a source with the same name to compare its redacted evidence with the last scan.</p></div>
          <Button variant="quiet" onClick={() => void refetchDrift()}>Check changes</Button>
        </div>
        {driftLoading ? <p role="status" className="text-xs text-slate-600 dark:text-gray-400">Checking the latest source versions…</p> : null}
        {driftError && <p role="alert" className="text-xs font-semibold text-rose-600 dark:text-rose-300">Could not check source changes: {(driftError as Error).message}</p>}
        {driftActionError && <p role="alert" className="text-xs font-semibold text-rose-600 dark:text-rose-300">{driftActionError}</p>}
        {!driftLoading && !driftError && driftAlerts.length === 0 ? <p className="text-xs text-slate-600 dark:text-gray-400">No detected source changes.</p> : null}
        {!driftError && driftAlerts.length > 0 ? (
          <ul className="space-y-2">
            {driftAlerts.map((alert) => <li key={alert.alert_id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 dark:border-amber-500/20 bg-white/80 dark:bg-black/20 p-3 text-xs">
              <span className="text-slate-800 dark:text-gray-200"><strong>{alert.title}</strong> changed · {alert.previous_chunks} → {alert.current_chunks} chunks · {alert.acl_label}</span>
              {user?.role === "approver" && <Button variant="quiet" onClick={() => void handleResolveAlert(alert.alert_id)}>Resolve</Button>}
            </li>)}
          </ul>
        ) : null}
      </section>

      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-gray-900 dark:text-white tracking-tight">Connected Sources</h2>
          <Button variant="quiet" onClick={() => void refetch()}>
            Refresh
          </Button>
        </div>
        {isLoading && <Loading label="Loading sources" />}
        {error && <ErrorState title="Could not load sources" detail={(error as Error).message} onRetry={() => void refetch()} />}
        {data && data.length === 0 && (
          <EmptyState title="No sources yet">
            Upload authorised documents, a Git export or a task CSV to begin.
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
                    <td className="px-4 py-3 text-slate-600 dark:text-gray-300">{source.type}</td>
                    <td className="px-4 py-3">
                      <span className="px-2.5 py-0.5 rounded-md bg-purple-50 dark:bg-purple-950/40 text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-500/20 font-bold text-[10px] font-mono">
                        {source.acl_label}
                      </span>
                    </td>
                    <td className="px-4 py-3 tabular-nums text-slate-600 dark:text-gray-300">{source.chunks}</td>
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
