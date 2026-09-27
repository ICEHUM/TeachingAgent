import { AuthEntry } from "../../shared/AuthEntry";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import type { editor as MonacoEditor } from "monaco-editor";
import { ThemeToggle, useTheme } from "../../shared/theme";
import { ApiError, api, newOperation } from "./api";
import { SubmitViews } from "./SubmitViews";
import type { ConsoleRun, Evidence, FilePayload, Requirement, SelfServiceTool, TerminalSession, Workbench } from "./types";

type SaveState = "saved" | "dirty" | "saving" | "failed";
type RunState = "idle" | "queued" | "running" | "timeout" | "failed";
type ConsoleState = "idle" | "running" | "failed";
type LocateTarget = { file: string; line: number };
const consoleStatusCopy: Record<string, { title: string; detail: string }> = {
  succeeded: { title: "程序正常结束", detail: "退出码为 0。下面是你这次运行的真实输出。" },
  student_failure: { title: "程序以非零退出码结束", detail: "这是你自己程序的输出和报错，不计入验收失败次数。" },
  infrastructure_failure: { title: "运行环境异常", detail: "本次不计入学习失败次数，可以稍后重试。" },
};

const helpLabel = (level?: string) => ({ L0: "引导", L1: "定位", L2: "局部示例" }[level || ""] || "引导");
const statusLabel = (status: string) => ({ SATISFIED: "已满足", NOT_SATISFIED: "未满足", INFRASTRUCTURE_ERROR: "环境异常", NOT_RUN: "未运行" }[status] || status);
const kindLabel = (kind: string) => ({ AUTO_TEST: "自动测试", STATIC_CHECK: "静态检查", STUDENT_EXPLANATION: "学习观察", TEACHER_REVIEW: "教师复核", TRANSFER_TASK: "迁移任务" }[kind] || kind.replaceAll("_", " "));
type EvidenceCheck = { code?: string; passed?: boolean; detail?: string; diagnosis_code?: string };
type Assignment = { attempt_id: string; task_key: string; task_title: string; task_version: string; course_name: string; status: string };
const diagnosisLabels: Record<string, string> = {
  indentation_error: "缩进错误", syntax_error: "语法错误", undefined_name: "变量名未定义",
  input_conversion_error: "输入转换失败", type_error: "数据类型错误", runtime_error: "运行时错误",
  missing_output: "没有输出", string_concatenation: "两个输入被当作文本拼接",
  boundary_condition: "60 分边界判断不符", empty_list_output: "空列表时输出不符",
  output_mismatch: "输出与预期不符", output_truncated: "输出超过限制",
  missing_if_else: "缺少 if/else 分支", missing_for_traversal: "未用 for 遍历 numbers",
};
const evidenceCheckLabels: Record<string, string> = {
  sources_loaded: "资料已成功加载",
  known_question_hit: "已知问题能够命中",
  unknown_question_no_fabrication: "未知问题不会伪造命中",
  basic_citation_present: "回答包含基础引用",
  authoritative_citation_present: "回答保留权威来源",
  local_rule_scope_preserved: "地方规则适用范围正确",
  answer_function_present: "已经定义 answer(question, sources)",
  answer_uses_retrieved_content: "回答正文来自命中资料",
  answer_citations_present: "引用包含标题、链接和发布机关",
  answer_scope_present: "回答保留资料适用范围",
  runtime_error: "程序运行时发生错误",
  structure_check: "练习要求的语法结构",
};
type EvidenceReason = { title: string; detail: string; next: string };
const evidenceReasonCopy: Record<string, EvidenceReason> = {
  empty_retrieval: { title: "已知问题没有检索到资料", detail: "资料加载正常，但检索函数没有找到对应资料。", next: "检查问题文本是否包含资料 keywords 中的具体词，例如“密码”或“实训室”。" },
  sources_load_failed: { title: "资料未能正常加载", detail: "程序没有成功读取任务提供的 FAQ 资料。", next: "检查资料路径、JSON 格式和 UTF-8 编码，保存后重新试运行。" },
  missing_citation: { title: "回答缺少可核对的来源", detail: "回答内容已经生成，但没有保留任务要求的来源信息。", next: "返回命中资料时，同时保留 source、authority 和 scope 字段。" },
  unknown_question_fabrication: { title: "资料外问题被错误匹配", detail: "月球基地问题不在资料中，却返回了已有校园资料。", next: "不要只匹配“如何”“请问”等宽泛词，优先使用“密码”“实训室”“提交”等主题词。" },
  unknown_question_no_fabrication: { title: "资料外问题被错误匹配", detail: "月球基地问题不在资料中，却返回了已有校园资料。通常是宽泛关键词造成的误命中。", next: "排除“如何”“请问”等宽泛关键词，再确认月球基地问题返回空列表。" },
  runtime_error: { title: "程序运行时发生错误", detail: "代码在执行过程中停止，暂时无法完成验收。", next: "查看下方文件、行号和报错信息，修复后先试运行代码。" },
  answer_function_present: { title: "还没有找到 answer() 函数", detail: "当前阶段需要在 faq_app.py 中新增 answer(question, sources)。", next: "保留已经通过的 retrieve()，在它下面新增 answer()，并先调用 retrieve(question, sources)。" },
  answer_uses_retrieved_content: { title: "回答正文没有使用命中资料", detail: "answer() 已经运行，但返回字典中的 answer 与命中资料不一致。", next: "取 hits[0] 作为 first，让返回值的 answer 使用 first['answer']。" },
  answer_citations_present: { title: "回答的来源信息不完整", detail: "citations[0] 需要同时包含来源标题、链接和发布机关。", next: "分别使用 first['source_title']、first['source'] 和 first['authority']。" },
  answer_scope_present: { title: "回答缺少适用范围", detail: "返回结果没有保留命中资料的 scope。", next: "让返回值中的 scope 使用 first['scope']。" },
};
const EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
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
const versionLabel = (value?: string | null) => value ? value.replace(/^Snapshot\s+/i, "版本 ") : "尚未创建";
const guidanceFailureMessage = (reason?: string | null) => ({
  forbidden_answer: "模型回答超出当前帮助范围，本次未生成建议。",
  assessment_policy_violation: "模型回答不符合考核规则，本次未生成建议。",
  invalid_evidence_ref: "模型引用了无法核对的证据，本次未生成建议。",
  invalid_structure: "模型回复格式异常，本次未生成建议。",
  timeout: "模型响应超时，本次未生成建议。",
  api_unavailable: "模型服务暂不可用，本次未生成建议。",
  api_error: "模型服务返回异常，本次未生成建议。",
}[reason || ""] || "本次未生成新的 AI 建议。你仍可以编辑代码和运行检查。");
function splitGuidance(message: string) {
  const boundary = message.search(/[。！？]/);
  if (boundary < 0 || boundary >= message.length - 1) return { judgment: message, advice: "" };
  return { judgment: message.slice(0, boundary + 1), advice: message.slice(boundary + 1).trim() };
}

