import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import { ApiError, api, newOperation } from "./api";
import { SubmitViews } from "./SubmitViews";
import type { Evidence, FilePayload, Requirement, Workbench } from "./types";

type SaveState = "saved" | "dirty" | "saving" | "failed";
type RunState = "idle" | "queued" | "running" | "timeout" | "failed";

const helpLabel = (level?: string) => ({ L0: "引导", L1: "定位", L2: "局部示例" }[level || ""] || "引导");
const statusLabel = (status: string) => ({ SATISFIED: "已满足", NOT_SATISFIED: "未满足", INFRASTRUCTURE_ERROR: "环境异常", NOT_RUN: "未运行" }[status] || status);
const kindLabel = (kind: string) => ({ AUTO_TEST: "自动测试", STATIC_CHECK: "静态检查", STUDENT_EXPLANATION: "学习观察", TEACHER_REVIEW: "教师复核", TRANSFER_TASK: "迁移任务" }[kind] || kind.replaceAll("_", " "));
type EvidenceCheck = { code?: string; passed?: boolean };
const evidenceCheckLabels: Record<string, string> = {
  sources_loaded: "资料已成功加载",
  known_question_hit: "已知问题能够命中",
  unknown_question_no_fabrication: "未知问题不会伪造命中",
  basic_citation_present: "回答包含基础引用",
};
const evidenceReasonCopy: Record<string, { title: string; detail: string }> = {
  empty_retrieval: { title: "已知问题没有检索到资料", detail: "资料加载正常，但检索链路返回空结果。请从查询文本、匹配条件和结果过滤三个位置继续定位。" },
  sources_load_failed: { title: "资料未能正常加载", detail: "检查资料路径、文件格式和读取编码后，再创建新的 Snapshot 运行验收。" },
  missing_citation: { title: "回答缺少可核对的来源", detail: "回答内容已经生成，但没有提供满足任务要求的资料来源。" },
  unknown_question_fabrication: { title: "未知问题出现了无依据回答", detail: "边界问题没有可靠资料命中时，应明确返回无法回答，而不是生成猜测内容。" },
};
function parseEvidenceChecks(summary: string): EvidenceCheck[] {
  const marker = "__TEACHING_EVIDENCE__=";
  if (!summary.includes(marker)) return [];
  try {
    const payload = summary.slice(summary.indexOf(marker) + marker.length).trim();
    const parsed = JSON.parse(payload) as { checks?: EvidenceCheck[] };
    return Array.isArray(parsed.checks) ? parsed.checks : [];
  } catch { return []; }
}
const formatTime = (value: string) => new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
function splitGuidance(message: string) {
  const boundary = message.search(/[。！？]/);
  if (boundary < 0 || boundary >= message.length - 1) return { judgment: message, advice: "" };
  return { judgment: message.slice(0, boundary + 1), advice: message.slice(boundary + 1).trim() };
}

