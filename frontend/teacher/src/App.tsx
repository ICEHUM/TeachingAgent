import { AuthEntry } from "../../shared/AuthEntry";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, newOperation } from "./api";
import { CourseBuilder } from "./CourseBuilder";
import { ClassManager } from "./ClassManager";
import { ReviewView } from "./ReviewView";
import { TeacherAssistant } from "./TeacherAssistant";
import { ThemeToggle, useTheme } from "../../shared/theme";
import type { Classroom, ClassroomItem, Detail, Evidence } from "./types";

type GroupKey = "attention" | "progress" | "completed";
const groupNames: Record<GroupKey, string> = { attention: "待处理", progress: "进行中", completed: "已完成" };
const reasonNames: Record<string, string> = { student_help_requested: "学生主动求助", student_requested_help: "学生主动求助", failure_threshold_reached: "连续检查未通过", l2_authorization_required: "需要授权局部示例", pending_review: "提交待复核", grade_published: "成绩已发布", normal: "正常学习中" };
const helpNames: Record<string, string> = { L0: "引导", L1: "定位", L2: "局部示例", NONE: "尚未指导" };
const requirementStatus = (value: string) => ({ SATISFIED: "已满足", NOT_SATISFIED: "未满足", INFRASTRUCTURE_ERROR: "环境异常", NOT_RUN: "未运行" }[value] || value);
const evaluatorLabel = (value: string) => value.includes("run_python_public_tests") ? "隔离公开检查" : value.includes("run_python_hidden_tests") ? "提交边界检查" : value === "python_cases_v1" ? "Python 样例检查" : value.startsWith("openhands:") ? "隔离运行检查" : value.startsWith("static:") ? "静态检查" : value;
const diagnosisNames: Record<string, string> = { boundary_condition: "分数边界判断不符", missing_for_traversal: "未用 for 遍历 numbers", missing_if_else: "缺少 if/else 分支", empty_list_output: "空列表时输出不符", output_mismatch: "输出与预期不符", string_concatenation: "输入被当作文本拼接", syntax_error: "语法错误", indentation_error: "缩进错误", runtime_error: "运行时错误", missing_output: "没有输出" };
const skillNames: Record<string, string> = { input_conversion: "整数输入", integer_addition: "整数相加", if_else: "条件分支", comparison_boundary: "边界比较", for_traversal: "列表遍历", accumulator_initialization: "累加初始化" };
const versionLabel = (value?: string | null) => value ? value.replace(/^Snapshot\s+/i, "版本 ") : "尚未运行";
const time = (value: string) => new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
const waitLabel = (seconds: number) => {
  if (seconds >= 86400) return `已等待 ${Math.floor(seconds / 86400)} 天`;
  if (seconds >= 3600) return `已等待 ${Math.floor(seconds / 3600)} 小时`;
  return `已等待 ${Math.floor(seconds / 60)} 分钟`;
};