export function App() {
  const { theme, toggleTheme } = useTheme();
  const params = useMemo(() => new URLSearchParams(location.search), []);
  const userId = params.get("user") || params.get("user_id") || "";
  const attemptId = params.get("attempt") || params.get("attempt_id") || "";
  const [data, setData] = useState<Workbench | null>(null);
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [preparing, setPreparing] = useState(false);
  const [helpBusy, setHelpBusy] = useState(false);
  const actionLock = useRef(false);
  const contentRef = useRef("");
  const draftKey = (path: string) => `teaching-draft:${userId}:${attemptId}:${path}`;
  function storeDraft(path: string, value: string | null) {
    try { if (value === null) localStorage.removeItem(draftKey(path)); else localStorage.setItem(draftKey(path), value); } catch { /* Server save remains available if storage is full. */ }
  }
  const [fatal, setFatal] = useState("");
  const [activeFile, setActiveFile] = useState("main.py");
  const [file, setFile] = useState<FilePayload | null>(null);
  const [content, setContent] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [runState, setRunState] = useState<RunState>("idle");
  const [runOutcome, setRunOutcome] = useState<string | null>(null);
  const [runMessage, setRunMessage] = useState("");
  const [runSnapshot, setRunSnapshot] = useState<string | null>(null);
  const [oldSnapshotNotice, setOldSnapshotNotice] = useState(false);
  const [observation, setObservation] = useState("");
  const [observationExpanded, setObservationExpanded] = useState(false);
  const [bottomTab, setBottomTab] = useState<"result" | "console" | "debug" | "requirements">("result");
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [runEvidence, setRunEvidence] = useState<Evidence | null>(null);
  const [consoleRun, setConsoleRun] = useState<ConsoleRun | null>(null);
  const [consoleState, setConsoleState] = useState<ConsoleState>("idle");
  const [consoleMessage, setConsoleMessage] = useState("");
  const [terminalSession, setTerminalSession] = useState<TerminalSession | null>(null);
  const [terminalInput, setTerminalInput] = useState("");
  const [terminalBusy, setTerminalBusy] = useState(false);
  const [terminalSending, setTerminalSending] = useState(false);
  const [terminalError, setTerminalError] = useState("");
  const [debugRun, setDebugRun] = useState<ConsoleRun | null>(null);
  const [debugState, setDebugState] = useState<ConsoleState>("idle");
  const [debugMessage, setDebugMessage] = useState("");
  const [debugIndex, setDebugIndex] = useState(0);
  const [breakpoints, setBreakpoints] = useState<Record<string, number[]>>({});
  const [locateTarget, setLocateTarget] = useState<LocateTarget | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [coachLoading, setCoachLoading] = useState(false);
  const [coachFailure, setCoachFailure] = useState("");
  const [coachRepeat, setCoachRepeat] = useState(false);
  useEffect(() => setCoachRepeat(false), [content, observation, activeFile]);
  const [connection, setConnection] = useState<"connected" | "reconnecting" | "offline">("reconnecting");
  const [toast, setToast] = useState("");
  const [coachOpen, setCoachOpen] = useState(false);
  const [taskCollapsed, setTaskCollapsed] = useState(false);
  const [resultCollapsed, setResultCollapsed] = useState(true);
  const [resultHeight, setResultHeight] = useState(() => {
    const stored = Number(localStorage.getItem("student-result-height"));
    return Number.isFinite(stored) && stored >= 160 ? stored : 248;
  });
  const [, setNeedsSnapshot] = useState(false);
  const [fileAction, setFileAction] = useState<"create" | "rename" | "delete" | null>(null);
  const [filePathInput, setFilePathInput] = useState("");
  const [view, setView] = useState<"workbench" | "submit" | "recap">(params.get("view") === "recap" ? "recap" : params.get("view") === "submit" ? "submit" : "workbench");
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null);
  const breakpointDecorations = useRef<MonacoEditor.IEditorDecorationsCollection | null>(null);
  const debugLineDecorations = useRef<MonacoEditor.IEditorDecorationsCollection | null>(null);
  const activeFileRef = useRef(activeFile);
  const saveRef = useRef<() => Promise<string | false>>(async () => false);
  const stageRef = useRef<string | null>(null);
  const coachButtonRef = useRef<HTMLButtonElement | null>(null);
  const coachPanelRef = useRef<HTMLElement | null>(null);
  const terminalInputRef = useRef<HTMLInputElement | null>(null);
  const runEvidenceRequirement = data?.requirements.filter((item) =>
    item.snapshot_id === data.latest_snapshot?.id
    && item.operation_id
    && item.kind !== "STUDENT_EXPLANATION"
  ).find((item) => ["NOT_SATISFIED", "INFRASTRUCTURE_ERROR"].includes(item.status))
    ?? data?.requirements.find((item) => item.snapshot_id === data.latest_snapshot?.id && item.operation_id && item.kind !== "STUDENT_EXPLANATION");

  const load = useCallback(async () => {
    if (!userId || !attemptId) { setLoading(false); return; }
    try {
      const next = await api<Workbench>(`/api/product/attempts/${attemptId}/workbench`, userId);
      setData(next);
      if (stageRef.current === null) {
        if (next.student_observation) setObservationExpanded(true);
        const hasCurrentChecks = next.requirements.some((item) => item.status !== "NOT_RUN" && item.snapshot_id === next.latest_snapshot?.id);
        const pythonTask = next.task.key.startsWith("PYB-");
        setResultCollapsed(pythonTask ? false : !hasCurrentChecks);
        if (pythonTask) setBottomTab("console");
        else if (hasCurrentChecks) setBottomTab("requirements");
      }
      const stageChanged = stageRef.current !== null && stageRef.current !== next.stage.id;
      if (stageChanged) {
        setObservation("");
        setRunMessage((value) => value && !value.startsWith("上一阶段") ? `上一阶段：${value}` : value);
      } else {
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
      let restored: string | null = null;
      try { restored = localStorage.getItem(draftKey(path)); } catch { /* Private browsing. */ }
      const value = restored ?? next.content;
      setFile(next); setContent(value); contentRef.current = value; setActiveFile(path);
      setSaveState(value === next.content ? "saved" : "dirty");
      if (restored !== null && restored !== next.content) setToast("已恢复本机未保存草稿，请检查后保存。");
    } catch (error) { setToast(error instanceof Error ? error.message : "文件读取失败"); }
  }, [attemptId, userId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!userId || !attemptId || !data?.task.key.startsWith("PYB-")) return;
    let active = true;
    void api<{ session: TerminalSession | null }>(`/api/product/attempts/${attemptId}/terminal-sessions/active`, userId)
      .then(({ session }) => { if (active && session) setTerminalSession(session); })
      .catch(() => {});
    return () => { active = false; };
  }, [attemptId, data?.task.key, userId]);
  useEffect(() => {
    const sessionId = terminalSession?.session_id;
    if (!sessionId || !["running", "stopping"].includes(terminalSession.status) || !userId || !attemptId) return;
    let active = true;
    let polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const next = await api<TerminalSession>(`/api/product/attempts/${attemptId}/terminal-sessions/${sessionId}`, userId);
        if (active) setTerminalSession((current) => current?.session_id === sessionId && next.events.length >= current.events.length ? next : current);
      } catch (error) {
        if (active) setTerminalError(error instanceof Error ? error.message : "终端连接中断，请重新试运行。");
      } finally { polling = false; }
    };
    const timer = window.setInterval(() => { void poll(); }, 350);
    void poll();
    return () => { active = false; window.clearInterval(timer); };
  }, [attemptId, terminalSession?.session_id, terminalSession?.status, userId]);
  useEffect(() => {
    if (terminalSession?.status === "running") terminalInputRef.current?.focus();
  }, [terminalSession?.session_id, terminalSession?.status]);
  useEffect(() => {
    if (!userId) return;
    void api<{ assignments: Assignment[] }>("/api/product/student/assignments", userId)
      .then((result) => setAssignments(result.assignments))
      .catch(() => setAssignments([]));
  }, [userId]);
  useEffect(() => {
    const operationId = runEvidenceRequirement?.operation_id;
    if (!operationId || !userId || !attemptId) { setRunEvidence(null); return; }
    let active = true;
    void api<Evidence>(`/api/product/attempts/${attemptId}/evidence/${encodeURIComponent(operationId)}`, userId)
      .then((item) => { if (active) setRunEvidence(item); })
      .catch(() => { if (active) setRunEvidence(null); });
    return () => { active = false; };
  }, [attemptId, runEvidenceRequirement?.operation_id, userId]);
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
      if (result.hash !== file.hash) setNeedsSnapshot(true);
      const unchanged = contentRef.current === content;
      setSaveState(unchanged ? "saved" : "dirty");
      if (unchanged) storeDraft(file.path, null);
      setToast(unchanged ? "代码已保存" : "已保存上一刻的代码，新修改仍保留在草稿中");
      return unchanged ? result.hash : false;
    } catch (error) {
      setSaveState("failed");
      setToast(error instanceof ApiError && error.code === "snapshot_conflict" ? "保存冲突：请重新载入文件" : "保存失败，代码仍保留在编辑器中");
      return false;
    }
  }, [attemptId, content, file, saveState, userId]);

  useEffect(() => { saveRef.current = save; }, [save]);
  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (saveState !== "saved") { event.preventDefault(); event.returnValue = ""; }
    };
    addEventListener("beforeunload", protect);
    return () => removeEventListener("beforeunload", protect);
  }, [saveState]);

  const onMount: OnMount = (editor, monaco) => {
    editorRef.current = editor;
    breakpointDecorations.current = editor.createDecorationsCollection();
    debugLineDecorations.current = editor.createDecorationsCollection();
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => { void saveRef.current(); });
    const toggleBreakpoint = (line: number) => {
      const path = activeFileRef.current;
      setBreakpoints((current) => {
        const existing = current[path] || [];
        return { ...current, [path]: existing.includes(line) ? existing.filter((item) => item !== line) : [...existing, line].sort((a, b) => a - b) };
      });
    };
    editor.onKeyDown((event) => {
      if (event.browserEvent.key !== "F9") return;
      event.preventDefault();
      event.stopPropagation();
      const line = editor.getPosition()?.lineNumber;
      if (line) toggleBreakpoint(line);
    });
    editor.onMouseDown((event) => {
      if (event.target.type !== monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN
        && event.target.type !== monaco.editor.MouseTargetType.GUTTER_LINE_NUMBERS) return;
      const line = event.target.position?.lineNumber;
      if (!line) return;
      toggleBreakpoint(line);
    });
  };

  useEffect(() => {
    activeFileRef.current = activeFile;
    breakpointDecorations.current?.set((breakpoints[activeFile] || []).map((line) => ({
      range: { startLineNumber: line, startColumn: 1, endLineNumber: line, endColumn: 1 },
      options: { isWholeLine: true, glyphMarginClassName: "debug-breakpoint", glyphMarginHoverMessage: { value: "断点 · 调试后可继续到这里" } },
    })));
  }, [activeFile, breakpoints]);

  useEffect(() => {
    const step = bottomTab === "debug" ? debugRun?.trace?.[debugIndex] : undefined;
    debugLineDecorations.current?.set(step?.file === activeFile ? [{
      range: { startLineNumber: step.line, startColumn: 1, endLineNumber: step.line, endColumn: 1 },
      options: { isWholeLine: true, className: "debug-current-line" },
    }] : []);
  }, [activeFile, bottomTab, debugIndex, debugRun]);

  useEffect(() => {
    if (!locateTarget || !file || file.path !== locateTarget.file) return;
    const editor = editorRef.current;
    if (!editor) { setLocateTarget(null); return; }
    const total = editor.getModel()?.getLineCount() || locateTarget.line;
    const line = Math.min(Math.max(locateTarget.line, 1), total);
    editor.revealLineInCenter(line);
    editor.setPosition({ lineNumber: line, column: 1 });
    editor.focus();
    setLocateTarget(null);
  }, [content, file, locateTarget]);

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

  async function createCheckVersion(expectedHash: string) {
    if (!file) return null;
    try {
      const created = await api<{ id: string; label: string }>(`/api/product/attempts/${attemptId}/snapshots`, userId, {
        method: "POST", body: JSON.stringify({ operation_id: newOperation("ui-snapshot"), expected_file_hash: expectedHash, expected_file_path: file.path, reuse_unchanged: true }),
      });
      setNeedsSnapshot(false);
      return created;
    } catch (error) {
      setToast(error instanceof Error ? error.message : "无法准备本次检查，请稍后重试");
      return null;
    }
  }

  async function run() {
    if (actionLock.current) return;
    actionLock.current = true;
    const prepared = await prepareCurrentVersion();
    if (!prepared) { actionLock.current = false; return; }
    const { snapshot: targetSnapshot, stateVersion } = prepared;
    setRunEvidence(null);
    setRunOutcome(null);
    setRunSnapshot(targetSnapshot.label); setOldSnapshotNotice(false); setRunMessage("等待可用执行资源…"); setRunState("queued");
    setBottomTab("result"); setResultCollapsed(false);
    setRunState("running"); setRunMessage("正在处理检查，结果返回后会显示在这里…");
    try {
      const result = await api<{ last_tool_status: string; flow_status: string; guidance?: unknown }>(`/api/product/attempts/${attemptId}/runs`, userId, {
        method: "POST", body: JSON.stringify({ operation_id: newOperation("ui-run"), snapshot_id: targetSnapshot.id, expected_state_version: stateVersion, observation }),
      });
      setRunState("idle");
      setRunOutcome(result.last_tool_status);
      setRunMessage(result.last_tool_status === "succeeded" ? (result.flow_status === "COMPLETED" ? "实训任务已完成，可以提交作品。" : result.flow_status === "READY_FOR_NEXT_STAGE" ? "本阶段已完成，已进入下一阶段。" : "本次自动检查通过，请查看尚待完成的验收要求。") : result.last_tool_status === "infrastructure_failure" ? "运行环境异常，本次不计入学习失败次数。" : "检查未通过，请根据下方信息修改代码。" );
      await load(); setBottomTab("result"); setResultCollapsed(false);
    } catch (error) {
      const apiError = error instanceof ApiError ? error : null;
      setRunState(apiError?.code === "timeout" ? "timeout" : "failed");
      setRunMessage(apiError?.code === "openhands_unavailable" ? "运行环境暂不可用，本次不计入学生错误。" : apiError?.message || "运行失败，请检查连接后重试。");
      await load();
    } finally { actionLock.current = false; }
  }

  async function runProgram(tool: SelfServiceTool) {
    if (actionLock.current) return;
    actionLock.current = true;
    const debugging = tool.name === "run_python_trace";
    const prepared = await prepareCurrentVersion();
    if (!prepared) { actionLock.current = false; return; }
    const { snapshot: targetSnapshot, stateVersion } = prepared;
    setOldSnapshotNotice(false);
    if (debugging) {
      setDebugRun(null); setDebugState("running"); setDebugIndex(0);
      setDebugMessage("正在隔离工作区中采集逐行轨迹…");
    } else {
      setConsoleRun(null); setConsoleState("running");
      setConsoleMessage(`正在隔离工作区中运行${tool.label}，完成后这里会显示它的真实输出。`);
    }
    setBottomTab(debugging ? "debug" : "console"); setResultCollapsed(false);
    try {
      const result = await api<ConsoleRun>(`/api/product/attempts/${attemptId}/program-runs`, userId, {
        method: "POST", body: JSON.stringify({ operation_id: newOperation("ui-program"), snapshot_id: targetSnapshot.id, expected_state_version: stateVersion, tool: tool.name,
          stdin_text: debugging ? terminalSession?.events.filter((event) => event.kind === "input").map((event) => event.text).join("") || undefined : undefined }),
      });
      if (debugging) {
        setDebugRun(result); setDebugState("idle");
        setDebugMessage(result.status === "succeeded" ? "程序已运行，可以按步骤查看变量。" : "程序已停止，按步骤找到出错前的变量。");
      } else {
        setConsoleRun(result); setConsoleState("idle");
        setConsoleMessage(result.status === "succeeded" ? "程序已结束运行。" : "程序结束运行，下面是它的输出与报错。");
      }
      await load();
    } catch (error) {
      const apiError = error instanceof ApiError ? error : null;
      const failure = apiError?.code === "openhands_unavailable" ? "运行环境暂不可用，稍后可以重试；你的代码没有丢失。" : apiError?.message || "运行失败，请检查连接后重试。";
      if (debugging) { setDebugRun(null); setDebugState("failed"); setDebugMessage(failure); }
      else { setConsoleRun(null); setConsoleState("failed"); setConsoleMessage(failure); }
      await load();
    } finally { actionLock.current = false; }
  }

  async function startInteractiveTerminal() {
    if (actionLock.current) return;
    actionLock.current = true;
    setTerminalBusy(true); setTerminalError(""); setTerminalSession(null); setTerminalInput("");
    setBottomTab("console"); setResultCollapsed(false);
    if (window.innerWidth >= 768 && resultHeight < 320) updateResultHeight(320);
    try {
      const prepared = await prepareCurrentVersion();
      if (!prepared) return;
      const next = await api<TerminalSession>(`/api/product/attempts/${attemptId}/terminal-sessions`, userId, {
        method: "POST", body: JSON.stringify({ operation_id: newOperation("ui-terminal"), snapshot_id: prepared.snapshot.id, expected_state_version: prepared.stateVersion }),
      });
      setTerminalInput(""); setTerminalSession(next); setOldSnapshotNotice(false);
    } catch (error) {
      setTerminalError(error instanceof Error ? error.message : "终端启动失败，请重试。");
    } finally { setTerminalBusy(false); actionLock.current = false; }
  }

  async function sendTerminalLine() {
    if (!terminalSession || terminalSession.status !== "running" || terminalSession.stdin_closed || terminalSending) return;
    const line = terminalInput;
    if (new TextEncoder().encode(line + "\n").length > 1024) { setTerminalError("这一行超过 1 KB，请缩短后重试。"); return; }
    setTerminalSending(true); setTerminalError("");
    try {
      const next = await api<TerminalSession>(`/api/product/attempts/${attemptId}/terminal-sessions/${terminalSession.session_id}/input`, userId, {
        method: "POST", body: JSON.stringify({ line }),
      });
      setTerminalSession(next); setTerminalInput(""); terminalInputRef.current?.focus();
    } catch (error) { setTerminalError(error instanceof Error ? error.message : "输入发送失败，请重试。"); }
    finally { setTerminalSending(false); }
  }

  async function closeTerminalInput() {
    if (!terminalSession || terminalSession.status !== "running") return;
    try {
      const next = await api<TerminalSession>(`/api/product/attempts/${attemptId}/terminal-sessions/${terminalSession.session_id}/eof`, userId, { method: "POST" });
      setTerminalSession(next);
    } catch (error) { setTerminalError(error instanceof Error ? error.message : "结束输入失败，请重试。"); }
  }

  async function stopInteractiveTerminal() {
    if (!terminalSession || terminalSession.status !== "running") return;
    try {
      const next = await api<TerminalSession>(`/api/product/attempts/${attemptId}/terminal-sessions/${terminalSession.session_id}/stop`, userId, { method: "POST" });
      setTerminalSession(next);
    } catch (error) { setTerminalError(error instanceof Error ? error.message : "停止程序失败，请重试。"); }
  }

  async function locate(target: LocateTarget) {
    if (data && !data.files.some((item) => item.path === target.file)) {
      setToast(`文件 ${target.file} 不在当前练习区`);
      return;
    }
    if (file?.path !== target.file) {
      if (saveState === "dirty" || saveState === "failed") {
        const savedHash = await save();
        if (!savedHash) return;
      }
      await loadFile(target.file);
    }
    setLocateTarget(target);
  }

  function updateResultHeight(next: number) {
    const maximum = Math.max(240, window.innerHeight - 280);
    const height = Math.max(160, Math.min(maximum, next));
    setResultHeight(height);
    localStorage.setItem("student-result-height", String(height));
  }

  function startResultResize(event: React.PointerEvent<HTMLDivElement>) {
    if (matchMedia("(max-width: 767px)").matches) return;
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = resultHeight;
    const move = (moveEvent: PointerEvent) => updateResultHeight(startHeight + startY - moveEvent.clientY);
    const stop = () => {
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", stop);
      document.body.classList.remove("resizing-result");
    };
    document.body.classList.add("resizing-result");
    addEventListener("pointermove", move);
    addEventListener("pointerup", stop, { once: true });
  }

  async function createFile() {
    const path = filePathInput.trim().replaceAll("\\", "/");
    if (!path) return;
    if (saveState !== "saved" && !(await save())) return;
    try {
      await api(`/api/product/attempts/${attemptId}/files/${encodeURIComponent(path).replaceAll("%2F", "/")}`, userId, {
        method: "PUT", body: JSON.stringify({ content: "", expected_hash: EMPTY_SHA256 }),
      });
      setFileAction(null); setFilePathInput(""); setNeedsSnapshot(true);
      await load(); await loadFile(path); setToast(`已新建 ${path}`);
    } catch (error) { setToast(error instanceof Error ? error.message : "新建文件失败"); }
  }

  async function renameFile() {
    if (!file) return;
    const path = filePathInput.trim().replaceAll("\\", "/");
    if (!path || path === file.path) return;
    if (saveState !== "saved") { setToast("请先保存当前文件，再重命名"); return; }
    try {
      await api(`/api/product/attempts/${attemptId}/files/${encodeURIComponent(file.path).replaceAll("%2F", "/")}`, userId, {
        method: "PATCH", body: JSON.stringify({ target_path: path, expected_hash: file.hash }),
      });
      setFileAction(null); setFilePathInput(""); setActiveFile(path); setFile(null); setNeedsSnapshot(true);
      await load(); await loadFile(path); setToast(`已重命名为 ${path}`);
    } catch (error) { setToast(error instanceof Error ? error.message : "重命名失败"); }
  }

  async function deleteFile() {
    if (!file) return;
    if (saveState !== "saved") { setToast("请先保存当前文件，再删除"); return; }
    try {
      await api(`/api/product/attempts/${attemptId}/files/${encodeURIComponent(file.path).replaceAll("%2F", "/")}`, userId, {
        method: "DELETE", body: JSON.stringify({ expected_hash: file.hash }),
      });
      const deletedPath = file.path;
      const fallback = data?.files.find((item) => item.path !== deletedPath)?.path || "";
      setFileAction(null); setFilePathInput(""); setFile(null); setContent(""); setActiveFile(fallback); setNeedsSnapshot(true);
      await load(); if (fallback) await loadFile(fallback); setToast(`已删除 ${deletedPath}`);
    } catch (error) { setToast(error instanceof Error ? error.message : "删除文件失败"); }
  }

  async function prepareCurrentVersion() {
    if (!file || !data || saveState === "saving") return null;
    setPreparing(true);
    try {
      const hash = saveState === "saved" ? file.hash : await save();
      if (!hash) return null;
      const snapshot = await createCheckVersion(hash);
      if (!snapshot) return null;
      if (runSnapshot && runSnapshot !== snapshot.label) { setRunOutcome(null); setRunMessage(""); setRunSnapshot(null); }
      const fresh = await api<Workbench>(`/api/product/attempts/${attemptId}/workbench`, userId);
      setData(fresh);
      return { snapshot, stateVersion: fresh.attempt.state_version };
    } catch (error) { setToast(error instanceof Error ? error.message : "准备当前代码失败，请重试"); return null; }
    finally { setPreparing(false); }
  }

  async function openSubmit() {
    if (actionLock.current) return;
    actionLock.current = true;
    try { if (await prepareCurrentVersion()) setView("submit"); }
    finally { actionLock.current = false; }
  }

  async function askTeacher() {
    if (!data || actionLock.current) return;
    setHelpBusy(true); actionLock.current = true;
    try {
      const prepared = await prepareCurrentVersion();
      if (!prepared) return;
      await api(`/api/product/attempts/${attemptId}/help`, userId, {method: "POST", body: JSON.stringify({
        operation_id: newOperation("ui-help"), expected_state_version: prepared.stateVersion,
        message: observation.trim() || `我在“${data.stage.title}”遇到了困难，请老师结合当前代码和检查结果指导。`,
      })});
      setToast("求助已发送。老师会看到你的排查记录和当前代码版本；已有检查结果可在详情查看。"); await load();
    } catch (error) { setToast(error instanceof Error ? error.message : "求助发送失败，请重试"); }
    finally { setHelpBusy(false); actionLock.current = false; }
  }

  async function guidance() {
    if (actionLock.current) return;
    actionLock.current = true; setCoachLoading(true);
    const prepared = await prepareCurrentVersion();
    if (!prepared) { actionLock.current = false; setCoachLoading(false); return; }
    const { snapshot: targetSnapshot, stateVersion } = prepared;
    try {
      const result = await api<{ guidance?: { message?: string; next_step?: string; success?: boolean; fallback_reason?: string | null } | null }>(`/api/product/attempts/${attemptId}/guidance`, userId, { method: "POST", body: JSON.stringify({ operation_id: newOperation("ui-guidance"), snapshot_id: targetSnapshot.id, expected_state_version: stateVersion, observation }) });
      if (result.guidance?.success === true && result.guidance.message) {
        setCoachFailure("");
        const unchanged = data?.guidance?.message === result.guidance.message && data?.guidance?.next_step === result.guidance.next_step;
        setCoachRepeat(unchanged);
        setToast(unchanged ? "已重新调用模型，但本次建议与上次相同。" : "AI 已根据当前代码和证据生成新建议");
      } else {
        const message = guidanceFailureMessage(result.guidance?.fallback_reason);
        setCoachRepeat(false);
        setCoachFailure(message);
        setToast(message);
      }
      await load();
    } catch (error) {
      const message = error instanceof ApiError && ["model_unavailable", "ai_guidance_paused"].includes(error.code) ? "智能指导暂不可用，你仍可以继续编辑和运行检查。" : error instanceof Error ? error.message : "指导请求失败";
      setCoachRepeat(false);
      setCoachFailure(message);
      setToast(message);
    } finally { setCoachLoading(false); actionLock.current = false; }
  }

  async function openEvidence(requirement: Requirement) {
    if (!requirement.operation_id) return;
    setEvidenceLoading(true);
    try { setEvidence(await api<Evidence>(`/api/product/attempts/${attemptId}/evidence/${encodeURIComponent(requirement.operation_id)}`, userId)); }
    catch (error) { setToast(error instanceof Error ? error.message : "证据读取失败"); }
    finally { setEvidenceLoading(false); }
  }

  if (!userId) return <Setup />;
  if (!attemptId) return <StudentLobby userId={userId} />;
  if (loading) return <Loading />;
  if (fatal || !data) return <Fatal message={fatal || "工作台不可用"} retry={() => { setLoading(true); void load(); }} />;

  const currentEvidence = data.requirements.filter((item) => item.status !== "NOT_RUN").slice(0, 2);
  const observationMinimum = data.requirements.find((item) => item.kind === "STUDENT_EXPLANATION")?.min_length || 20;
  const latestCheck = [...data.timeline].reverse().find((item) => item.label.includes("运行检查"));
  const guidanceData = data.guidance;
  const guidanceParts = guidanceData ? splitGuidance(guidanceData.message ?? "") : null;
  const selfServiceTools = data.self_service_tools ?? [];
  const sampleTool = selfServiceTools.find((tool) => tool.name === "run_student_program" || tool.name === "run_python_sample") ?? null;
  const traceTool = selfServiceTools.find((tool) => tool.name === "run_python_trace") ?? null;
  const consoleBusy = preparing || helpBusy || coachLoading || terminalBusy || terminalSession?.status === "running" || terminalSession?.status === "stopping" || consoleState === "running" || debugState === "running" || runState === "queued" || runState === "running";
  const isPythonTask = data.task.key.startsWith("PYB-");
  const visibleAssignments = isPythonTask ? assignments.filter((item) => item.task_key.startsWith("PYB-")) : assignments;

  async function switchAssignment(nextAttempt: string) {
    if (nextAttempt === attemptId) return;
    if (saveState !== "saved" && !await save()) return;
    const target = new URL(location.href);
    target.searchParams.set("attempt", nextAttempt);
    target.searchParams.delete("view");
    location.assign(target.toString());
  }

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-mark" aria-hidden="true">AI</span><strong>实训教练</strong></div>
      <div className="crumb"><span>{data.course.name}</span><span aria-hidden="true">/</span><b>{data.task.title}</b></div>
      <div className="top-actions">
        <ThemeToggle theme={theme} onToggle={toggleTheme} />
        <span className={`connection ${connection}`}><i />{connection === "connected" ? "已连接" : connection === "offline" ? "网络断开" : "正在重连"}</span>
        <span className={`save-state ${saveState}`} aria-live="polite">{({ saved: "已保存", dirty: "未保存", saving: "保存中…", failed: "保存失败，代码仍在编辑器中" } as const)[saveState]}</span>
        {saveState === "failed" && <button className="save-retry" onClick={() => void save()}>重试保存</button>}
        {view === "workbench" && data.latest_submission && <button className="button quiet" onClick={() => setView("recap")}>查看提交</button>}
        {view === "workbench" && <button className="button quiet" disabled={consoleBusy} onClick={() => void openSubmit()}>{preparing ? "正在保存…" : data.latest_submission ? "提交修订" : "提交"}</button>}
        {view === "workbench" && <button ref={coachButtonRef} className="coach-toggle" aria-controls="student-coach" aria-expanded={coachOpen} onClick={() => setCoachOpen((value) => !value)}>AI 教练</button>}
        <span className="avatar" aria-label={`当前学生：${data.identity.display_name}`}>{data.identity.display_name.slice(0, 1)}</span>
      </div>
    </header>

    {view !== "workbench" && <SubmitViews userId={userId} attemptId={attemptId} mode={view} explanation={observation} hiddenCheck={isPythonTask} onBack={() => setView("workbench")} onSubmitted={() => { setView("recap"); void load(); }} />}
    {view === "workbench" && oldSnapshotNotice && <div className="snapshot-banner" role="status">现有结果依据 {versionLabel(bottomTab === "console" ? consoleRun?.snapshot : runSnapshot || data.latest_snapshot?.label)}；你刚才的修改未包含在结果中。重新运行即可检查最新代码。</div>}
    {view === "workbench" && saveState === "failed" && <div className="save-recovery" role="alert"><div><strong>代码尚未保存</strong><span>编辑器中的内容仍然保留。请重试；若冲突持续，可先导出本地副本。</span></div><div><button onClick={() => void save()}>重试保存</button><button onClick={exportDraft}>导出本地代码</button><button onClick={() => { if (confirm("重新读取将放弃编辑器中尚未保存的内容，是否继续？")) { storeDraft(activeFile, null); void loadFile(activeFile); } }}>重新读取</button></div></div>}

    {view === "workbench" && <main className={`workspace-grid ${taskCollapsed ? "task-collapsed" : ""} ${isPythonTask ? "python-workspace" : ""}`}>
      <aside className="task-pane" aria-label="任务与阶段">
        <div className="task-compact-rail"><button onClick={() => setTaskCollapsed(false)} aria-label="展开任务栏">展开任务</button><b>{data.stage.position + 1}/{data.stage.total}</b><span>{data.stage.title}</span></div>
        <section className="task-head"><div className="task-head-row"><span className="task-id">{data.task.key} · {data.task.version}</span><button className="task-collapse" onClick={() => setTaskCollapsed(true)}>收起任务</button></div><h1>{data.task.title}</h1><p>{data.stage.objective}</p>{visibleAssignments.length > 1 && <label className="task-switch"><span>切换练习</span><select aria-label="切换练习" value={attemptId} disabled={preparing || saveState === "saving"} onChange={(event) => void switchAssignment(event.target.value)}>{visibleAssignments.map((item) => <option key={item.attempt_id} value={item.attempt_id}>{item.task_key} · {item.task_title}</option>)}</select></label>}</section>
        {!isPythonTask && <section className="pane-section stage-section"><div className="section-row"><h2>任务进度</h2><span>{data.stage.position + 1}/{data.stage.total}</span></div><div className="stage-progress" role="progressbar" aria-label="任务阶段进度" aria-valuemin={1} aria-valuemax={data.stage.total} aria-valuenow={data.stage.position + 1}><i style={{ transform: `scaleX(${(data.stage.position + 1) / data.stage.total})` }} /></div><p className="stage-current">当前：{data.stage.title}</p><details className="task-details"><summary>查看全部阶段</summary><ol className="stage-list">{data.stages.map((stage) => <li key={stage.key} className={stage.status}><span className="stage-index">{stage.status === "complete" ? "✓" : stage.position + 1}</span><span>{stage.title}</span>{stage.status === "current" && <b>当前</b>}{stage.status === "skipped" && <b className="stage-note">本次未执行</b>}</li>)}</ol></details></section>}
        {!isPythonTask && <details className="pane-section requirement-compact task-details requirement-details"><summary><span className="section-row"><span><strong>本阶段验收</strong></span><span>{data.requirement_summary.satisfied_count}/{data.requirement_summary.required_count}</span></span></summary><div className="requirement-details-body">{data.requirements.map((item) => <button key={item.id} disabled={!item.operation_id} onClick={() => void openEvidence(item)} className={`compact-requirement ${item.status.toLowerCase()}`}><span aria-hidden="true">{item.status === "SATISFIED" ? "✓" : item.status === "NOT_SATISFIED" ? "×" : "·"}</span><span><b>{item.name}</b>{item.student_goal && <small>{item.student_goal}</small>}</span>{item.has_old_result && <small>有旧结果</small>}</button>)}</div></details>}
      </aside>

      <section className={`code-pane ${resultCollapsed ? "result-collapsed" : ""}`} style={{ "--result-height": `${resultHeight}px` } as React.CSSProperties} aria-label="Workspace 代码区">
        <div className="workspace-toolbar">
          <div className="file-context"><span className="workspace-label">编辑文件</span><strong>{activeFile}</strong></div>
          <div className="version-context" aria-label="当前代码版本"><span>当前代码版本</span><b>{versionLabel(data.latest_snapshot?.label)}</b><small>运行检查会自动保存新版本</small></div>
          <div className="toolbar-actions"><button className="button quiet" onClick={() => void save()} disabled={saveState === "saving" || saveState === "saved"}>保存 <kbd>Ctrl S</kbd></button><button className="button quiet" title={sampleTool ? `在隔离环境中试运行 ${isPythonTask ? "main.py，可在终端逐行输入" : "faq_app.py"}` : "当前任务没有配置可用的程序试运行工具"} onClick={() => { setBottomTab("console"); setResultCollapsed(false); if (isPythonTask) void startInteractiveTerminal(); else if (sampleTool) void runProgram(sampleTool); }} disabled={consoleBusy || !sampleTool}>试运行</button>{traceTool && <button className="button quiet debug-trigger" title="用最近一次终端输入或公开样例运行，随后单步查看代码与变量" onClick={() => void runProgram(traceTool)} disabled={consoleBusy}>调试</button>}<button className={`button primary ${runState === "queued" || runState === "running" ? "is-running" : ""}`} onClick={() => void run()} disabled={consoleBusy}>{runState === "queued" ? "检查排队" : runState === "running" ? "检查中…" : isPythonTask ? "运行公开检查" : "运行并检查"}</button></div>
          <p className="mobile-ide-note">手机适合查看任务、反馈与求助；完整编码建议使用电脑。</p>
        </div>
        <div className="editor-zone">
          <nav className="file-tree" aria-label="文件列表">
            <div className="file-tree-head"><strong>文件</strong><details className="file-tools"><summary>文件操作</summary><div><button onClick={() => { setFileAction("create"); setFilePathInput(""); }} aria-label="新建文件" title="新建文件">新建</button><button onClick={() => { setFileAction("rename"); setFilePathInput(file?.path || ""); }} disabled={!file} aria-label="重命名当前文件" title="重命名当前文件">重命名</button><button onClick={() => setFileAction("delete")} disabled={!file} aria-label="删除当前文件" title="删除当前文件">删除</button></div></details></div>
            {fileAction && <div className={`file-action ${fileAction}`}>
              {fileAction === "delete" ? <><strong>删除 {file?.path}？</strong><p>此操作只删除当前练习区中的文件。</p><div><button onClick={() => setFileAction(null)}>取消</button><button className="danger" onClick={() => void deleteFile()}>确认删除</button></div></> : <><label htmlFor="file-path">{fileAction === "create" ? "新文件路径" : "重命名为"}</label><input id="file-path" autoFocus value={filePathInput} onChange={(event) => setFilePathInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void (fileAction === "create" ? createFile() : renameFile()); if (event.key === "Escape") setFileAction(null); }} placeholder="例如 utils/helper.py"/><small>支持 .py、.json、.md、.txt、.toml、.yaml</small><div><button onClick={() => setFileAction(null)}>取消</button><button className="confirm" disabled={!filePathInput.trim()} onClick={() => void (fileAction === "create" ? createFile() : renameFile())}>{fileAction === "create" ? "新建" : "重命名"}</button></div></>}
            </div>}
            <div className="file-list">{data.files.map((item) => <button key={item.path} className={item.path === activeFile ? "active" : ""} aria-label={`打开 ${item.path}`} title={item.path} disabled={preparing || saveState === "saving"} onClick={async () => { if (saveState !== "saved" && !await save()) return; void loadFile(item.path); }}><span className="file-ext">{item.name.split(".").pop()?.toUpperCase()}</span><span>{item.path}</span></button>)}</div>
          </nav>
          <div className="editor-frame" aria-label={`${activeFile} 代码编辑器`}>
            <div className="editor-tab"><span>{activeFile}</span>{saveState === "dirty" && <i aria-label="未保存" />}</div>
            <Editor height="100%" theme={theme === "dark" ? "vs-dark" : "vs"} language={activeFile.endsWith(".py") ? "python" : activeFile.endsWith(".json") ? "json" : "markdown"} value={content} onMount={onMount} onChange={(value) => { setContent(value || ""); contentRef.current = value || ""; storeDraft(activeFile, value || ""); setSaveState("dirty"); if (runSnapshot || consoleRun || debugRun || data.requirements.some((item) => item.snapshot_id === data.latest_snapshot?.id && item.status !== "NOT_RUN")) setOldSnapshotNotice(true); }} loading={<EditorLoading />} options={{ readOnly: preparing, minimap: { enabled: false }, glyphMargin: true, fontSize: 14, fontFamily: "Cascadia Code, Consolas, monospace", lineHeight: 22, renderLineHighlight: "line", padding: { top: 12 }, scrollBeyondLastLine: false, wordWrap: "off", automaticLayout: true, tabSize: 4, ariaLabel: `${activeFile} 代码编辑器` }} />
          </div>
        </div>
        <div className="result-panel">
          {!resultCollapsed && <div className="result-resize-handle" role="separator" aria-label="调整运行结果区域高度" aria-orientation="horizontal" aria-valuemin={160} aria-valuemax={Math.max(240, window.innerHeight - 280)} aria-valuenow={resultHeight} tabIndex={0} onPointerDown={startResultResize} onKeyDown={(event) => { if (event.key === "ArrowUp") { event.preventDefault(); updateResultHeight(resultHeight + 24); } if (event.key === "ArrowDown") { event.preventDefault(); updateResultHeight(resultHeight - 24); } }}><span /></div>}
          <div className="result-head"><div className="tabs" role="tablist" aria-label="运行与验收"><button role="tab" aria-selected={bottomTab === "console"} onClick={() => { setBottomTab("console"); setResultCollapsed(false); }}>终端{consoleRun && <span aria-hidden="true">·</span>}</button>{traceTool && <button role="tab" aria-selected={bottomTab === "debug"} onClick={() => { setBottomTab("debug"); setResultCollapsed(false); }}>调试{debugRun && <span aria-hidden="true">·</span>}</button>}<button role="tab" aria-selected={bottomTab === "result"} onClick={() => { setBottomTab("result"); setResultCollapsed(false); }}>检查结果</button><button role="tab" aria-selected={bottomTab === "requirements"} onClick={() => { setBottomTab("requirements"); setResultCollapsed(false); }}>验收详情 <span>{data.requirement_summary.satisfied_count}/{data.requirement_summary.required_count}</span></button></div><button className="result-toggle" aria-expanded={!resultCollapsed} onClick={() => setResultCollapsed((value) => !value)}>{resultCollapsed ? "展开结果" : "收起结果"}</button></div>
          {bottomTab === "result" && <div className="result-content" role="tabpanel"><RunStateView state={runState} outcome={runOutcome} message={runMessage} snapshot={runSnapshot || data.latest_snapshot?.label || ""} evidence={runEvidence} onOpen={() => { if (runEvidence) setEvidence(runEvidence); }} onLocate={(target) => void locate(target)} /></div>}
          {bottomTab === "console" && <div className="result-content console-pane" role="tabpanel">{isPythonTask ? <InteractiveTerminal session={terminalSession} input={terminalInput} onInput={setTerminalInput} error={terminalError} busy={terminalBusy} sending={terminalSending} inputRef={terminalInputRef} onSend={() => void sendTerminalLine()} onStop={() => void stopInteractiveTerminal()} onEof={() => void closeTerminalInput()} onStart={() => void startInteractiveTerminal()} /> : <><div className="console-actions">{selfServiceTools.map((tool) => <button key={tool.name} className="button quiet" onClick={() => void runProgram(tool)} disabled={consoleBusy}>{consoleState === "running" ? "正在运行…" : tool.label}</button>)}<span className="console-actions-note">试运行显示程序输出和报错，不计入阶段验收。</span></div><ConsoleView run={consoleRun} state={consoleState} message={consoleMessage} entryFile="faq_app.py" onLocate={(target) => void locate(target)} /></>}</div>}
          {bottomTab === "debug" && traceTool && <div className="result-content debug-pane" role="tabpanel"><DebugView run={debugRun} state={debugState} message={debugMessage} index={debugIndex} breakpoints={breakpoints} stale={oldSnapshotNotice} onSelect={(index) => { setDebugIndex(index); const step = debugRun?.trace?.[index]; if (step) void locate({ file: step.file, line: step.line }); }} onLocate={(target) => void locate(target)} /></div>}
          {bottomTab === "requirements" && <div className="requirement-checklist" role="tabpanel"><div className="checklist-summary"><div><strong>本阶段要完成</strong><span>{data.requirement_summary.satisfied ? "全部要求已满足" : `${data.requirement_summary.required_count - data.requirement_summary.satisfied_count} 项仍需完成`}</span></div><b>{data.requirement_summary.satisfied_count}<small> / {data.requirement_summary.required_count}</small></b></div><div className="checklist-items">{data.requirements.map((item) => <button className={`checklist-item ${item.status.toLowerCase()}`} key={item.id} disabled={!item.operation_id} onClick={() => void openEvidence(item)}><span className="check-state" aria-hidden="true">{item.status === "SATISFIED" ? "✓" : item.status === "NOT_SATISFIED" ? "×" : item.status === "INFRASTRUCTURE_ERROR" ? "!" : "·"}</span><span className="check-copy"><b>{item.name}</b><small>{item.student_goal || `${kindLabel(item.kind)} · ${item.evaluator}`}</small>{item.success_criteria && <em>通过标准：{item.success_criteria}</em>}</span><span className="check-result"><b>{statusLabel(item.status)}</b><small>{item.snapshot_label ? versionLabel(item.snapshot_label) : "尚未检查当前代码"}{item.evaluated_at ? ` · ${formatTime(item.evaluated_at)}` : ""}</small>{item.has_old_result && <em>上一检查版本的结果，仅供查看</em>}</span></button>)}</div></div>}
        </div>
      </section>

      {coachOpen && <button className="coach-backdrop" aria-label="关闭下一步提示" onClick={() => { setCoachOpen(false); coachButtonRef.current?.focus(); }} />}
      <aside ref={coachPanelRef} id="student-coach" className={`coach-pane ${coachOpen ? "open" : ""}`} role={coachOpen ? "dialog" : undefined} aria-modal={coachOpen ? "true" : undefined} aria-labelledby="coach-title">
        <div className="coach-head"><div><h2 id="coach-title">AI 教练</h2><p>根据已保存代码与检查结果回答</p></div><div className="coach-head-actions">{guidanceData?.level && <span className={`help-level ${guidanceData.level.toLowerCase()}`}>{helpLabel(guidanceData.level)}</span>}<button className="coach-close" onClick={() => { setCoachOpen(false); coachButtonRef.current?.focus(); }} aria-label="关闭 AI 教练">关闭</button></div></div>
        {data.teacher_feedback?.source !== "system" && data.teacher_feedback && <CoachSection label="老师的指导"><p>{data.teacher_feedback.message}</p><small>{formatTime(data.teacher_feedback.time)}</small></CoachSection>}
        {(runState !== "idle" || currentEvidence.length > 0) && <CoachSection label="检查依据">
          <p className="coach-summary">{runState === "running" ? `正在检查 ${versionLabel(runSnapshot)}…` : runMessage || latestCheck?.label || (data.latest_snapshot ? "点击上方的运行按钮，查看当前版本的检查结果。" : "修改代码后，点击上方的运行按钮；系统会先保存当前版本。")}</p>
          {currentEvidence.length > 0 && <div className="evidence-summary">{currentEvidence.map((item) => <button key={item.id} onClick={() => void openEvidence(item)}><span className={item.status === "SATISFIED" ? "ok" : "bad"}>{item.status === "SATISFIED" ? "通过" : "未通过"}</span><span>{item.name}</span><small>{versionLabel(item.snapshot_label)}</small></button>)}</div>}
        </CoachSection>}
        <CoachSection label="下一步怎么改">
          {guidanceData && data.guidance_snapshot && <p className="guidance-snapshot">{coachFailure ? "上次成功生成" : "模型生成"}{data.guidance_generated_at ? `于 ${formatTime(data.guidance_generated_at)}` : ""} · 依据 {versionLabel(data.guidance_snapshot.label)}</p>}
          {coachFailure && !coachLoading && <p className="coach-action-reason" role="status">{coachFailure}{guidanceData ? " 下方是上次成功生成的建议。" : ""}</p>}
          {coachRepeat && !coachLoading && !coachFailure && <p className="coach-action-reason" role="status">本次已重新调用模型，但建议与上次相同。若代码、检查结果或排查记录没有变化，可以先按这一步修改后再分析。</p>}
          {coachLoading ? <div className="coach-generating"><i /><span>正在根据当前情况生成建议…</span><small>你仍可以继续编辑代码</small></div> : guidanceData && guidanceParts ? <div className="guidance guidance-structured"><div className="guidance-block judgment"><span>当前判断</span><p>{guidanceParts.judgment}</p></div>{guidanceParts.advice && <div className="guidance-block advice"><span>检查方向</span><p>{guidanceParts.advice}</p></div>}{guidanceData.next_step && <div className="guidance-block next"><span>下一步</span><p>{guidanceData.next_step}</p></div>}</div> : <p className="muted">需要建议时，可以让教练分析当前代码；先运行检查会提供更具体的依据。</p>}
          <button className="button coach-action" onClick={() => void guidance()} disabled={consoleBusy || data.attempt.ai_guidance_paused}>{data.attempt.ai_guidance_paused ? "智能指导暂时关闭" : coachLoading ? "正在分析…" : guidanceData ? "重新分析" : "分析当前代码"}</button>
          {data.attempt.ai_guidance_paused && <p className="coach-action-reason" role="status">智能指导暂时关闭；你仍可以继续编辑、运行程序和查看验收结果。</p>}
        </CoachSection>
        <div className="observation-details"><button type="button" aria-expanded={observationExpanded} onClick={() => setObservationExpanded((value) => !value)}>记录我的排查{observation ? ` · ${observation.length} 字` : "（可选）"}</button>{observationExpanded && <CoachSection label="我已经试过什么"><label className="sr-only" htmlFor="observation">写下已经检查过的现象</label><textarea id="observation" aria-describedby="observation-help" value={observation} onChange={(event) => setObservation(event.target.value)} placeholder={data.task.key === "PYB-01" ? "例如：输入 2 和 3，实际输出了 23；我准备检查是否把输入转换成整数。" : data.task.key === "PYB-02" ? "例如：59 分正确，但 60 分输出不及格；我准备检查边界条件。" : data.task.key === "PYB-03" ? "例如：三个数能求和，但空列表没有输出；我准备检查累加变量的初始值。" : data.stage.key === "generate_cited_answer" ? "例如：retrieve 已通过，但 faq_app.py 里还没有 answer()；我准备先返回 answer、citations 和 scope。" : "例如：密码问题有结果，但月球基地问题错误命中了校园资料。"} rows={4} maxLength={512}/><div id="observation-help" className="textarea-meta observation-meta"><span>已写 {observation.length} 字{isPythonTask ? "" : ` · 本阶段至少 ${observationMinimum} 字`}</span><span>检查或求助时会附上这段记录，帮助教练判断下一步</span></div></CoachSection>}</div>
        <div className="teacher-help"><button className="button quiet" disabled={consoleBusy || data.help_requested} onClick={() => void askTeacher()}>{helpBusy ? "发送中…" : data.help_requested ? "已求助，等待老师回复" : "求助教师"}</button><p>老师会看到你的排查记录和当前代码版本；已有检查结果可在详情查看。</p></div>
      </aside>
    </main>}

    {view === "workbench" && (evidence || evidenceLoading) && <EvidenceDrawer evidence={evidence} loading={evidenceLoading} onClose={() => setEvidence(null)} onLocate={(target) => void locate(target)} />}
    {toast && <div className="toast" role="status">{toast}</div>}
  </div>;
}

