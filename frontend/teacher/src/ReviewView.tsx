import { useEffect, useMemo, useState } from "react";
import { ApiError, api, newOperation } from "./api";
import type { Evaluation, RubricItem } from "./types";

const statusLabel = (status: string) => ({ SATISFIED: "已满足", NOT_SATISFIED: "未满足", INFRASTRUCTURE_ERROR: "环境异常", NOT_RUN: "未运行" }[status] || status);

type DraftItem = { key: string; score: string; reason: string; confirmed: boolean };

function toDraft(items: RubricItem[]): DraftItem[] {
  return items.map((item) => ({
    key: item.key,
    score: item.teacher.score == null ? "" : String(item.teacher.score),
    reason: item.teacher.reason,
    confirmed: item.teacher.confirmed,
  }));
}

export function ReviewView({ userId, submissionId, onBack }: { userId: string; submissionId: string; onBack: () => void }) {
  const [data, setData] = useState<Evaluation | null>(null);
  const [draft, setDraft] = useState<DraftItem[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [activeFile, setActiveFile] = useState("");
  const [fileText, setFileText] = useState("");

  async function load(preserveDraft = false) {
    setLoading(true);
    try {
      const value = await api<Evaluation>(`/api/product/submissions/${submissionId}`, userId);
      setData(value);
      if (!preserveDraft) setDraft(toDraft(value.rubric));
      const first = value.files?.[0]?.path || "";
      setActiveFile(first);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "评价页载入失败");
    } finally { setLoading(false); }
  }

  useEffect(() => { void load(); }, [submissionId, userId]);
  useEffect(() => {
    if (!activeFile || !submissionId) return;
    api<{ content: string }>(`/api/product/submissions/${submissionId}/files/${encodeURIComponent(activeFile).replaceAll("%2F", "/")}`, userId)
      .then((value) => setFileText(value.content))
      .catch(() => setFileText("无法读取该提交版本中的文件。"));
  }, [activeFile, submissionId, userId]);

  const published = data?.review.status === "published";
  const missing = useMemo(() => draft.filter((item) => !item.confirmed || item.score === "").map((item) => item.key), [draft]);

  function update(key: string, patch: Partial<DraftItem>) {
    setDraft((items) => items.map((item) => item.key === key ? { ...item, ...patch } : item));
  }

  async function save() {
    if (!data?.review.id) return false;
    setBusy("save"); setError("");
    try {
      const value = await api<Evaluation>(`/api/product/teacher/reviews/${data.review.id}`, userId, {
        method: "PUT",
        body: JSON.stringify({
          items: draft.map((item) => ({
            key: item.key,
            score: item.score === "" ? null : Number(item.score),
            reason: item.reason,
            confirmed: item.confirmed && item.score !== "",
          })),
        }),
      });
      setData(value);
      setDraft(toDraft(value.rubric));
      return true;
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : "草稿保存失败");
      return false;
    } finally { setBusy(""); }
  }

  async function publish() {
    if (!data?.review.id) return;
    setError("");
    const saved = await save();
    if (!saved) return;
    setBusy("publish");
    try {
      await api(`/api/product/teacher/reviews/${data.review.id}/publish`, userId, {
        method: "POST",
        body: JSON.stringify({ operation_id: newOperation("ui-publish") }),
      });
      await load();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : "发布失败");
    } finally { setBusy(""); }
  }

  async function confirmRequirement(key: string, status: "SATISFIED" | "NOT_SATISFIED") {
    if (!data?.submission.id) return;
    const reason = window.prompt("请说明本项复核的依据或需要修改的内容：");
    if (!reason || reason.trim().length < 4) { setError("请填写至少4个字的复核说明。"); return; }
    setBusy(key); setError("");
    try {
      await api(`/api/product/teacher/submissions/${data.submission.id}/requirement-reviews`, userId, {
        method: "POST",
        body: JSON.stringify({ operation_id: newOperation("ui-teacher-review"), requirement_key: key, status, reason: reason.trim() }),
      });
      await load(true);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : "教师复核失败");
    } finally { setBusy(""); }
  }

  if (loading) return <div className="eval-loading"><i /><span>正在读取提交 Snapshot 与量规…</span></div>;
  if (error && !data) return <div className="eval-empty"><strong>评价页暂时无法载入</strong><p>{error}</p><button className="refresh" onClick={onBack}>返回课堂台</button></div>;
  if (!data) return null;
  const checks = [...new Map(data.rubric.flatMap((item) => item.requirements).map((item) => [item.key, item] as const)).values()];

  return <div className="eval-shell">
    <div className="eval-toolbar">
      <button className="refresh" onClick={onBack}>返回课堂台</button>
      <div className="eval-toolbar-copy">
        <strong>作品评价 · {data.student.display_name}</strong>
        <p><span className="snapshot-chip">{data.submission.snapshot_label}</span><span>{data.attempt.stage}</span><span>Submission #{data.submission.sequence}</span></p>
      </div>
      {data.formal_grade ? <span className="status-badge completed">已发布 {data.formal_grade.total_score}/{data.formal_grade.max_score}</span> : <span className="status-badge waiting">待评价 {missing.length} 项</span>}
    </div>
    {error && <div className="eval-alert" role="alert">{error}</div>}
    <div className="eval-layout">
      <section className="eval-main">
        <article className="eval-block">
          <header><h2>作品</h2><span>只读提交版本</span></header>
          <div className="eval-files">
            <nav>{(data.files || []).map((file) => <button key={file.path} className={file.path === activeFile ? "active" : ""} onClick={() => setActiveFile(file.path)}>{file.path}</button>)}</nav>
            <pre>{fileText || "选择一个文件查看提交版本。"}</pre>
          </div>
        </article>
        <article className="eval-block">
          <header><h2>检查</h2></header>
          <div className="eval-checks">{checks.map((req) => <div key={req.key} className={req.status.toLowerCase()}>
            <b>{req.name}</b><small>{req.kind === "TEACHER_REVIEW" ? "教师复核" : req.evaluator || "尚未运行"}</small><span>{statusLabel(req.status)}</span>
            {req.kind === "TEACHER_REVIEW" && !published && <><button className="refresh" disabled={!!busy} onClick={() => void confirmRequirement(req.key, "SATISFIED")}>确认通过</button><button className="refresh" disabled={!!busy} onClick={() => void confirmRequirement(req.key, "NOT_SATISFIED")}>需要修改</button></>}
          </div>)}</div>
        </article>
        <article className="eval-block">
          <header><h2>解释</h2></header>
          <blockquote>{data.submission.explanation || "学生提交时未写入观察。"}</blockquote>
        </article>
        <article className="eval-block">
          <header><h2>变式</h2></header>
          <p>{data.variant.name} · {statusLabel(data.variant.status)}</p>
          <p className="muted">变式任务使用提交 Snapshot 上的证据；未运行时保持待评价。</p>
        </article>
      </section>
      <aside className="eval-side">
        <section>
          <h2>量规</h2>
          <p>自动检查、检查说明和教师确认分开。未确认项保持待评价。</p>
        </section>
        {data.rubric.map((item) => {
          const current = draft.find((entry) => entry.key === item.key) || { key: item.key, score: "", reason: "", confirmed: false };
          const confirmed = current.confirmed && current.score !== "";
          return <article key={item.key} className={`rubric-card ${confirmed ? "confirmed" : "pending"}`}>
            <header><h3>{item.title}</h3><div className="rubric-meta"><span className={`review-state ${confirmed ? "confirmed" : "pending"}`}>{confirmed ? "已确认" : "待评价"}</span><small>{item.max_score} 分</small></div></header>
            <dl className="review-lanes">
              <div className="review-lane auto"><dt>自动检查</dt><dd>{item.auto.summary}</dd></div>
              <div className="review-lane ai"><dt>检查说明</dt><dd>{item.ai.text}</dd></div>
            </dl>
            <div className="teacher-confirmation">
              <div className="teacher-confirmation-heading"><strong>教师确认</strong><span>正式成绩依据</span></div>
              <label>确认分值
                <input aria-label={`${item.title}确认分值`} inputMode="numeric" type="number" min={0} max={item.max_score} value={current.score} disabled={published} placeholder="待评价" onChange={(event) => update(item.key, { score: event.target.value, confirmed: event.target.value !== "" })} />
              </label>
              <label>评价理由
                <textarea rows={2} value={current.reason} disabled={published} onChange={(event) => update(item.key, { reason: event.target.value })} placeholder="说明依据的 Snapshot 与证据。" />
              </label>
              <label className="confirm-row"><input type="checkbox" checked={confirmed} disabled={published || current.score === ""} onChange={(event) => update(item.key, { confirmed: event.target.checked })} />已依据证据确认本项</label>
            </div>
          </article>;
        })}
        <div className="eval-actions">
          <button className="refresh" disabled={published || !!busy} onClick={() => void save()}>{busy === "save" ? "保存中…" : "保存草稿"}</button>
          <button className="primary" disabled={published || missing.length > 0 || !!busy} onClick={() => void publish()}>{published ? "已发布正式成绩" : missing.length ? `还有 ${missing.length} 项待评价` : busy === "publish" ? "发布中…" : "发布正式成绩"}</button>
        </div>
      </aside>
    </div>
  </div>;
}