export function App() {
  const { theme, toggleTheme } = useTheme();
  const params = useMemo(() => new URLSearchParams(location.search), []);
  const userId = params.get("user") || params.get("user_id") || "";
  const healthRequested = params.get("view") === "health";
  const [classroom, setClassroom] = useState<Classroom | null>(null);
  const [courseId, setCourseId] = useState<string | null>(null);
  const [group, setGroup] = useState<GroupKey>("attention");
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [prompt, setPrompt] = useState("");
  const [actionLoading, setActionLoading] = useState("");
  const [feedbackNotice, setFeedbackNotice] = useState("");
  const [actionError, setActionError] = useState("");
  const [detailOpen, setDetailOpen] = useState(false);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [connection, setConnection] = useState<"connected" | "reconnecting" | "offline">("reconnecting");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [workspace, setWorkspace] = useState<"classroom" | "assistant" | "courses" | "classes">(params.get("view") === "assistant" ? "assistant" : params.get("view") === "courses" ? "courses" : params.get("view") === "classes" ? "classes" : "classroom");
  const [assistantOpened, setAssistantOpened] = useState(params.get("view") === "assistant");
  const selectedRef = useRef<string | null>(null);
  const courseIdRef = useRef<string | null>(null);
  const selectionInitialized = useRef(false);
  const detailPanelRef = useRef<HTMLElement | null>(null);
  const studentTriggerRef = useRef<HTMLElement | null>(null);

  const loadClassroom = useCallback(async () => {
    if (!userId) { setLoading(false); return; }
    try {
      const data = await api<Classroom>("/api/product/teacher/classroom", userId);
      setClassroom(data); setError(""); setConnection("connected"); setLastUpdated(new Date());
      const activeCourse = courseIdRef.current && data.courses.some((course) => course.id === courseIdRef.current)
        ? courseIdRef.current
        : (data.courses.find((course) => course.code === "DEMO-PYTHON-BASICS-001") || data.courses[0])?.id || null;
      courseIdRef.current = activeCourse;
      setCourseId(activeCourse);
      const all = [...data.groups.attention, ...data.groups.progress, ...data.groups.completed].filter((item) => item.course_id === activeCourse);
      if (!selectionInitialized.current || (selectedRef.current && !all.some((item) => item.attempt_id === selectedRef.current))) {
        const first = all[0];
        selectedRef.current = first?.attempt_id || null;
        setSelected(selectedRef.current);
        if (first) setGroup(first.category as GroupKey);
        selectionInitialized.current = true;
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "课堂数据载入失败"); setConnection(navigator.onLine ? "reconnecting" : "offline"); }
    finally { setLoading(false); }
  }, [userId]);

  const loadDetail = useCallback(async (attemptId: string) => {
    setDetailLoading(true);
    try { const value = await api<Detail>(`/api/product/teacher/attempts/${attemptId}`, userId); if (selectedRef.current === attemptId) { setDetail(value); setActionError(""); } }
    catch (cause) { setActionError(cause instanceof Error ? cause.message : "学生详情载入失败"); }
    finally { setDetailLoading(false); }
  }, [userId]);

  useEffect(() => { void loadClassroom(); }, [loadClassroom]);
  useEffect(() => { if (selected) void loadDetail(selected); }, [loadDetail, selected]);
  useEffect(() => {
    if (!detailOpen || !matchMedia("(max-width: 1050px)").matches) return;
    const panel = detailPanelRef.current;
    const focusable = () => panel ? Array.from(panel.querySelectorAll<HTMLElement>('button:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])')) : [];
    requestAnimationFrame(() => focusable()[0]?.focus());
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setDetailOpen(false); requestAnimationFrame(() => studentTriggerRef.current?.focus()); return; }
      if (event.key === "Tab") { const items = focusable(); if (!items.length) return; const first = items[0]; const last = items[items.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }
    };
    addEventListener("keydown", close);
    return () => removeEventListener("keydown", close);
  }, [detailOpen]);
  useEffect(() => {
    const offline = () => setConnection("offline"); const online = () => { setConnection("reconnecting"); void loadClassroom().then(() => setConnection("connected")); };
    addEventListener("offline", offline); addEventListener("online", online);
    const refresh = window.setInterval(() => { if (navigator.onLine) void loadClassroom(); }, 5000);
    return () => { clearInterval(refresh); removeEventListener("offline", offline); removeEventListener("online", online); };
  }, [loadClassroom]);
  useEffect(() => {
    if (!selected) return;
    const timer = setInterval(() => {
      void api<Detail>(`/api/product/teacher/attempts/${selected}`, userId).then((value) => { if (selectedRef.current === selected) setDetail(value); }).catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, [selected, userId]);

  function chooseGroup(key: GroupKey) {
    setGroup(key);
    setPrompt(""); setFeedbackNotice("");
    const first = classroom?.groups[key].find((item) => item.course_id === courseIdRef.current);
    selectedRef.current = first?.attempt_id || null;
    setSelected(selectedRef.current);
    setDetail(null);
    setDetailOpen(false);
  }

  function chooseStudent(attemptId: string, trigger: HTMLElement) {
    studentTriggerRef.current = trigger;
    selectedRef.current = attemptId;
    setSelected(attemptId);
    setPrompt(""); setFeedbackNotice("");
    setDetailOpen(true);
  }

  function chooseCourse(nextCourseId: string) {
    courseIdRef.current = nextCourseId;
    setCourseId(nextCourseId);
    setGroup("attention");
    setPrompt(""); setFeedbackNotice("");
    const first = classroom?.groups.attention.find((item) => item.course_id === nextCourseId)
      || classroom?.groups.progress.find((item) => item.course_id === nextCourseId)
      || classroom?.groups.completed.find((item) => item.course_id === nextCourseId);
    selectedRef.current = first?.attempt_id || null;
    setSelected(selectedRef.current);
    if (first) setGroup(first.category as GroupKey);
    setDetail(null); setDetailOpen(false);
  }

  function inspectFromAssistant(attemptId: string) {
    if (!attemptId) return;
    const item = classroom?.groups.attention.concat(classroom.groups.progress, classroom.groups.completed).find((candidate) => candidate.attempt_id === attemptId);
    if (item) { setGroup(item.category as GroupKey); if (item.course_id) { courseIdRef.current = item.course_id; setCourseId(item.course_id); } }
    selectedRef.current = attemptId; setSelected(attemptId); setDetail(null); setDetailOpen(true); chooseWorkspace("classroom");
    if (selected === attemptId) void loadDetail(attemptId);
  }

  function chooseWorkspace(next: "classroom" | "assistant" | "courses" | "classes") {
    if (next === "assistant") { setAssistantOpened(true); setDetailOpen(false); }
    setWorkspace(next);
    const url = new URL(location.href);
    if (next !== "classroom") url.searchParams.set("view", next);
    else url.searchParams.delete("view");
    history.replaceState(null, "", url);
  }

  async function takeAction(action: "continue" | "allow_l2" | "pause_ai" | "resume" | "feedback") {
    if (!detail) return;
    if (action === "feedback") {
      if (!prompt.trim()) { setActionError("请先写下给学生的指导。"); return; }
      setActionLoading(action); setActionError(""); setFeedbackNotice("");
      try {
        const fresh = await api<Detail>(`/api/product/teacher/attempts/${detail.attempt.id}`, userId);
        await api(`/api/product/teacher/attempts/${detail.attempt.id}/feedback`, userId, {method: "POST", body: JSON.stringify({
          operation_id: newOperation("teacher-feedback"), expected_state_version: fresh.attempt.state_version, message: prompt,
        })});
        setPrompt(""); setFeedbackNotice("指导已发送，学生工作台会显示你的回复。");
        await loadClassroom(); await loadDetail(detail.attempt.id);
      } catch (cause) { setActionError(cause instanceof Error ? cause.message : "发送失败，请重试"); }
      finally { setActionLoading(""); }
      return;
    }
    const intervention = detail.interventions.find((item) => ["WAITING_TEACHER", "RESUME_FAILED"].includes(item.status));
    if (!intervention) { setActionError("当前没有可处理的教师介入事项。"); return; }
    setActionLoading(action); setActionError("");
    try {
      await api(`/api/product/teacher/interventions/${intervention.id}/action`, userId, {
        method: "POST", body: JSON.stringify({ operation_id: newOperation(`teacher-${action}`), expected_state_version: intervention.requested_state_version, action, teacher_prompt: prompt }),
      });
      setPrompt(""); await loadClassroom(); await loadDetail(detail.attempt.id);
    } catch (cause) {
      setActionError(cause instanceof ApiError && cause.code === "resume_failed" ? "恢复失败：教师操作已保留，可以安全重试。" : cause instanceof Error ? cause.message : "教师操作失败");
      await loadDetail(detail.attempt.id);
    } finally { setActionLoading(""); }
  }

  if (!userId) return <Setup />;
  if (healthRequested) return <DemoHealthView userId={userId} theme={theme} onToggle={toggleTheme} />;
  if (loading) return <Loading />;
  if (error || !classroom) return <Fatal message={error || "课堂工作台不可用"} retry={() => { setLoading(true); void loadClassroom(); }} />;
  if (reviewId) return <div className="teacher-app"><header className="topbar"><div className="brand"><span className="brand-mark">AI</span><strong>教师课堂台</strong></div><div className="top-actions"><ThemeToggle theme={theme} onToggle={toggleTheme} /></div></header><ReviewView userId={userId} submissionId={reviewId} onBack={() => { setReviewId(null); void loadClassroom(); }} /></div>;
  const activeCourse = classroom.courses.find((course) => course.id === courseId);
  const visibleGroups = {
    attention: classroom.groups.attention.filter((item) => item.course_id === courseId),
    progress: classroom.groups.progress.filter((item) => item.course_id === courseId),
    completed: classroom.groups.completed.filter((item) => item.course_id === courseId),
  };
  const currentList = visibleGroups[group];
  const groupTitle = group === "attention" ? "现在有哪些事要处理" : group === "progress" ? "跟进正在学习的学生" : "查看已完成的学习记录";
  const latestByStudentTask = new Map<string, ClassroomItem>();
  [...visibleGroups.attention, ...visibleGroups.progress, ...visibleGroups.completed]
    .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""))
    .forEach((item) => { const key = `${item.student_id}:${item.task_key}`; if (!latestByStudentTask.has(key)) latestByStudentTask.set(key, item); });
  const failedByTask = new Map<string, { title: string; count: number; sample: ClassroomItem }>();
  for (const item of latestByStudentTask.values()) {
    if (!item.task_key?.startsWith("PYB-") || item.public_check_status !== "NOT_SATISFIED") continue;
    const existing = failedByTask.get(item.task_key);
    if (existing) existing.count += 1;
    else failedByTask.set(item.task_key, { title: item.task_title, count: 1, sample: item });
  }
  const patterns = [...failedByTask.values()].sort((a, b) => b.count - a.count || a.title.localeCompare(b.title)).slice(0, 2);
  const uncheckedCount = [...latestByStudentTask.values()].filter((item) => item.task_key?.startsWith("PYB-") && item.public_check_status === "NOT_RUN").length;

  const topbar = <header className="topbar">
      <div className="brand"><span className="brand-mark">AI</span><strong>教师课堂台</strong></div>
      <div className="course-context"><b>{workspace === "courses" ? "课程设计" : workspace === "classes" ? "班级管理" : activeCourse?.name || "课堂"}</b>{["classroom", "assistant"].includes(workspace) && activeCourse?.code.startsWith("DEMO-") && <span>示例数据</span>}</div>
      <nav className="workspace-nav" aria-label="教师工作区"><button className={workspace === "classroom" ? "active" : ""} aria-current={workspace === "classroom" ? "page" : undefined} onClick={() => chooseWorkspace("classroom")}>课堂</button><button className={workspace === "assistant" ? "active" : ""} aria-current={workspace === "assistant" ? "page" : undefined} onClick={() => chooseWorkspace("assistant")}>教学对话</button><button className={workspace === "courses" ? "active" : ""} aria-current={workspace === "courses" ? "page" : undefined} onClick={() => chooseWorkspace("courses")}>课程设计</button><button className={workspace === "classes" ? "active" : ""} aria-current={workspace === "classes" ? "page" : undefined} onClick={() => chooseWorkspace("classes")}>班级</button></nav>
      {feedbackNotice && <span role="status">{feedbackNotice}</span>}<div className="top-actions"><ThemeToggle theme={theme} onToggle={toggleTheme} /><span className={`connection ${connection}`}><i />{connection === "connected" ? "自动更新" : connection === "offline" ? "网络断开" : "正在重连"}{lastUpdated && <small>· {time(lastUpdated.toISOString())}</small>}</span><span className="avatar" aria-label={`当前教师：${classroom.teacher.display_name}`}>{classroom.teacher.display_name.slice(0, 1)}</span></div>
    </header>;

  return <div className="teacher-app">
    {topbar}
    {assistantOpened && <div className="assistant-workspace" hidden={workspace !== "assistant"}><TeacherAssistant userId={userId} onInspect={inspectFromAssistant} /></div>}
    {workspace === "courses" && <CourseBuilder userId={userId} />}
    {workspace === "classes" && <ClassManager userId={userId} onOpenCourses={() => chooseWorkspace("courses")} />}
    {workspace === "classroom" && <main className="teacher-layout">
      <section className="classroom-list" aria-label="课堂学生列表">
        <header className="list-head"><div className="list-head-copy"><h1>{groupTitle}</h1><p>{activeCourse?.name || "当前课程"} · 按教师介入优先级查看</p></div><label className="classroom-course-picker"><span>课程</span><select aria-label="选择课堂课程" value={courseId || ""} onChange={(event) => chooseCourse(event.target.value)}>{classroom.courses.map((course) => <option key={course.id} value={course.id}>{course.name}</option>)}</select></label><button className="refresh" onClick={() => void loadClassroom()}>刷新数据</button></header>
        {activeCourse?.code === "DEMO-PYTHON-BASICS-001" && <section className="classroom-patterns" aria-label="公开检查共性问题"><div><strong>公开检查信号</strong><span>按每名学生每题的最近尝试统计，可点开核对证据</span></div>{patterns.length ? patterns.map((pattern) => <button key={pattern.sample.task_key} onClick={(event) => { setGroup(pattern.sample.category as GroupKey); chooseStudent(pattern.sample.attempt_id, event.currentTarget); }}><b>{pattern.title}</b><span>{pattern.count} 人未通过 · 查看学生</span></button>) : <span className="pattern-empty">当前没有公开检查未通过记录</span>}{uncheckedCount > 0 && <span className="pattern-unchecked">{uncheckedCount} 人次尚未检查</span>}</section>}
        <nav className="group-tabs" aria-label="学生状态分组">{(Object.keys(groupNames) as GroupKey[]).map((key) => <button key={key} aria-current={group === key} onClick={() => chooseGroup(key)}><span>{groupNames[key]}</span><b>{visibleGroups[key].length}</b></button>)}</nav>
        <div className="student-list">{currentList.length ? currentList.map((item) => <StudentRow key={item.attempt_id} item={item} selected={selected === item.attempt_id} onSelect={(trigger) => chooseStudent(item.attempt_id, trigger)} />) : <EmptyGroup group={group} />}{currentList.length === 1 && <div className="queue-note"><strong>当前队列已按介入优先级排序</strong><span>新的教学事件会自动归入需要关注、进行中或已完成。</span></div>}</div>
      </section>
      {detailOpen && <button className="detail-backdrop" aria-label="返回学生列表" onClick={() => setDetailOpen(false)} />}
      <aside ref={detailPanelRef} className={`intervention-panel ${detailOpen ? "open" : ""}`} role={detailOpen ? "dialog" : undefined} aria-modal={detailOpen ? "true" : undefined} aria-label="教师介入详情">
        {!selected ? <EmptyDetail /> : detailLoading || !detail ? <DetailLoading /> : <InterventionDetail key={detail.attempt.id} detail={detail} prompt={prompt} setPrompt={setPrompt} actionLoading={actionLoading} actionError={actionError} takeAction={takeAction} onClose={() => { setDetailOpen(false); requestAnimationFrame(() => studentTriggerRef.current?.focus()); }} userId={userId} onReview={(id) => setReviewId(id)} />}
      </aside>
    </main>}
  </div>;
}