function CoachSection({ label, children }: { label: string; children: React.ReactNode }) { return <section className="coach-section"><h3>{label}</h3>{children}</section>; }

function RunStateView({ state, outcome, message, snapshot, evidence, onOpen, onLocate }: { state: RunState; outcome: string | null; message: string; snapshot: string; evidence: Evidence | null; onOpen: () => void; onLocate: (target: LocateTarget) => void }) {
  const failed = outcome ? outcome === "student_failure" : evidence?.status === "NOT_SATISFIED";
  const passed = outcome ? outcome === "succeeded" : evidence?.status === "SATISFIED";
  const reason = evidence ? evidenceReasonCopy[evidence.reason_code] : null;
  const title = state === "queued" ? "运行排队" : state === "running" ? "正在运行" : state === "timeout" ? "运行超时" : state === "failed" || outcome === "infrastructure_failure" || evidence?.status === "INFRASTRUCTURE_ERROR" ? "运行环境异常" : failed ? (evidence?.reason_code === "runtime_error" ? "程序运行错误" : "检查未通过") : passed ? "自动检查通过" : "准备运行";
  const checks = evidence?.checks?.length ? evidence.checks : evidence ? parseEvidenceChecks(evidence.stdout_summary) : [];
  return <div className={`run-state ${state} ${failed ? "has-errors" : passed ? "passed" : ""}`}>
    <div className="run-summary"><div className="run-indicator" aria-hidden="true">{state === "running" || state === "queued" ? <i /> : passed ? "✓" : failed || state === "failed" || state === "timeout" ? "!" : "▶"}</div><div><strong>{title}</strong><p>{reason?.title || message || "点击上方的运行按钮；系统会保存代码并显示检查结果。"}</p><span>本次代码：{versionLabel(snapshot)}</span></div></div>
    {evidence?.location && <button className="run-error-location" onClick={() => onLocate(evidence.location as LocateTarget)}><span>定位</span><b>{evidence.location.file}</b><em>第 {evidence.location.line} 行</em><small>点击跳到代码</small></button>}
    {evidence?.error_summary && <pre className="runtime-error" aria-label="程序报错信息">{evidence.error_summary}</pre>}
    {checks.length > 0 && <div className="run-check-list">{checks.map((check, index) => <div key={`${check.code}-${index}`} className={check.passed ? "passed" : "failed"}><span aria-hidden="true">{check.passed ? "✓" : "×"}</span><div><b>{evidenceCheckLabels[check.code || ""] || (check.code?.startsWith("case_") ? `公开样例 ${index + 1}` : check.code) || "检查项"}</b>{!check.passed && check.diagnosis_code && <small>{diagnosisLabels[check.diagnosis_code] || "请查看实际输出"}</small>}{check.detail && <small>{check.detail}</small>}</div></div>)}</div>}
    {evidence && <button className="run-evidence-link" onClick={onOpen}>查看完整证据</button>}
  </div>;
}

