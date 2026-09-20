import { useEffect, useState } from "react";
import { ApiError, api, newOperation } from "./api";
import type { Evaluation } from "./types";

const statusLabel = (status: string) => ({ SATISFIED: "已满足", NOT_SATISFIED: "未满足", INFRASTRUCTURE_ERROR: "环境异常", NOT_RUN: "未运行" }[status] || status);
const helpLabel = (level?: string) => ({ L0: "引导", L1: "定位", L2: "局部示例" }[level || ""] || "指导");

export function SubmitViews({
  userId,
  attemptId,
  mode,
  onBack,
  onSubmitted,
}: {
  userId: string;
  attemptId: string;
  mode: "submit" | "recap";
  onBack: () => void;
  onSubmitted: () => void;
}) {
  const [data, setData] = useState<Evaluation | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<Evaluation>(`/api/product/attempts/${attemptId}/submissions/latest`, userId)
      .then((value) => { if (!cancelled) { setData(value); setError(""); } })
      .catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : "提交页载入失败"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [attemptId, userId, mode]);

  async function submit() {
    if (!data?.submission.snapshot_id) return;
    setBusy(true);
    try {
      await api(`/api/product/attempts/${attemptId}/submissions`, userId, {
        method: "POST",
        body: JSON.stringify({
          operation_id: newOperation("ui-submit"),
          snapshot_id: data.submission.snapshot_id,
          explanation: data.submission.explanation,
        }),
      });
      onSubmitted();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : "提交失败");
    } finally { setBusy(false); }
  }

  if (loading) return <div className="eval-loading"><i /><span>正在读取将要提交的 Snapshot 与验收证据…</span></div>;
  if (error || !data) return <div className="eval-empty"><strong>提交页暂时无法载入</strong><p>{error || "没有可提交的版本"}</p><button className="button" onClick={onBack}>返回工作台</button></div>;

  const submitted = Boolean(data.submission.id);
  const recap = mode === "recap" || submitted;

  return <div className="eval-shell">
    <div className="eval-toolbar">
      <button className="button quiet" onClick={onBack}>返回工作台</button>
      <div className="eval-toolbar-copy">
        <strong>{recap ? "学习复盘" : "确认提交"}</strong>
        <p><span>评价针对</span><span className="snapshot-chip">{data.submission.snapshot_label}</span><span>后续编辑不会改写本次提交</span></p>
      </div>
      {!recap && <button className="button primary" onClick={() => void submit()} disabled={busy || !data.submission.snapshot_id}>{busy ? "提交中…" : "确认提交当前 Snapshot"}</button>}
      {recap && data.formal_grade && <span className="status-badge completed">教师已确认 {data.formal_grade.total_score}/{data.formal_grade.max_score}</span>}
      {recap && !data.formal_grade && <span className="status-badge waiting">等待教师评价</span>}
    </div>
    {error && <div className="eval-alert" role="alert">{error}</div>}
    <div className="eval-layout">
      <section className="eval-main" aria-label="提交作品与证据">
        <article className="eval-block">
          <header><h2>将被提交的版本</h2><span>{data.submission.snapshot_label}</span></header>
          <p>提交后仍可继续编辑，但教师评价只看这一版 Snapshot。</p>
          <ul className="file-chips">{(data.files || []).map((file) => <li key={file.path}>{file.path}</li>)}</ul>
        </article>
        <article className="eval-block">
          <header><h2>自动检查</h2><span>绑定当前提交版本</span></header>
          <div className="eval-checks">{data.rubric.flatMap((item) => item.requirements).map((req) => <div key={req.key} className={req.status.toLowerCase()}><b>{req.name}</b><small>{req.evaluator || "尚未运行"}</small><span>{statusLabel(req.status)}</span></div>)}</div>
        </article>
        <article className="eval-block">
          <header><h2>学生解释</h2></header>
          <blockquote>{data.submission.explanation || "提交时没有写入调试观察。"}</blockquote>
        </article>
        <article className="eval-block">
          <header><h2>系统内协助记录</h2><span>{data.submission.assistance.length} 条</span></header>
          {data.submission.assistance.length ? data.submission.assistance.map((item, index) => <div className="assist-item" key={`${item.time}-${index}`}><b>{helpLabel(item.level)}</b><p>{item.message}</p></div>) : <p className="muted">本次提交没有智能指导记录。</p>}
        </article>
      </section>
      <aside className="eval-side" aria-label="复盘与成绩状态">
        <section>
          <h2>自动检查 / AI 建议 / 教师确认</h2>
          <p>三栏分开。AI 建议不会自动变成正式分数。</p>
        </section>
        {data.rubric.map((item) => { const confirmed = item.teacher.status === "confirmed" && item.teacher.score != null; return <article key={item.key} className={`rubric-card ${confirmed ? "confirmed" : "pending"}`}>
          <header><h3>{item.title}</h3><div className="rubric-meta"><span className={`review-state ${confirmed ? "confirmed" : "pending"}`}>{confirmed ? "教师已确认" : "待评价"}</span><small>{item.max_score} 分</small></div></header>
          <dl className="review-lanes">
            <div className="review-lane auto"><dt>自动检查</dt><dd>{item.auto.summary}</dd></div>
            <div className="review-lane ai"><dt>AI 建议</dt><dd>{item.ai.text}</dd></div>
            <div className="review-lane teacher"><dt>教师确认</dt><dd>{confirmed ? `${item.teacher.score} 分 · ${item.teacher.reason}` : "待评价"}</dd></div>
          </dl>
        </article>; })}
        <section className="eval-grade">
          <h3>正式成绩</h3>
          {data.formal_grade ? <p><strong className="grade-value">{data.formal_grade.total_score}<small> / {data.formal_grade.max_score}</small></strong><span>教师已发布</span></p> : <p>尚未发布。未填项保持待评价，不会用 0 或满分占位。</p>}
        </section>
      </aside>
    </div>
  </div>;
}