function StudentRow({ item, selected, onSelect }: { item: ClassroomItem; selected: boolean; onSelect: (trigger: HTMLElement) => void }) {
  const attention = item.category === "attention";
  const status = item.intervention_status === "RESUME_FAILED" ? "恢复失败" : attention ? "等待教师" : item.category === "completed" ? "已完成" : "学习中";
  const wait = attention && item.wait_seconds > 60 ? waitLabel(item.wait_seconds) : status;
  return <button className={`student-row ${selected ? "selected" : ""} ${attention ? "needs-attention" : ""}`} onClick={(event) => onSelect(event.currentTarget)} aria-pressed={selected}>
    <span className="student-identity"><span className="student-avatar" aria-hidden="true">{item.student.slice(0, 1)}</span><span><b>{item.student}</b><small>{item.task_title} · {item.stage}</small></span></span>
    <span className="student-need"><b>{reasonNames[item.reason] || item.reason}</b>{item.intervention_status === "RESUME_FAILED" && <em>可安全重试</em>}</span>
    <span className={`status-badge row-status ${item.intervention_status === "RESUME_FAILED" ? "failed" : attention ? "waiting" : item.category === "completed" ? "completed" : "progress"}`}>{wait}</span><span className="row-chevron" aria-hidden="true">›</span>
  </button>;
}