function EvidenceDrawer({ evidence, loading, onClose, onLocate }: { evidence: Evidence | null; loading: boolean; onClose: () => void; onLocate: (target: LocateTarget) => void }) {
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
  const checks = evidence ? (evidence.checks?.length ? evidence.checks : parseEvidenceChecks(evidence.stdout_summary)) : [];
  const passed = evidence?.status === "SATISFIED";
  const reason = evidence ? evidenceReasonCopy[evidence.reason_code] || { title: passed ? "本项验收已经通过" : "检查结果未满足验收要求", detail: passed ? "当前代码已经满足这项任务要求。" : "查看下方失败项，先处理唯一未通过的条件。", next: passed ? "继续完成本阶段其他要求。" : "根据失败项修改代码，然后重新检查任务要求。" } : null;
  return <div className="drawer-layer"><button className="drawer-backdrop" aria-label="关闭证据详情" onClick={onClose}/><aside ref={dialogRef} className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-title"><header><div><h2 id="evidence-title">验收证据</h2><p>{evidence?.requirement || "正在读取教学事实"}</p></div><button className="icon-close" onClick={onClose} autoFocus aria-label="关闭">×</button></header>{loading || !evidence || !reason ? <div className="drawer-loading"><i/><span>正在读取有界证据…</span></div> : <div className="drawer-body">
    <section className={`evidence-conclusion ${passed ? "passed" : "failed"}`}><span className="conclusion-mark" aria-hidden="true">{passed ? "✓" : "×"}</span><div className="conclusion-copy"><span>教学结论</span><h3>{passed ? "当前要求已满足" : "当前要求尚未满足"}</h3><div className="conclusion-reason"><b>{passed ? "通过依据" : "失败原因"}</b><p>{reason.title}</p></div></div><b className="conclusion-snapshot">{versionLabel(evidence.snapshot)}</b></section>
    <section className="evidence-explanation"><h3>为什么得到这个结论</h3><p>{reason.detail}</p><div className="evidence-next-step"><b>下一步怎么做</b><p>{reason.next}</p></div>{evidence.location && <button className="evidence-location" onClick={() => { onClose(); void onLocate(evidence.location as LocateTarget); }}><b>{evidence.location.file}</b> · 第 {evidence.location.line} 行 <small>点击跳到代码</small></button>}{evidence.error_summary && <pre className="runtime-error">{evidence.error_summary}</pre>}{checks.length > 0 && <div className="evidence-checks">{checks.map((check, index) => <div key={`${check.code}-${index}`} className={check.passed ? "passed" : "failed"}><span aria-hidden="true">{check.passed ? "✓" : "×"}</span><div><b>{evidenceCheckLabels[check.code || ""] || (check.code?.startsWith("case_") ? `公开样例 ${index + 1}` : check.code) || "验收检查"}</b>{!check.passed && check.diagnosis_code && <small>{diagnosisLabels[check.diagnosis_code] || "请查看实际输出"}</small>}<small>{check.detail || (check.passed ? "检查通过" : "按照上方建议继续修改")}</small></div></div>)}</div>}</section>
    <section className="evidence-trace"><div className="trace-heading"><h3>技术追溯</h3><span>供教师与评审复核</span></div><dl><div><dt>检查版本</dt><dd>{versionLabel(evidence.snapshot)}</dd></div><div><dt>检查工具</dt><dd className="mono">{evidence.tool}</dd></div><div><dt>原因代码</dt><dd className="mono">{evidence.reason_code}</dd></div><div><dt>时间</dt><dd>{new Date(evidence.observed_at).toLocaleString("zh-CN")}</dd></div></dl><details className="trace-identifiers"><summary>操作与运行产物</summary><div><span>Operation</span><code>{evidence.operation}</code></div>{evidence.artifacts.map((artifact) => <div key={artifact.ref}><span>{artifact.kind}</span><code>{artifact.ref}</code><b>{artifact.available ? "可追溯" : "记录缺失"}</b></div>)}</details></section>
    <details className="raw-evidence"><summary>查看原始结构化输出</summary><pre>{evidence.stdout_summary || "本次工具未产生可展示的标准输出。"}</pre>{evidence.stdout_truncated && <p className="bounded-note">输出已按安全上限截断。</p>}</details>
    <footer>已隐藏宿主路径、容器令牌、模型密钥和内部数据库标识。</footer>
  </div>}</aside></div>;
}

