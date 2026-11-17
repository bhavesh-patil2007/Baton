import { EmptyState, StateBadge } from "@/shared/ui";
import type { EvidenceView, PlanBuckets, PlanItem } from "../api";
import { CitationChip } from "./CitationChip";

const BUCKETS: { key: keyof PlanBuckets; title: string; hint: string }[] = [
  { key: "day_1", title: "Day 1", hint: "Nothing blocks these. Start here." },
  { key: "week_1", title: "Week 1", hint: "Each waits on a Day 1 item." },
  { key: "month_1", title: "Month 1", hint: "Each waits on a Week 1 item or later." },
];
const KIND_LABEL: Record<PlanItem["kind"], string> = {
  unblock: "Unblock",
  confirm_owner: "Confirm ownership",
  next_action: "Next action",
};

const STATUS_OPTS: { value: "not_started" | "in_progress" | "blocked" | "completed"; label: string; badge: string }[] = [
  { value: "not_started", label: "Not Started", badge: "bg-slate-100 text-slate-700 border-slate-300 dark:bg-white/10 dark:text-gray-300 dark:border-white/20" },
  { value: "in_progress", label: "In Progress", badge: "bg-blue-50 text-blue-700 border-blue-300 dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-500/30" },
  { value: "blocked", label: "Blocked", badge: "bg-amber-50 text-amber-700 border-amber-300 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-500/30" },
  { value: "completed", label: "Completed", badge: "bg-emerald-50 text-emerald-700 border-emerald-300 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-500/30" },
];

export function PlanPanel({
  plan,
  onOpenCitation,
  onUpdateStatus,
}: {
  plan: PlanBuckets;
  onOpenCitation: (e: EvidenceView) => void;
  onUpdateStatus?: (claimId: string, status: "not_started" | "in_progress" | "blocked" | "completed") => void;
}) {
  const titles = new Map<string, string>();
  Object.values(plan).forEach((items) => items.forEach((i) => titles.set(i.claim_id, i.title)));
  const total = BUCKETS.reduce((n, b) => n + plan[b.key].length, 0);
  if (total === 0) {
    return <EmptyState title="No plan items yet">The approved handover has no next actions, blockers or owners you can see.</EmptyState>;
  }
  return (
    <div className="space-y-8">
      {BUCKETS.map((bucket) => (
        <section key={bucket.key} aria-labelledby={`plan-${bucket.key}`}>
          <h2 id={`plan-${bucket.key}`} className="text-xl font-bold text-gray-900 dark:text-white tracking-tight">{bucket.title}</h2>
          <p className="text-xs text-slate-500 dark:text-gray-400">{bucket.hint}</p>
          {plan[bucket.key].length === 0 ? (
            <p className="mt-2 text-xs text-slate-500 dark:text-gray-500 italic">Nothing planned here.</p>
          ) : (
            <ol className="mt-3.5 space-y-3">
              {plan[bucket.key].map((item) => {
                const currentStatus = item.action_status || "not_started";
                const statusMeta = STATUS_OPTS.find((s) => s.value === currentStatus) || STATUS_OPTS[0];

                return (
                  <li
                    key={item.claim_id}
                    className={`rounded-2xl border p-4 sm:p-5 shadow-sm transition-all ${
                      currentStatus === "completed"
                        ? "border-emerald-200 bg-emerald-50/30 dark:border-emerald-900/40 dark:bg-emerald-950/20"
                        : "border-slate-200 bg-white dark:border-white/10 dark:bg-black/40 dark:backdrop-blur-2xl"
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-mono font-semibold text-purple-700 bg-purple-50 px-2.5 py-0.5 rounded border border-purple-200 dark:text-purple-300 dark:bg-purple-950/40 dark:border-purple-500/20">
                          {item.position}. {KIND_LABEL[item.kind]}
                        </span>
                        <StateBadge state={item.state} />
                      </div>

                      {/* Interactive Action Tracker Status Selector */}
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] font-semibold text-slate-500 dark:text-gray-400 uppercase tracking-wider">
                          Status:
                        </span>
                        {onUpdateStatus ? (
                          <select
                            value={currentStatus}
                            onChange={(e) =>
                              onUpdateStatus(
                                item.claim_id,
                                e.target.value as "not_started" | "in_progress" | "blocked" | "completed"
                              )
                            }
                            aria-label={`Update status for ${item.title}`}
                            className={`rounded-lg px-2.5 py-1 text-xs font-bold border cursor-pointer transition-colors ${statusMeta.badge}`}
                          >
                            {STATUS_OPTS.map((opt) => (
                              <option key={opt.value} value={opt.value}>
                                {opt.label}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <span className={`rounded-lg px-2.5 py-1 text-xs font-bold border ${statusMeta.badge}`}>
                            {statusMeta.label}
                          </span>
                        )}
                      </div>
                    </div>

                    <p className={`mt-2.5 text-base font-semibold ${
                      currentStatus === "completed"
                        ? "line-through text-slate-500 dark:text-gray-400"
                        : "text-slate-900 dark:text-white"
                    }`}>
                      {item.title}
                    </p>

                    <p className="mt-1 text-xs text-slate-500 dark:text-gray-400">
                      Owner: <span className="font-medium text-slate-800 dark:text-gray-200">{item.owner_known ? item.owner : "unknown"}</span>
                      {item.proposed_for_me && " • Proposed for you: accept or decline it on the Handover tab"}
                      {item.due_date && ` • Due ${item.due_date}`}
                    </p>

                    {item.action_notes && (
                      <p className="mt-1.5 text-xs text-blue-800 dark:text-blue-300 bg-blue-50/50 dark:bg-blue-950/30 p-2 rounded-lg border border-blue-200/50 dark:border-blue-500/20">
                        <strong className="font-semibold">Note:</strong> {item.action_notes}
                      </p>
                    )}

                    {item.blocked_by.length > 0 && (
                      <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                        Blocked by: {item.blocked_by.map((id) => titles.get(id) ?? id).join("; ")}
                      </p>
                    )}

                    {item.cycle && (
                      <p role="alert" className="mt-2 text-xs text-rose-800 dark:text-rose-300 border border-rose-200 dark:border-rose-500/30 bg-rose-50 dark:bg-rose-950/30 p-2 rounded-xl">
                        These items block each other. Decide which goes first.
                      </p>
                    )}

                    <div className="mt-3.5 flex flex-wrap gap-2">
                      {item.evidence.map((e) => <CitationChip key={`${e.chunk_id}:${e.quote}`} evidence={e} onOpen={onOpenCitation} />)}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </section>
      ))}
    </div>
  );
}