function TeacherEvidence({ evidence }: { evidence: Evidence }) {
  const checks = evidence.checks || [];
  const skills = evidence.skill_evidence || [];
  return <section className={`teacher-evidence-detail ${evidence.status === "SATISFIED" ? "passed" : "failed"}`}>
    <header><div><span>教学结论</span><h4>{evidence.status === "SATISFIED" ? "当前要求已满足" : "当前要求尚未满足"}</h4></div><b>{versionLabel(evidence.snapshot)}</b></header>
    <p>{checks.length ? "以下结果来自这次版本的公开样例检查。" : evidence.reason_code === "empty_retrieval" ? "已知问题没有检索到资料；请结合学生观察判断下一步。" : "该结论来自当前版本的运行证据。"}</p>
    {checks.length > 0 && <ol className="teacher-case-results">{checks.map((check, index) => <li key={`${check.code}-${index}`} className={check.passed ? "passed" : "failed"}><span aria-hidden="true">{check.passed ? "✓" : "×"}</span><div><strong>{check.code.startsWith("case_") ? `公开样例 ${index + 1}` : check.code === "structure_check" ? "语法结构" : "检查项"} · {check.passed ? "通过" : "未通过"}</strong>{!check.passed && check.diagnosis_code && <b>{diagnosisNames[check.diagnosis_code] || "检查结果不符"}</b>}<small>{check.detail}</small></div></li>)}</ol>}
    {skills.length > 0 && <div className="teacher-skill-evidence"><strong>本次样例涉及的技能</strong><p>{skills.map((skill) => `${skillNames[skill.skill_id] || skill.skill_id}：${skill.status === "verified_in_sample" ? "样例通过" : "需检查"}`).join(" · ")}</p><small>仅表示这次公开样例的结果，不代表整体掌握程度。</small></div>}
    {evidence.error_summary && <pre className="teacher-evidence-error">{evidence.error_summary}</pre>}
    <dl><div><dt>检查工具</dt><dd>{evaluatorLabel(evidence.tool)}</dd></div><div><dt>运行时间</dt><dd>{new Date(evidence.observed_at).toLocaleString("zh-CN")}</dd></div></dl>
    <details><summary>查看原始输出与运行标识</summary><pre>{evidence.stdout_summary || "无可展示输出"}</pre><code>{evidence.operation}</code></details>
  </section>;
}