function ConsoleView({ run, state, message, entryFile, onLocate }: { run: ConsoleRun | null; state: ConsoleState; message: string; entryFile: string; onLocate: (target: LocateTarget) => void }) {
  const copy = run ? consoleStatusCopy[run.status] ?? { title: `运行结束：${run.status}`, detail: "" } : null;
  const failed = state === "failed" || Boolean(run && run.status !== "succeeded");
  const status = state === "running" ? "正在运行" : run ? copy?.title : state === "failed" ? "启动失败" : "等待运行";
  return <section className={`terminal ${failed ? "has-error" : ""} ${run ? "is-complete" : ""}`} aria-label="程序终端">
    <div className="terminal-bar">
      <div className="terminal-context"><strong>{entryFile}</strong><span>{run?.label || "试运行"}</span></div>
      <span className={`terminal-state ${state === "running" ? "running" : ""}`} role="status"><i aria-hidden="true" />{status}</span>
    </div>
    <div className="terminal-body">
      <div className="terminal-command"><span aria-hidden="true">&gt;</span><code>python {entryFile}</code></div>
      {state === "running" ? <p className="terminal-message">{message || "正在隔离环境中运行…"}</p> : !run ? <p className={`terminal-message ${failed ? "error" : ""}`}>{state === "failed" ? message : "点击上方「试运行」，这里会显示程序的输入、输出和错误。"}</p> : <>
        {run.sample_input && <div className="terminal-block input"><span className="terminal-caption">样例输入 · stdin</span><pre>{run.sample_input}</pre></div>}
        <div className="terminal-block output"><span className="terminal-caption">程序输出 · stdout {run.stdout_truncated && <em>已截断</em>}</span>{run.stdout ? <pre aria-label="程序标准输出">{run.stdout}</pre> : <p className="terminal-no-output">程序没有输出</p>}</div>
        {(run.stderr || run.status !== "succeeded") && <div className="terminal-block error"><span className="terminal-caption">错误输出 · stderr {run.stderr_truncated && <em>已截断</em>}</span>{run.location && <button className="terminal-error-link" onClick={() => onLocate(run.location as LocateTarget)}>定位到 {run.location.file} 第 {run.location.line} 行 ↗</button>}{run.stderr ? <pre aria-label="程序错误输出">{run.stderr}</pre> : <p className="terminal-no-output">{run.status === "infrastructure_failure" ? "运行环境未返回错误文本，请重试。" : `没有错误文本；退出码 ${run.exit_code ?? "未知"}。`}</p>}</div>}
      </>}
    </div>
    {run && <div className="terminal-footer"><span>退出码 <b>{run.exit_code ?? "—"}</b></span><span>{versionLabel(run.snapshot)} · {run.recorded ? "运行记录已保存" : "运行记录未保存"}</span>{run.status !== "succeeded" && copy?.detail && <span className="terminal-footnote">{copy.detail}</span>}</div>}
  </section>;
}

