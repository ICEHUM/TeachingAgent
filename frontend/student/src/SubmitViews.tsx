import { useEffect, useState } from "react";
import { ApiError, api, newOperation } from "./api";
import type { Evaluation } from "./types";

const statusLabel = (status: string) => ({ SATISFIED: "已满足", NOT_SATISFIED: "未满足", INFRASTRUCTURE_ERROR: "环境异常", NOT_RUN: "未运行" }[status] || status);
const helpLabel = (level?: string) => ({ L0: "引导", L1: "定位", L2: "局部示例" }[level || ""] || "指导");
const versionLabel = (value?: string | null) => value ? value.replace(/^Snapshot\s+/i, "版本 ") : "尚未创建";

export function SubmitViews({
  userId,
  attemptId,
  mode,
  explanation,
  hiddenCheck,
  onBack,
  onSubmitted,
}: {
  userId: string;
  attemptId: string;
  mode: "submit" | "recap";
  explanation: string;
  hiddenCheck?: boolean;
  onBack: () => void;
  onSubmitted: () => void;
}) {
  const [data, setData] = useState<Evaluation | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [submissionExplanation, setSubmissionExplanation] = useState(explanation);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<Evaluation>(`/api/product/attempts/${attemptId}/submissions/latest${mode === "submit" ? "?preview=true" : ""}`, userId)
      .then((value) => { if (!cancelled) { setData(value); setSubmissionExplanation(explanation || value.submission.explanation); setError(""); } })
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
          explanation: submissionExplanation,
        }),
      });
      onSubmitted();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : "提交失败");
    } finally { setBusy(false); }
  }

  if (loading) return <div className="eval-loading"><i /><span>正在读取将要提交的代码版本与验收证据…</span></div>;
  if (!data) return <div className="eval-empty"><strong>提交页暂时无法载入</strong><p>{error || "没有可提交的版本"}</p><button className="button" onClick={onBack}>返回工作台</button></div>;

  const recap = mode === "recap";
  const requirements = data.rubric.flatMap((item) => item.requirements);
  const satisfiedCount = requirements.filter((item) => item.status === "SATISFIED").length;
  const needsWork = requirements.filter((item) => item.status === "NOT_SATISFIED");
  const infrastructureCount = requirements.filter((item) => item.status === "INFRASTRUCTURE_ERROR").length;
  const pendingReviewCount = requirements.filter((item) => item.kind === "TEACHER_REVIEW" && item.status === "NOT_RUN").length;
  const hiddenPending = hiddenCheck && !recap && requirements.some((item) => item.key === "addition_hidden_tests" && item.status === "NOT_RUN");
  const notRunCount = requirements.filter((item) => item.kind !== "TEACHER_REVIEW" && item.status === "NOT_RUN" && !(hiddenPending && item.key === "addition_hidden_tests")).length;
  const checkSummary = [
    needsWork.length > 0 ? `${needsWork.length} 项仍需修改` : "",
    infrastructureCount > 0 ? `${infrastructureCount} 项因环境异常未验证` : "",
    notRunCount > 0 ? `${notRunCount} 项尚未检查` : "",
    hiddenPending ? "边界检查将在提交时运行" : "",
    pendingReviewCount > 0 ? `${pendingReviewCount} 项待教师复核` : "",
  ].filter(Boolean).join("，") || (requirements.length ? "全部检查已满足" : "暂无检查记录");

  return <div className="eval-shell">
    <div className="eval-toolbar">
      <button className="button quiet" onClick={onBack}>返回工作台</button>
      <div className="eval-toolbar-copy">
        <strong>{recap ? "学习复盘" : "确认提交"}</strong>
        <p><span>评价针对</span><span className="snapshot-chip">{versionLabel(data.submission.snapshot_label)}</span><span>后续编辑不会改写本次提交</span></p>
      </div>
      {!recap && <button className="button primary" onClick={() => void submit()} disabled={busy || !data.submission.snapshot_id}>{busy ? (hiddenCheck ? "正在检查边界用例…" : "提交中…") : hiddenCheck ? "检查并提交当前版本" : "确认提交当前版本"}</button>}
      {recap && data.formal_grade && <span className="status-badge completed">教师已确认 {data.formal_grade.total_score}/{data.formal_grade.max_score}</span>}
      {recap && !data.formal_grade && <span className="status-badge waiting">等待教师评价</span>}
    </div>
    {error && <div className="eval-alert" role="alert">{error}</div>}
    <div className={`eval-layout ${recap ? "" : "submit-layout"}`}>
      <section className="eval-main submit-main" aria-label={recap ? "提交复盘" : "确认提交"}>
        <article className="eval-block">
          <header><h2>{recap ? "提交版本" : "将被提交的版本"}</h2><span className="snapshot-chip">{versionLabel(data.submission.snapshot_label)}</span></header>
          <p>{hiddenCheck && !recap ? "提交时会先运行不公开具体输入的边界检查；通过后教师才能评价这个版本。" : "教师将评价这个版本；提交后仍可继续编辑，后续修改不会覆盖本次提交。"}</p>
          <div className="submit-facts"><span>{data.files?.length || 0} 个文件将随版本保存</span></div>
          <details className="secondary-details">
            <summary>查看文件清单 <span>{data.files?.length || 0}</span></summary>
            <ul className="file-chips">{(data.files || []).map((file) => <li key={file.path}>{file.path}</li>)}</ul>
          </details>
        </article>
        <article className="eval-block">
          <header><h2>检查结论</h2><span className={needsWork.length ? "check-summary warning" : "check-summary"}>{satisfiedCount}/{requirements.length} 已满足</span></header>
          <p className="check-summary-copy">{checkSummary}，结果绑定 {versionLabel(data.submission.snapshot_label)}。</p>
          {needsWork.length > 0 && <ul className="check-attention">{needsWork.slice(0, 3).map((item) => <li key={item.key}><b>{item.name}</b><span>{statusLabel(item.status)}</span></li>)}</ul>}
          <details className="secondary-details">
            <summary>查看每项检查 <span>{requirements.length}</span></summary>
            <div className="eval-checks">{requirements.map((req) => <div key={req.key} className={req.status.toLowerCase()}><b>{req.name}</b><small>{req.evaluator || "尚未运行"}</small><span>{statusLabel(req.status)}</span></div>)}</div>
          </details>
        </article>
        <article className="eval-block">
          <header><h2>提交说明</h2></header>
          {recap ? <blockquote>{data.submission.explanation || "提交时没有写入调试观察。"}</blockquote> : <label>用一句话说明你排查了什么、做了什么改进<textarea aria-label="提交说明" rows={4} maxLength={2000} value={submissionExplanation} onChange={(event) => setSubmissionExplanation(event.target.value)} /></label>}
        </article>
        <details className="secondary-details assistance-details">
          <summary>查看系统协助记录 <span>{data.submission.assistance.length}</span></summary>
          {data.submission.assistance.length ? data.submission.assistance.map((item, index) => <div className="assist-item" key={`${item.time}-${index}`}><b>{helpLabel(item.level)}</b><p>{item.message}</p></div>) : <p className="muted">本次提交没有智能指导记录。</p>}
        </details>
      </section>
      {recap && <aside className="eval-side" aria-label="复盘与成绩状态">
        <section className="recap-grade-summary">
          <h2>教师反馈</h2>
          {data.formal_grade ? <p><strong className="grade-value">{data.formal_grade.total_score}<small> / {data.formal_grade.max_score}</small></strong><span>教师已发布正式成绩</span></p> : <p>教师尚未发布成绩，当前先查看提交版本和检查结论。</p>}
        </section>
        <details className="secondary-details rubric-details">
          <summary>查看评分依据 <span>{data.rubric.length} 项</span></summary>
          <p>自动检查和检查说明仅供参考，正式成绩由教师确认。</p>
          {data.rubric.map((item) => { const confirmed = item.teacher.status === "confirmed" && item.teacher.score != null; return <article key={item.key} className={`rubric-card ${confirmed ? "confirmed" : "pending"}`}>
            <header><h3>{item.title}</h3><div className="rubric-meta"><span className={`review-state ${confirmed ? "confirmed" : "pending"}`}>{confirmed ? "教师已确认" : "待评价"}</span><small>{item.max_score} 分</small></div></header>
            <dl className="review-lanes">
              <div className="review-lane auto"><dt>自动检查</dt><dd>{item.auto.summary}</dd></div>
              <div className="review-lane ai"><dt>检查说明</dt><dd>{item.ai.text}</dd></div>
              <div className="review-lane teacher"><dt>教师确认</dt><dd>{confirmed ? `${item.teacher.score} 分 · ${item.teacher.reason}` : "待评价"}</dd></div>
            </dl>
          </article>; })}
        </details>
      </aside>}
    </div>
  </div>;
}