function InterventionDetail({ detail, prompt, setPrompt, actionLoading, actionError, takeAction, onClose, userId, onReview }: { detail: Detail; prompt: string; setPrompt: (value: string) => void; actionLoading: string; actionError: string; takeAction: (action: "continue" | "allow_l2" | "pause_ai" | "resume" | "feedback") => void; onClose: () => void; userId: string; onReview: (submissionId: string) => void }) {
  const active = detail.interventions.find((item) => ["WAITING_TEACHER", "RESUME_FAILED"].includes(item.status));
  const hasResolved = detail.interventions.some((item) => item.status === "RESOLVED");
  const statusTone = active?.status === "RESUME_FAILED" ? "failed" : active ? "waiting" : detail.attempt.status === "COMPLETED" ? "completed" : hasResolved ? "resolved" : "progress";
  const statusText = active?.status === "RESUME_FAILED" ? "恢复失败" : active ? "等待处理" : detail.attempt.status === "COMPLETED" ? "已完成" : hasResolved ? "流程已恢复" : "学习中";
  const [tab, setTab] = useState<"overview" | "evidence" | "trace">("overview");
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState("");
  const [actionsOpen, setActionsOpen] = useState(!!active);
  const [stageReviewNotice, setStageReviewNotice] = useState("");
  const [reviewBusy, setReviewBusy] = useState(false);
  async function reviewStage(key: string, status: string) {
    if (!detail.latest_snapshot || !prompt.trim()) { setStageReviewNotice("先填写下方的教学提示，说明本项依据或修改意见。"); return; }
    setReviewBusy(true);
    try {
      await api(`/api/product/teacher/attempts/${detail.attempt.id}/stage-reviews`, userId, {method: "POST", body: JSON.stringify({
        operation_id: newOperation("stage-review"), snapshot_id: detail.latest_snapshot.id,
        requirement_key: key, status, reason: prompt,
      })});
      setStageReviewNotice("复核已保存，学生可查看意见后检查并继续。");
    } catch (cause) { setStageReviewNotice(cause instanceof Error ? cause.message : "保存失败，请重试"); }
    finally { setReviewBusy(false); }
  }
  const interventionBasis = detail.requirements.find((item) => item.status === "NOT_SATISFIED") || detail.requirements[0];
  async function openEvidence(operationId: string | null) {
    if (!operationId) return;
    setEvidenceLoading(operationId);
    try { setSelectedEvidence(await api<Evidence>(`/api/product/attempts/${detail.attempt.id}/evidence/${encodeURIComponent(operationId)}`, userId)); }
    catch { setSelectedEvidence(null); }
    finally { setEvidenceLoading(""); }
  }
  function chooseTab(next: "overview" | "evidence" | "trace") { setTab(next); }
  return <>
    <header className="detail-head"><button className="detail-back" onClick={onClose} aria-label="返回学生列表">返回</button><span className="detail-avatar" aria-hidden="true">{detail.identity.display_name.slice(0, 1)}</span><div><h2>{detail.identity.display_name}</h2><p>{detail.stage.title} · 阶段 {detail.stage.position + 1}/{detail.stage.total}</p></div>{detail.latest_submission && <button className="refresh" onClick={() => onReview(detail.latest_submission!.id)}>{detail.latest_submission.formal_grade ? "查看评价" : "评价作品"}</button>}<span className={`status-badge intervention-status ${statusTone}`}>{statusText}</span></header>
    <nav className="detail-tabs" aria-label="学生详情分组"><button aria-current={tab === "overview"} onClick={() => chooseTab("overview")}>概览</button><button aria-current={tab === "evidence"} onClick={() => chooseTab("evidence")}>证据 <span>{detail.requirement_summary.satisfied_count}/{detail.requirement_summary.required_count}</span></button><button aria-current={tab === "trace"} onClick={() => chooseTab("trace")}>教学过程 <span>{detail.timeline.length}</span></button></nav>
    <div className="detail-content">
      <div className="detail-scroll">
        {tab === "overview" && <div className="detail-tab-panel overview-panel"><section className="learning-summary"><div><span>当前阶段</span><h3>{detail.stage.title}</h3><p>{detail.stage.objective}</p></div><div className="requirement-meter"><b>{detail.requirement_summary.satisfied_count}<small> / {detail.requirement_summary.required_count}</small></b><span>验收项已满足</span></div></section><section className="detail-section"><div className="section-heading"><h3>学生观察</h3><span>最近一次提交</span></div><blockquote>{detail.student_observation || "学生尚未提交有效观察。"}</blockquote></section><section className="detail-section"><div className="section-heading"><h3>最近指导</h3><span>{detail.guidance_history.length} 条记录</span></div><div className="guidance-history">{detail.guidance_history.length ? detail.guidance_history.slice(-2).reverse().map((item, index) => <article key={`${item.time}-${index}`}><header><b>{helpNames[item.level || ""] || "引导"}</b><time>{time(item.time)}</time></header><p>{item.message}</p></article>) : <p className="muted">尚无智能指导记录。</p>}</div></section><details className="detail-section advanced-detail"><summary>查看版本变化 · {detail.snapshot_diff.files_changed} 个文件</summary><div className="diff-summary"><div><span>{detail.snapshot_diff.from ? versionLabel(detail.snapshot_diff.from) : "尚无前序版本"}</span><b>→</b><span>{detail.snapshot_diff.to ? versionLabel(detail.snapshot_diff.to) : "当前版本"}</span></div><p>{detail.snapshot_diff.files_changed} 个文件变化 · <strong>+{detail.snapshot_diff.additions}</strong> / <em>−{detail.snapshot_diff.deletions}</em></p>{detail.snapshot_diff.files?.length ? <small>{detail.snapshot_diff.files.join("、")}</small> : null}</div></details></div>}
        {tab === "evidence" && <div className="detail-tab-panel evidence-panel"><div className="panel-intro"><h3>当前阶段验收证据</h3><p>先阅读教学结论，再决定介入方式。</p></div><div className="teacher-requirements">{detail.requirements.map((item) => <button type="button" key={item.id} className={item.status.toLowerCase()} disabled={!item.operation_id || !!evidenceLoading} onClick={() => void openEvidence(item.operation_id)}><span className="requirement-state" aria-hidden="true">{item.status === "SATISFIED" ? "✓" : item.status === "NOT_SATISFIED" ? "×" : "·"}</span><div><h4>{item.name}</h4><p>{versionLabel(item.snapshot_label)} · {evaluatorLabel(item.evaluator)}</p><small>{evidenceLoading === item.operation_id ? "正在读取证据…" : item.evidence_refs.length ? `${item.evidence_refs.length} 条证据 · 点击查看` : "尚无证据"}</small></div><b>{requirementStatus(item.status)}</b></button>)}</div>{selectedEvidence && <TeacherEvidence evidence={selectedEvidence} />}</div>}
        {tab === "trace" && <div className="detail-tab-panel trace-panel"><section className="detail-section"><div className="section-heading"><h3>教学过程</h3><span>{detail.timeline.length} 个事件</span></div><div className="teaching-timeline"><ol>{detail.timeline.length ? detail.timeline.slice(-10).map((item) => <li key={`${item.state_version}-${item.time}`}><time>{time(item.time)}</time><i/><span>{item.label}</span></li>) : <li className="empty-line">尚无可解释业务事件</li>}</ol></div></section><details className="detail-section advanced-detail"><summary>查看运行依据</summary><div className="agent-trace">{detail.agent_trace.length ? detail.agent_trace.map((item, index) => <div key={`${item.kind}-${index}`}><span>{index + 1}</span><p><b>{item.label}</b><small>{item.detail}</small></p></div>) : <p className="muted">暂无运行依据。</p>}</div></details></div>}
      </div>
      <section className={`teacher-actions action-dock ${actionsOpen ? "expanded" : "collapsed"}`}>
        {detail.requirements.some((item) => item.kind === "TEACHER_REVIEW") && <section className="detail-section"><h3>本阶段教师复核</h3><p>结合当前版本与资料填写意见，再确认结果。学生重新检查后继续下一阶段。</p>{detail.requirements.filter((item) => item.kind === "TEACHER_REVIEW").map((item) => <div key={item.id}><strong>{item.name} · {requirementStatus(item.status)}</strong><button className="refresh" disabled={reviewBusy || !detail.latest_snapshot} onClick={() => void reviewStage(item.key, "SATISFIED")}>确认通过</button><button className="refresh" disabled={reviewBusy || !detail.latest_snapshot} onClick={() => void reviewStage(item.key, "NOT_SATISFIED")}>需要修改</button></div>)}{stageReviewNotice && <p role="status">{stageReviewNotice}</p>}</section>}
        <button type="button" className="action-toggle" aria-expanded={actionsOpen} onClick={() => setActionsOpen((value) => !value)}><span><b>{active ? "处理这次求助" : "给学生下一步"}</b><small>{active ? "依据证据完成最小必要干预" : "发送一条具体、可执行的指导"}</small></span><span>{actionsOpen ? "收起" : "展开"}</span></button>
        <div className="action-body">
          {active && interventionBasis && <section className="action-basis" aria-label="本次介入依据"><div><span>本次介入依据</span><b>{interventionBasis.name}</b></div><p><strong>{requirementStatus(interventionBasis.status)}</strong><span>{versionLabel(interventionBasis.snapshot_label)}</span><small>{evaluatorLabel(interventionBasis.evaluator)}</small></p></section>}
          <section className="action-explanation"><div className="action-section-heading"><div><h3>给学生的下一步</h3><p>{active ? "依据当前证据选择最小必要干预。" : "写清当前问题和下一步检查方向，学生会在提示区收到。"}</p></div>{active && <span className="status-badge waiting">待处理</span>}</div><label htmlFor="teacher-prompt">{active ? "补充教学提示（可选）" : "指导内容"}</label><textarea id="teacher-prompt" rows={2} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="例如：先检查检索结果为空时的分支，再运行并检查。"/>{!active && <button className="primary" disabled={!!actionLoading || !prompt.trim()} onClick={() => takeAction("feedback")}>{actionLoading === "feedback" ? "发送中…" : "发送这条指导"}</button>}{actionError && <div className="action-error" role="alert">{actionError}</div>}</section>
          {active && <section className="action-group flow-control"><button className="primary" onClick={() => takeAction("resume")} disabled={!!actionLoading}>{actionLoading === "resume" ? "正在处理…" : prompt.trim() ? "发送提示并继续学习" : "确认继续学习"}</button></section>}
          {active && <details className="advanced-detail action-options"><summary>调整智能指导方式</summary><div className="action-grid strategy"><button onClick={() => takeAction("continue")} disabled={!!actionLoading}>{actionLoading === "continue" ? "处理中…" : "保持当前指导"}</button><button onClick={() => takeAction("allow_l2")} disabled={!!actionLoading}>{actionLoading === "allow_l2" ? "处理中…" : "允许局部示例"}</button><button onClick={() => takeAction("pause_ai")} disabled={!!actionLoading}>{actionLoading === "pause_ai" ? "处理中…" : "暂停智能指导"}</button></div></details>}
          {detail.latest_submission && <section className="action-group"><div className="action-section-heading"><div><h3>作品评价</h3><p>{detail.latest_submission.snapshot_label || "已提交"} · {detail.latest_submission.formal_grade ? "成绩已发布" : "待复核"}</p></div></div><button className="primary" onClick={() => onReview(detail.latest_submission!.id)}>打开评价页</button></section>}
        </div>
      </section>
    </div>
  </>;
}