function InteractiveTerminal({ session, input, onInput, error, busy, sending, inputRef, onSend, onStop, onEof, onStart }: {
  session: TerminalSession | null; input: string; onInput: (value: string) => void; error: string;
  busy: boolean; sending: boolean; inputRef: RefObject<HTMLInputElement | null>;
  onSend: () => void; onStop: () => void; onEof: () => void; onStart: () => void;
}) {
  const screenRef = useRef<HTMLDivElement | null>(null);
  const historyIndex = useRef(-1);
  useEffect(() => { screenRef.current?.scrollTo({ top: screenRef.current.scrollHeight }); }, [session?.events, input]);
  useEffect(() => { historyIndex.current = -1; }, [session?.session_id]);
  const running = session?.status === "running";
  const canType = running && !session.stdin_closed;
  const status = busy ? "正在启动" : !session ? "等待运行" : ({ running: "正在运行", stopping: "正在停止", completed: "程序正常结束", failed: "程序运行出错", stopped: "已停止", timed_out: "运行超时" }[session.status]);
  const inputHistory = session?.events.filter((event) => event.kind === "input").map((event) => event.text.replace(/\n$/, "")) || [];
  function onInputKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.ctrlKey && event.key.toLowerCase() === "c") { event.preventDefault(); onStop(); return; }
    if (event.ctrlKey && event.key.toLowerCase() === "d") { event.preventDefault(); onEof(); return; }
    if (event.key === "ArrowUp" && inputHistory.length) {
      event.preventDefault();
      historyIndex.current = historyIndex.current < 0 ? inputHistory.length - 1 : Math.max(0, historyIndex.current - 1);
      onInput(inputHistory[historyIndex.current]);
    } else if (event.key === "ArrowDown" && historyIndex.current >= 0) {
      event.preventDefault();
      historyIndex.current = Math.min(inputHistory.length, historyIndex.current + 1);
      onInput(inputHistory[historyIndex.current] || "");
    }
  }
  return <section className={`terminal live-terminal ${session?.status === "completed" ? "is-complete" : ""} ${session?.status === "failed" || session?.status === "timed_out" ? "has-error" : ""}`} aria-label="交互式程序终端">
    <div className="terminal-bar"><div className="terminal-context"><strong>Python 终端</strong><span>main.py · {session ? versionLabel(session.snapshot) : "等待启动"}</span></div><div className="terminal-bar-actions"><span className={`terminal-state ${running || busy ? "running" : ""}`} role="status"><i aria-hidden="true" />{status}</span>{running ? <><button type="button" onClick={onEof} disabled={session.stdin_closed}>结束输入</button><button type="button" onClick={onStop}>停止运行</button></> : <button type="button" onClick={onStart} disabled={busy || session?.status === "stopping"}>{session ? "重新运行" : "运行"}</button>}</div></div>
    <div className="terminal-screen" ref={screenRef} onClick={(event) => { if (event.target === event.currentTarget || (event.target as HTMLElement).closest(".terminal-flow")) inputRef.current?.focus(); }}>
      <div className="terminal-command-line"><span aria-hidden="true">$</span> python main.py</div>
      {!session && <p className="terminal-start-hint">{busy ? "正在启动隔离程序…" : "点击「运行」后，直接在这里输入程序需要的内容。"}</p>}
      <div className="terminal-flow" role="log" aria-label="终端输出" aria-live="polite">{session?.events.map((event, index) => <span key={index} className={`terminal-event ${event.kind}`}>{event.text}</span>)}{canType && <form className="terminal-inline-form" onSubmit={(event) => { event.preventDefault(); historyIndex.current = -1; onSend(); }}><input ref={inputRef} aria-label="终端输入" autoComplete="off" autoCapitalize="off" autoCorrect="off" enterKeyHint="send" spellCheck={false} value={input} onChange={(event) => onInput(event.target.value)} onKeyDown={onInputKeyDown} disabled={sending} style={{ width: `${Math.max(2, Math.min(input.length + 1, 48))}ch` }} /></form>}</div>
      {session?.output_truncated && <p className="terminal-limit">输出达到上限，后续内容已截断。</p>}
      {session && !running && session.status !== "stopping" && <p className="terminal-exit">{session.status === "stopped" ? "进程已停止" : "进程已结束"} · 退出码 {session.exit_code ?? "—"}</p>}
    </div>
    {error && <p className="terminal-inline-error" role="alert">{error}</p>}
    <div className="terminal-live-footer"><span>{canType ? "直接输入，按 Enter 发送 · ↑↓ 找回本次输入" : session?.status === "stopping" ? "程序正在停止…" : session?.stdin_closed && running ? "输入已结束，等待程序退出" : "试运行不计入阶段验收"}</span><span>{session ? versionLabel(session.snapshot) : "Python"}</span></div>
  </section>;
}