export function App() {
  const params = useMemo(() => new URLSearchParams(location.search), []);
  const userId = params.get("user") || params.get("user_id") || "";
  const attemptId = params.get("attempt") || params.get("attempt_id") || "";
  const [data, setData] = useState<Workbench | null>(null);
  const [loading, setLoading] = useState(true);
  const [fatal, setFatal] = useState("");
  const [activeFile, setActiveFile] = useState("faq_app.py");
  const [file, setFile] = useState<FilePayload | null>(null);
  const [content, setContent] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [runState, setRunState] = useState<RunState>("idle");
  const [runMessage, setRunMessage] = useState("");
  const [runSnapshot, setRunSnapshot] = useState<string | null>(null);
  const [oldSnapshotNotice, setOldSnapshotNotice] = useState(false);
  const [observation, setObservation] = useState("");
  const [bottomTab, setBottomTab] = useState<"result" | "requirements" | "preview">("requirements");
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [coachLoading, setCoachLoading] = useState(false);
  const [connection, setConnection] = useState<"connected" | "reconnecting" | "offline">("reconnecting");
  const [toast, setToast] = useState("");
  const [coachOpen, setCoachOpen] = useState(false);
  const [taskCollapsed, setTaskCollapsed] = useState(false);
  const [resultCollapsed, setResultCollapsed] = useState(false);
  const [view, setView] = useState<"workbench" | "submit" | "recap">(params.get("view") === "recap" ? "recap" : params.get("view") === "submit" ? "submit" : "workbench");
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null);
  const saveRef = useRef<() => Promise<string | false>>(async () => false);
  const stageRef = useRef<string | null>(null);
  const coachButtonRef = useRef<HTMLButtonElement | null>(null);
  const coachPanelRef = useRef<HTMLElement | null>(null);

  const load = useCallback(async () => {
    if (!userId || !attemptId) { setLoading(false); return; }
    try {
      const next = await api<Workbench>(`/api/product/attempts/${attemptId}/workbench`, userId);
      setData(next);
      const stageChanged = stageRef.current !== null && stageRef.current !== next.stage.id;
      const hasStageEvidence = next.requirements.some((item) => item.status !== "NOT_RUN");
      if (stageChanged || (stageRef.current === null && !hasStageEvidence)) {
        setObservation("");
        setRunMessage("");
        setRunSnapshot(null);
      } else if (hasStageEvidence) {
        setObservation((value) => value || next.student_observation);
      }
      stageRef.current = next.stage.id;
      setFatal("");
    } catch (error) {
      setFatal(error instanceof Error ? error.message : "工作台载入失败");
    } finally { setLoading(false); }
  }, [attemptId, userId]);

  const loadFile = useCallback(async (path: string) => {
    if (!userId || !attemptId) return;
    try {
      const next = await api<FilePayload>(`/api/product/attempts/${attemptId}/files/${encodeURIComponent(path).replaceAll("%2F", "/")}`, userId);
      setFile(next); setContent(next.content); setActiveFile(path); setSaveState("saved");
    } catch (error) { setToast(error instanceof Error ? error.message : "文件读取失败"); }
  }, [attemptId, userId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!data?.files.length) return;
    const target = data.files.some((item) => item.path === activeFile) ? activeFile : data.files[0].path;
    if (!file || file.path !== target) void loadFile(target);
  }, [data, activeFile, file, loadFile]);

  useEffect(() => {
    if (!userId || !attemptId) return;
    const controller = new AbortController();
    let streamCursor = data?.attempt.state_version ?? 0;
    let retry: number | undefined;
    async function connect() {
      try {
        setConnection("reconnecting");
        const response = await fetch(`/api/product/attempts/${attemptId}/stream?since_state_version=${streamCursor}`, { headers: { "X-User-Id": userId }, signal: controller.signal });
        if (!response.ok || !response.body) throw new Error("stream_failed");
        setConnection("connected");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split("\n\n"); buffer = frames.pop() || "";
          for (const frame of frames) { const id = frame.match(/^id: (\d+)$/m); if (id) streamCursor = Math.max(streamCursor, Number(id[1])); if (frame.includes("event: teaching_event")) void load(); }
        }
        throw new Error("stream_closed");
      } catch {
        if (controller.signal.aborted) return;
        setConnection(navigator.onLine ? "reconnecting" : "offline");
        retry = window.setTimeout(connect, 2000);
      }
    }
    void connect();
    const offline = () => setConnection("offline");
    const online = () => setConnection("reconnecting");
    addEventListener("offline", offline); addEventListener("online", online);
    return () => { controller.abort(); if (retry) clearTimeout(retry); removeEventListener("offline", offline); removeEventListener("online", online); };
  }, [attemptId, load, userId, data?.attempt.state_version]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4200);
    return () => clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    if (!coachOpen) return;
    const panel = coachPanelRef.current;
    const focusable = () => panel ? Array.from(panel.querySelectorAll<HTMLElement>('button:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex=\"-1\"])')) : [];
    requestAnimationFrame(() => focusable()[0]?.focus());
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setCoachOpen(false);
        requestAnimationFrame(() => coachButtonRef.current?.focus());
      } else if (event.key === "Tab" && matchMedia("(max-width: 1120px)").matches) {
        const items = focusable();
        if (!items.length) return;
        const first = items[0]; const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    addEventListener("keydown", close);
    return () => removeEventListener("keydown", close);
  }, [coachOpen]);

  const save = useCallback(async (): Promise<string | false> => {
    if (!file || saveState === "saving") return false;
    setSaveState("saving");
    try {
      const result = await api<{ hash: string }>(`/api/product/attempts/${attemptId}/files/${encodeURIComponent(file.path).replaceAll("%2F", "/")}`, userId, {
        method: "PUT", body: JSON.stringify({ content, expected_hash: file.hash }),
      });
      setFile({ ...file, content, hash: result.hash, size: new TextEncoder().encode(content).length });
      setSaveState("saved"); setToast("代码已保存"); return result.hash;
    } catch (error) {
      setSaveState("failed");
      setToast(error instanceof ApiError && error.code === "snapshot_conflict" ? "保存冲突：请重新载入文件" : "保存失败，代码仍保留在编辑器中");
      return false;
    }
  }, [attemptId, content, file, saveState, userId]);

  useEffect(() => { saveRef.current = save; }, [save]);

  const onMount: OnMount = (editor, monaco) => {
    editorRef.current = editor;
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => { void saveRef.current(); });
  };

  function exportDraft() {
    if (!file) return;
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${file.path.split("/").pop() || "student-code"}.local-copy`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function snapshot() {
    if (!file) return;
    let expectedHash = file.hash;
    if (saveState === "dirty" || saveState === "failed") {
      const savedHash = await save(); if (!savedHash) return;
      expectedHash = savedHash;
    }
    try {
      await api(`/api/product/attempts/${attemptId}/snapshots`, userId, {
        method: "POST", body: JSON.stringify({ operation_id: newOperation("ui-snapshot"), expected_file_hash: expectedHash }),
      });
      setToast("已创建新的版本快照"); await load();
    } catch (error) { setToast(error instanceof Error ? error.message : "Snapshot 创建失败"); }
  }

  async function run() {
    if (!data?.latest_snapshot) { setToast("请先保存代码并创建版本快照"); return; }
    if (saveState !== "saved") { setToast("当前有未保存修改，请先保存并创建新的版本快照"); return; }
    setRunSnapshot(data.latest_snapshot.label); setOldSnapshotNotice(false); setRunMessage("等待可用执行资源…"); setRunState("queued");
    await new Promise((resolve) => setTimeout(resolve, 220));
    setRunState("running"); setRunMessage("正在隔离工作区中运行 FAQ 验收…");
    try {
      const result = await api<{ last_tool_status: string; flow_status: string; guidance?: unknown }>(`/api/product/attempts/${attemptId}/runs`, userId, {
        method: "POST", body: JSON.stringify({ operation_id: newOperation("ui-run"), snapshot_id: data.latest_snapshot.id, expected_state_version: data.attempt.state_version, observation }),
      });
      setRunState("idle");
      setRunMessage(result.last_tool_status === "succeeded" ? "本次检查通过，验收结果已写入当前版本。" : result.last_tool_status === "infrastructure_failure" ? "运行环境异常，本次不计入学习失败次数。" : "检查完成：仍有验收项未满足，请查看证据。" );
      await load(); setBottomTab("requirements"); setResultCollapsed(false);
    } catch (error) {
      const apiError = error instanceof ApiError ? error : null;
      setRunState(apiError?.code === "timeout" ? "timeout" : "failed");
      setRunMessage(apiError?.code === "openhands_unavailable" ? "OpenHands 暂不可用，本次不计入学生错误。" : apiError?.message || "运行失败，请检查连接后重试。");
      await load();
    }
  }

  async function guidance() {
    if (!data?.latest_snapshot) { setToast("请先运行一次基于 Snapshot 的检查"); return; }
    setCoachLoading(true);
    try {
      await api(`/api/product/attempts/${attemptId}/guidance`, userId, { method: "POST", body: JSON.stringify({ operation_id: newOperation("ui-guidance"), snapshot_id: data.latest_snapshot.id, expected_state_version: data.attempt.state_version, observation }) });
      await load();
    } catch (error) {
      setToast(error instanceof ApiError && ["model_unavailable", "ai_guidance_paused"].includes(error.code) ? "智能指导暂不可用，你仍可以继续编辑和运行检查。" : error instanceof Error ? error.message : "指导请求失败");
    } finally { setCoachLoading(false); }
  }

  async function openEvidence(requirement: Requirement) {
    if (!requirement.operation_id) return;
    setEvidenceLoading(true);
    try { setEvidence(await api<Evidence>(`/api/product/attempts/${attemptId}/evidence/${encodeURIComponent(requirement.operation_id)}`, userId)); }
    catch (error) { setToast(error instanceof Error ? error.message : "证据读取失败"); }
    finally { setEvidenceLoading(false); }
  }

  if (!userId || !attemptId) return <Setup />;
  if (loading) return <Loading />;
  if (fatal || !data) return <Fatal message={fatal || "工作台不可用"} retry={() => { setLoading(true); void load(); }} />;

  const hasCurrentEvidence = data.requirements.some((item) => item.status !== "NOT_RUN");
  const guidanceData = hasCurrentEvidence ? data.guidance : null;
  const guidanceParts = guidanceData ? splitGuidance(guidanceData.message ?? "") : null;
  const waitingTeacher = data.intervention?.status === "WAITING_TEACHER";

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-mark" aria-hidden="true">AI</span><strong>实训教练</strong></div>
      <div className="crumb"><span>{data.course.name}</span><span aria-hidden="true">/</span><b>{data.task.title}</b></div>
      <div className="top-actions">
        <span className={`connection ${connection}`}><i />{connection === "connected" ? "已连接" : connection === "offline" ? "网络断开" : "正在重连"}</span>
        <span className={`save-state ${saveState}`} aria-live="polite">{({ saved: "已保存", dirty: "未保存", saving: "保存中…", failed: "保存失败，代码仍在编辑器中" } as const)[saveState]}</span>
        {saveState === "failed" && <button className="save-retry" onClick={() => void save()}>重试保存</button>}
        <button className="button quiet" onClick={() => setView(data.latest_submission ? "recap" : "submit")}>{data.latest_submission ? "查看提交" : "提交"}</button>
        <button ref={coachButtonRef} className="coach-toggle" aria-controls="student-coach" aria-expanded={coachOpen} onClick={() => setCoachOpen((value) => !value)}>实训教练</button>
        <span className="avatar" aria-label={`当前学生：${data.identity.display_name}`}>{data.identity.display_name.slice(0, 1)}</span>
      </div>
    </header>

    {view !== "workbench" && <SubmitViews userId={userId} attemptId={attemptId} mode={view} onBack={() => setView("workbench")} onSubmitted={() => { setView("recap"); void load(); }} />}
    {view === "workbench" && oldSnapshotNotice && <div className="snapshot-banner" role="status">本次检查基于 {runSnapshot}；当前编辑内容尚未包含在此次结果中。</div>}
    {view === "workbench" && waitingTeacher && <div className="teacher-banner" role="status"><strong>正在等待教师</strong><span>你的代码和证据已保留；教师处理前暂不能再次运行或请求指导。</span></div>}
    {view === "workbench" && saveState === "failed" && <div className="save-recovery" role="alert"><div><strong>代码尚未保存</strong><span>编辑器中的内容仍然保留。请重试；若冲突持续，可先导出本地副本。</span></div><div><button onClick={() => void save()}>重试保存</button><button onClick={exportDraft}>导出本地代码</button><button onClick={() => { if (confirm("重新读取将放弃编辑器中尚未保存的内容，是否继续？")) void loadFile(activeFile); }}>重新读取</button></div></div>}

    {view === "workbench" && <main className={`workspace-grid ${taskCollapsed ? "task-collapsed" : ""}`}>
      <aside className="task-pane" aria-label="任务与阶段">
        <div className="task-compact-rail"><button onClick={() => setTaskCollapsed(false)} aria-label="展开任务栏">展开任务</button><b>{data.stage.position + 1}/{data.stage.total}</b><span>{data.stage.title}</span></div>
        <section className="task-head"><div className="task-head-row"><span className="task-id">{data.task.key} · {data.task.version}</span><button className="task-collapse" onClick={() => setTaskCollapsed(true)}>收起任务</button></div><h1>{data.task.title}</h1><p>{data.stage.objective}</p></section>
        <section className="pane-section stage-section"><div className="section-row"><h2>任务进度</h2><span>{data.stage.position + 1}/{data.stage.total}</span></div><div className="stage-progress" role="progressbar" aria-label="任务阶段进度" aria-valuemin={1} aria-valuemax={data.stage.total} aria-valuenow={data.stage.position + 1}><i style={{ transform: `scaleX(${(data.stage.position + 1) / data.stage.total})` }} /></div><ol className="stage-list">{data.stages.map((stage) => <li key={stage.key} className={stage.status}><span className="stage-index">{stage.status === "complete" ? "✓" : stage.position + 1}</span><span>{stage.title}</span>{stage.status === "current" && <b>当前</b>}</li>)}</ol></section>
        <section className="pane-section requirement-compact"><div className="section-row"><h2>本阶段验收</h2><span>{data.requirement_summary.satisfied_count}/{data.requirement_summary.required_count}</span></div>{data.requirements.map((item) => <button key={item.id} disabled={!item.operation_id} onClick={() => void openEvidence(item)} className={`compact-requirement ${item.status.toLowerCase()}`}><span aria-hidden="true">{item.status === "SATISFIED" ? "✓" : item.status === "NOT_SATISFIED" ? "×" : "·"}</span><span>{item.name}</span>{item.has_old_result && <small>有旧结果</small>}</button>)}</section>
      </aside>

      <section className={`code-pane ${resultCollapsed ? "result-collapsed" : ""}`} aria-label="Workspace 代码区">
        <div className="workspace-toolbar">
          <div className="file-context"><span className="workspace-label">Workspace</span><strong>{activeFile}</strong></div>
          <div className="version-context" aria-label="当前版本状态"><span>已检查版本</span><b>{data.latest_snapshot?.label || "尚未创建"}</b></div>
          <div className="toolbar-actions"><button className="button quiet" onClick={() => void save()} disabled={saveState === "saving" || saveState === "saved"}>保存 <kbd>Ctrl S</kbd></button><button className="button secondary" onClick={() => void snapshot()} disabled={saveState === "saving"}>创建 Snapshot</button><button className={`button primary ${runState === "queued" || runState === "running" ? "is-running" : ""}`} onClick={() => void run()} disabled={runState === "queued" || runState === "running" || waitingTeacher}>{runState === "queued" ? "运行排队" : runState === "running" ? "运行中…" : "运行检查"}</button></div>
          <p className="mobile-ide-note">手机适合查看任务、反馈与求助；完整编码建议使用电脑。</p>
        </div>
        <div className="editor-zone">
          <nav className="file-tree" aria-label="文件列表">{data.files.map((item) => <button key={item.path} className={item.path === activeFile ? "active" : ""} onClick={() => { if (saveState === "dirty" && !confirm("当前文件有未保存修改，仍要切换吗？")) return; void loadFile(item.path); }}><span className="file-ext">{item.name.split(".").pop()?.toUpperCase()}</span><span>{item.path}</span></button>)}</nav>
          <div className="editor-frame" aria-label={`${activeFile} 代码编辑器`}>
            <div className="editor-tab"><span>{activeFile}</span>{saveState === "dirty" && <i aria-label="未保存" />}</div>
            <Editor height="100%" language={activeFile.endsWith(".py") ? "python" : activeFile.endsWith(".json") ? "json" : "markdown"} value={content} onMount={onMount} onChange={(value) => { setContent(value || ""); setSaveState("dirty"); if (runState === "running") setOldSnapshotNotice(true); }} loading={<EditorLoading />} options={{ minimap: { enabled: false }, fontSize: 14, fontFamily: "Cascadia Code, Consolas, monospace", lineHeight: 22, renderLineHighlight: "line", padding: { top: 12 }, scrollBeyondLastLine: false, wordWrap: "off", automaticLayout: true, tabSize: 4, ariaLabel: `${activeFile} 代码编辑器` }} />
          </div>
        </div>
        <div className="result-panel">
          <div className="result-head"><div className="tabs" role="tablist" aria-label="运行与验收"><button role="tab" aria-selected={bottomTab === "result"} onClick={() => { setBottomTab("result"); setResultCollapsed(false); }}>运行结果</button><button role="tab" aria-selected={bottomTab === "requirements"} onClick={() => { setBottomTab("requirements"); setResultCollapsed(false); }}>验收 <span>{data.requirement_summary.satisfied_count}/{data.requirement_summary.required_count}</span></button><button role="tab" aria-selected={bottomTab === "preview"} onClick={() => { setBottomTab("preview"); setResultCollapsed(false); }}>成果预览</button></div><button className="result-toggle" aria-expanded={!resultCollapsed} onClick={() => setResultCollapsed((value) => !value)}>{resultCollapsed ? "展开结果" : "收起结果"}</button></div>
          {bottomTab === "result" && <div className="result-content" role="tabpanel"><RunStateView state={runState} message={runMessage} snapshot={runSnapshot || data.latest_snapshot?.label || "尚未创建版本快照"} /></div>}
          {bottomTab === "requirements" && <div className="requirement-checklist" role="tabpanel"><div className="checklist-summary"><div><strong>当前阶段验收</strong><span>{data.requirement_summary.satisfied ? "全部要求已满足" : `${data.requirement_summary.required_count - data.requirement_summary.satisfied_count} 项仍需完成`}</span></div><b>{data.requirement_summary.satisfied_count}<small> / {data.requirement_summary.required_count}</small></b></div><div className="checklist-items">{data.requirements.map((item) => <button className={`checklist-item ${item.status.toLowerCase()}`} key={item.id} disabled={!item.operation_id} onClick={() => void openEvidence(item)}><span className="check-state" aria-hidden="true">{item.status === "SATISFIED" ? "✓" : item.status === "NOT_SATISFIED" ? "×" : item.status === "INFRASTRUCTURE_ERROR" ? "!" : "·"}</span><span className="check-copy"><b>{item.name}</b><small>{kindLabel(item.kind)} · {item.evaluator}</small></span><span className="check-result"><b>{statusLabel(item.status)}</b><small>{item.snapshot_label || "尚未绑定 Snapshot"}{item.evaluated_at ? ` · ${formatTime(item.evaluated_at)}` : ""}</small>{item.has_old_result && <em>旧 Snapshot 仅供追溯</em>}</span></button>)}</div></div>}
          {bottomTab === "preview" && <div className="preview-empty" role="tabpanel"><strong>FAQ 服务成果预览</strong><p>当前阶段通过后，这里将展示基于学生代码运行的问答结果。预览不会替代验收。</p><dl><div><dt>当前代码</dt><dd>{data.latest_snapshot?.label || "尚未创建版本快照"}</dd></div><div><dt>阶段状态</dt><dd>{data.requirement_summary.satisfied ? "已满足" : `${data.requirement_summary.satisfied_count} / ${data.requirement_summary.required_count} 已满足`}</dd></div></dl></div>}
        </div>
      </section>

      {coachOpen && <button className="coach-backdrop" aria-label="关闭实训教练" onClick={() => { setCoachOpen(false); coachButtonRef.current?.focus(); }} />}
      <aside ref={coachPanelRef} id="student-coach" className={`coach-pane ${coachOpen ? "open" : ""}`} role={coachOpen ? "dialog" : undefined} aria-modal={coachOpen ? "true" : undefined} aria-labelledby="coach-title">
        <div className="coach-head"><div><h2 id="coach-title">实训教练</h2><p>基于当前版本快照与验收证据</p></div><div className="coach-head-actions">{guidanceData?.level && <span className={`help-level ${guidanceData.level.toLowerCase()}`}>{helpLabel(guidanceData.level)}</span>}<button className="coach-close" onClick={() => { setCoachOpen(false); coachButtonRef.current?.focus(); }} aria-label="关闭实训教练">关闭</button></div></div>
        <CoachSection label="当前观察"><p>{runState === "running" ? `正在检查 ${runSnapshot}…` : runMessage || (data.requirement_summary.satisfied ? "当前阶段验收项已满足。" : "运行检查后，教练会根据真实证据给出下一步。")}</p></CoachSection>
        <CoachSection label="证据"><div className="evidence-summary">{data.requirements.filter((item) => item.status !== "NOT_RUN").slice(0, 3).map((item) => <button key={item.id} onClick={() => void openEvidence(item)}><span className={item.status === "SATISFIED" ? "ok" : "bad"}>{item.status === "SATISFIED" ? "通过" : "未通过"}</span><span>{item.name}</span><small>{item.snapshot_label}</small></button>)}{!data.requirements.some((item) => item.status !== "NOT_RUN") && <p className="muted">尚无当前版本的验收证据。</p>}</div></CoachSection>
        <CoachSection label="本轮指导">{coachLoading ? <div className="coach-generating"><i /><span>正在根据本次运行证据生成指导…</span><small>你仍可以继续编辑代码</small></div> : guidanceData && guidanceParts ? <div className="guidance guidance-structured"><div className="guidance-block judgment"><span>当前判断</span><p>{guidanceParts.judgment}</p></div>{guidanceParts.advice && <div className="guidance-block advice"><span>检查方向 / 建议</span><p>{guidanceParts.advice}</p></div>}{guidanceData.success === false && <div className="fallback-note">智能指导暂不可用，已显示安全模板。你仍可以继续编辑和运行检查。</div>}</div> : <p className="muted">完成一次运行检查后可请求指导。</p>}</CoachSection>
        <CoachSection label="学生观察"><label className="sr-only" htmlFor="observation">记录你观察到的现象</label><textarea id="observation" value={observation} onChange={(event) => setObservation(event.target.value)} placeholder="例如：资料已成功加载，但已知问题的检索结果仍为空；未知问题返回空结果。" rows={5} maxLength={1200}/><div className="textarea-meta"><span>{observation.length}/1200</span><span>观察会随下次运行写入当前版本的验收记录</span></div></CoachSection>
        <CoachSection label="下一步行动"><p>{guidanceData?.next_step || "保存修改，创建新的版本快照，再运行验收。"}</p><button className="button coach-action" onClick={() => void guidance()} disabled={coachLoading || waitingTeacher || data.attempt.ai_guidance_paused}>{data.attempt.ai_guidance_paused ? "教师已暂停智能指导" : "请求本轮指导"}</button></CoachSection>
      </aside>
    </main>}

    {view === "workbench" && (evidence || evidenceLoading) && <EvidenceDrawer evidence={evidence} loading={evidenceLoading} onClose={() => setEvidence(null)} />}
    {toast && <div className="toast" role="status">{toast}</div>}
  </div>;
}

function CoachSection({ label, children }: { label: string; children: React.ReactNode }) { return <section className="coach-section"><h3>{label}</h3>{children}</section>; }

function RunStateView({ state, message, snapshot }: { state: RunState; message: string; snapshot: string }) {
  const title = ({ idle: message ? "检查已完成" : "等待运行", queued: "运行排队", running: "正在运行", timeout: "运行超时", failed: "运行失败" } as const)[state];
  return <div className={`run-state ${state}`}><div className="run-indicator" aria-hidden="true">{state === "running" || state === "queued" ? <i /> : state === "idle" && message ? "✓" : state === "idle" ? "·" : "!"}</div><div><strong>{title}</strong><p>{message || "保存代码并创建版本快照 后运行验收。"}</p><span>绑定版本：{snapshot}</span></div></div>;
}

function EvidenceDrawer({ evidence, loading, onClose }: { evidence: Evidence | null; loading: boolean; onClose: () => void }) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    previousFocus.current = document.activeElement as HTMLElement | null;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab" && dialogRef.current) {
        const items = Array.from(dialogRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), summary, [href], [tabindex]:not([tabindex=\"-1\"])'));
        if (!items.length) return;
        const first = items[0]; const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    addEventListener("keydown", close);
    return () => { removeEventListener("keydown", close); requestAnimationFrame(() => previousFocus.current?.focus()); };
  }, [onClose]);
  const checks = evidence ? parseEvidenceChecks(evidence.stdout_summary) : [];
  const passed = evidence?.status === "SATISFIED";
  const reason = evidence ? evidenceReasonCopy[evidence.reason_code] || { title: passed ? "本项验收已经通过" : "检查结果未满足验收要求", detail: passed ? "当前 Snapshot 已通过本项自动验收。" : "查看下方证据摘要和技术追溯信息，确定下一步修改范围。" } : null;
  return <div className="drawer-layer"><button className="drawer-backdrop" aria-label="关闭证据详情" onClick={onClose}/><aside ref={dialogRef} className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-title"><header><div><h2 id="evidence-title">验收证据</h2><p>{evidence?.requirement || "正在读取教学事实"}</p></div><button className="icon-close" onClick={onClose} autoFocus aria-label="关闭">×</button></header>{loading || !evidence || !reason ? <div className="drawer-loading"><i/><span>正在读取有界证据…</span></div> : <div className="drawer-body">
    <section className={`evidence-conclusion ${passed ? "passed" : "failed"}`}><span className="conclusion-mark" aria-hidden="true">{passed ? "✓" : "×"}</span><div className="conclusion-copy"><span>教学结论</span><h3>{passed ? "当前要求已满足" : "当前要求尚未满足"}</h3><div className="conclusion-reason"><b>{passed ? "通过依据" : "失败原因"}</b><p>{reason.title}</p></div></div><b className="conclusion-snapshot">{evidence.snapshot}</b></section>
    <section className="evidence-explanation"><h3>为什么得到这个结论</h3><p>{reason.detail}</p>{checks.length > 0 && <div className="evidence-checks">{checks.map((check, index) => <div key={`${check.code}-${index}`} className={check.passed ? "passed" : "failed"}><span aria-hidden="true">{check.passed ? "✓" : "×"}</span><div><b>{evidenceCheckLabels[check.code || ""] || check.code || "验收检查"}</b><small>{check.passed ? "检查通过" : "需要继续处理"}</small></div></div>)}</div>}</section>
    <section className="evidence-trace"><div className="trace-heading"><h3>技术追溯</h3><span>供教师与评审复核</span></div><dl><div><dt>版本快照</dt><dd>{evidence.snapshot}</dd></div><div><dt>检查工具</dt><dd className="mono">{evidence.tool}</dd></div><div><dt>原因代码</dt><dd className="mono">{evidence.reason_code}</dd></div><div><dt>时间</dt><dd>{new Date(evidence.observed_at).toLocaleString("zh-CN")}</dd></div></dl><details className="trace-identifiers"><summary>操作与运行产物</summary><div><span>Operation</span><code>{evidence.operation}</code></div>{evidence.artifacts.map((artifact) => <div key={artifact.ref}><span>{artifact.kind}</span><code>{artifact.ref}</code><b>{artifact.available ? "可追溯" : "记录缺失"}</b></div>)}</details></section>
    <details className="raw-evidence"><summary>查看原始结构化输出</summary><pre>{evidence.stdout_summary || "本次工具未产生可展示的标准输出。"}</pre>{evidence.stdout_truncated && <p className="bounded-note">输出已按安全上限截断。</p>}</details>
    <footer>已隐藏宿主路径、容器令牌、模型密钥和内部数据库标识。</footer>
  </div>}</aside></div>;
}

function EditorLoading() { return <div className="editor-loading"><i/><span>正在启动 Workspace 编辑器…</span></div>; }
function Loading() { return <div className="page-loading"><div className="loading-brand"><span className="brand-mark">AI</span><strong>实训教练</strong></div><div className="skeleton-layout"><i/><i/><i/></div><p>正在连接 Workspace 和教学状态…</p></div>; }

type DemoLoginResult = { role: "student" | "teacher"; display_name: string; user_id: string; attempt_id: string | null };

function Setup() {
  useEffect(() => { document.title = "AI 实训教练 · 登录"; }, []);
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function signIn(event: React.FormEvent) {
    event.preventDefault(); setSubmitting(true); setError("");
    try {
      const response = await fetch("/api/product/auth/demo-login", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ account, password }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = body.detail; throw new Error(typeof detail === "object" ? detail.message : detail || "登录失败");
      }
      const result = body as DemoLoginResult;
      const base = result.role === "student" ? "http://127.0.0.1:5173/" : "http://127.0.0.1:5174/";
      const target = new URL(base);
      target.searchParams.set("user", result.user_id);
      if (result.attempt_id) target.searchParams.set("attempt", result.attempt_id);
      location.assign(target.toString());
    } catch (cause) { setError(cause instanceof Error ? cause.message : "登录失败，请检查服务状态。"); }
    finally { setSubmitting(false); }
  }

  return <main className="login-page">
    <section className="login-shell" aria-labelledby="login-title">
      <div className="login-context">
        <div className="login-brand"><span className="brand-mark">AI</span><strong>实训教练</strong></div>
        <p className="login-eyebrow">FAQ-001 · 演示教学空间</p>
        <h1 id="login-title">进入你的实训课堂</h1>
        <p>学生完成代码实训与证据验收，教师处理介入、复核作品并发布评价。</p>
        <dl><div><dt>学生</dt><dd>编辑、Snapshot、运行检查、获取分级指导</dd></div><div><dt>教师</dt><dd>查看课堂状态、处理介入、评价学习证据</dd></div></dl>
      </div>
      <form className="login-form" onSubmit={signIn}>
        <header><span>DEMO / TEST</span><h2>账号登录</h2><p>使用演示账号进入对应工作台</p></header>
        <label><span>账号</span><input autoFocus autoComplete="username" value={account} onChange={(event) => setAccount(event.target.value)} placeholder="请输入演示账号" required /></label>
        <label><span>密码</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="请输入演示密码" required /></label>
        {error && <div className="login-error" role="alert"><b>!</b><span>{error}</span></div>}
        <button className="login-submit" type="submit" disabled={submitting}>{submitting ? "正在验证…" : "进入教学空间"}</button>
        <div className="login-accounts"><span>演示账号</span><button type="button" onClick={() => setAccount("demo_student")}>学生 demo_student</button><button type="button" onClick={() => setAccount("demo_teacher")}>教师 demo_teacher</button></div>
        <footer>仅限本机 DEMO / TEST 环境 · 身份将由服务端验证</footer>
      </form>
    </section>
  </main>;
}

function Fatal({ message, retry }: { message: string; retry: () => void }) { return <main className="setup-state error"><span className="error-mark">!</span><h1>工作台暂时无法载入</h1><p>{message}</p><button className="button primary" onClick={retry}>重新连接</button></main>; }