function EmptyGroup({ group }: { group: GroupKey }) { return <div className="empty-group"><strong>{group === "attention" ? "当前没有待处理事项" : `当前没有${groupNames[group]}的学生`}</strong><p>列表只显示来自业务数据库的课堂记录。</p></div>; }
function EmptyDetail() { return <div className="empty-detail"><strong>选择一名学生</strong><p>查看验收项、证据、学生观察和教学过程。</p></div>; }
function DetailLoading() { return <div className="detail-loading"><i/><span>正在读取教学事实与证据…</span></div>; }
function Loading() { return <div className="page-loading"><div className="loading-brand"><span className="brand-mark">AI</span><strong>教师课堂台</strong></div><div className="skeleton"><i/><i/></div><p>正在从业务数据库读取课堂状态…</p></div>; }

function Setup() { return <AuthEntry />; }

function Fatal({ message, retry }: { message: string; retry: () => void }) { return <main className="setup-state"><span className="error-mark">!</span><h1>课堂台暂时无法载入</h1><p>{message}</p><button className="primary-retry" onClick={retry}>重新连接</button></main>; }


type HealthStatus = "normal" | "degraded" | "unavailable";
type DemoHealth = { business_database: HealthStatus; execution_environment: HealthStatus; teaching_flow: HealthStatus; model_service: HealthStatus };
const healthLabels: Record<keyof DemoHealth, string> = { business_database: "业务数据库", execution_environment: "执行环境", teaching_flow: "教学流程", model_service: "模型服务" };
const healthStatusLabels: Record<HealthStatus, string> = { normal: "正常", degraded: "降级", unavailable: "不可用" };