function DebugView({ run, state, message, index, breakpoints, stale, onSelect, onLocate }: {
  run: ConsoleRun | null; state: ConsoleState; message: string; index: number;
  breakpoints: Record<string, number[]>; stale: boolean;
  onSelect: (index: number) => void; onLocate: (target: LocateTarget) => void;
}) {
  if (state === "running") return <div className="console-status running"><i aria-hidden="true" /><div><strong>正在采集逐行轨迹</strong><p>{message}</p></div></div>;
  if (!run) return <div className={`console-status ${state === "failed" ? "failed" : ""}`}><strong>{state === "failed" ? "这次调试未能启动" : "设置断点后开始调试"}</strong><p>{message || "点击编辑器行号或按 F9 设置断点，再按上方「调试」。程序会在隔离环境运行一次，你可以逐步回放。"}</p></div>;
  const trace = run.trace || [];
  const current = trace[index];
  const nextBreakpoint = trace.findIndex((step, position) => position > index && (breakpoints[step.file] || []).includes(step.line));
  return <div className="debug-session">
    <div className="debug-summary"><div><strong>逐行调试 · {versionLabel(run.snapshot)}</strong><span>{run.label} · 退出码 {run.exit_code ?? "未知"}</span></div>{stale && <em>代码已修改；轨迹对应旧版本</em>}</div>
    <div className="debug-controls"><button className="button quiet" disabled={index <= 0} onClick={() => onSelect(index - 1)}>上一步</button><button className="button quiet" disabled={index >= trace.length - 1} onClick={() => onSelect(index + 1)}>下一步</button><button className="button quiet" disabled={nextBreakpoint < 0} onClick={() => onSelect(nextBreakpoint)}>继续到断点</button><span>{trace.length ? `第 ${index + 1} / ${trace.length} 步` : "没有逐行轨迹"}</span></div>
    {current ? <div className="debug-current"><button onClick={() => onLocate({ file: current.file, line: current.line })}>{current.file} · 第 {current.line} 行 ↗</button><span>执行这一行之前的变量</span><dl>{Object.entries(current.locals).length ? Object.entries(current.locals).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>) : <p>此时还没有局部变量。</p>}</dl></div> : <div className="console-status failed"><strong>无法逐行回放</strong><p>程序可能在语法解析时停止。查看下方错误，并跳到报错行修改。</p></div>}
    {run.trace_truncated && <p className="debug-note">轨迹已达到采集上限；前面的步骤仍可回放。</p>}
    {(run.stdout || run.stderr || run.location) && <details className="debug-output"><summary>本次输出与错误{run.stderr ? " · 有错误" : ""}</summary>{run.location && <button className="button quiet" onClick={() => onLocate(run.location as LocateTarget)}>跳到报错行 {run.location.file}:{run.location.line}</button>}{run.stdout && <pre>stdout\n{run.stdout}</pre>}{run.stderr && <pre className="error">stderr\n{run.stderr}</pre>}</details>}
  </div>;
}