function DemoHealthView({ userId, theme, onToggle }: { userId: string; theme: "light" | "dark"; onToggle: () => void }) {
  const [health, setHealth] = useState<DemoHealth | null>(null);
  const [error, setError] = useState("");
  const loadHealth = useCallback(async () => {
    setError("");
    try { setHealth(await api<DemoHealth>("/api/product/teacher/demo-health", userId)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "健康状态读取失败"); }
  }, [userId]);
  useEffect(() => { void loadHealth(); }, [loadHealth]);
  return <div className="teacher-app">
    <header className="topbar"><div className="brand"><span className="brand-mark">AI</span><strong>演示健康</strong></div><div className="top-actions"><ThemeToggle theme={theme} onToggle={onToggle} /><a className="health-link" href={`?user=${encodeURIComponent(userId)}`}>返回课堂台</a></div></header>
    <main className="demo-health-page"><header><span>DEMO PREFLIGHT</span><h1>演示环境健康状态</h1><p>仅呈现可用性，不显示密钥、路径、容器或数据库凭据。</p></header>
      {error ? <section className="health-error"><strong>状态不可用</strong><p>{error}</p><button className="refresh" onClick={() => void loadHealth()}>重新检查</button></section> : !health ? <section className="health-loading">正在检查四个服务域…</section> : <section className="health-grid">{(Object.keys(healthLabels) as Array<keyof DemoHealth>).map((key) => <article key={key}><div><span className={`health-dot ${health[key]}`} aria-hidden="true"/><h2>{healthLabels[key]}</h2></div><b className={`health-state ${health[key]}`}>{healthStatusLabels[health[key]]}</b></article>)}</section>}
      <footer><strong>进入演示前仍须运行 CLI Preflight。</strong><span>存在 FAIL 时禁止进入演示状态。</span></footer>
    </main>
  </div>;
}