function EditorLoading() { return <div className="editor-loading"><i/><span>正在启动 Workspace 编辑器…</span></div>; }
function Loading() { return <div className="page-loading"><div className="loading-brand"><span className="brand-mark">AI</span><strong>实训教练</strong></div><div className="skeleton-layout"><i/><i/><i/></div><p>正在连接 Workspace 和教学状态…</p></div>; }

function Setup() { return <AuthEntry />; }

function StudentLobby({ userId }: { userId: string }) {
  const { theme, toggleTheme } = useTheme();
  const [classes, setClasses] = useState<Array<{ name: string; course_name: string | null; teacher_name: string }>>([]);
  const [available, setAvailable] = useState<Assignment[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    try {
      const [groupData, assignmentData] = await Promise.all([
        api<{ classes: Array<{ name: string; course_name: string | null; teacher_name: string }> }>("/api/product/student/classes", userId),
        api<{ assignments: Assignment[] }>("/api/product/student/assignments", userId),
      ]);
      setClasses(groupData.classes); setAvailable(assignmentData.assignments); setError("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "学习数据载入失败"); }
    finally { setLoading(false); }
  }, [userId]);
  useEffect(() => { void refresh(); const timer = setInterval(() => void refresh(), 10000); return () => clearInterval(timer); }, [refresh]);
  return <main className="student-lobby">
    <header><div className="brand"><span className="brand-mark">AI</span><strong>实训教练</strong></div><ThemeToggle theme={theme} onToggle={toggleTheme} /></header>
    <section><h1>我的学习空间</h1><p>班级与练习会从教师端同步到这里。</p>
      {error && <div role="alert" className="lobby-error">{error}<button onClick={() => void refresh()}>重试</button></div>}
      {loading ? <p>正在载入班级和练习…</p> : <>
        <div className="lobby-section"><h2>已加入班级</h2>{classes.length ? classes.map((item) => <div className="lobby-row" key={item.name}><strong>{item.name}</strong><span>{item.teacher_name} · {item.course_name || "课程待关联"}</span></div>) : <p>尚未加入班级，请联系教师确认注册信息。</p>}</div>
        <div className="lobby-section"><h2>我的练习</h2>{available.length ? available.map((item) => <a className="lobby-row" key={item.attempt_id} href={`?user=${encodeURIComponent(userId)}&attempt=${encodeURIComponent(item.attempt_id)}`}><strong>{item.task_title}</strong><span>{item.course_name} · 点击进入</span></a>) : <p>教师尚未发布练习。课程和任务准备好后，这里会自动显示入口。</p>}</div>
      </>}
    </section>
  </main>;
}

function Fatal({ message, retry }: { message: string; retry: () => void }) { return <main className="setup-state error"><span className="error-mark">!</span><h1>工作台暂时无法载入</h1><p>{message}</p><button className="button primary" onClick={retry}>重新连接</button></main>; }
