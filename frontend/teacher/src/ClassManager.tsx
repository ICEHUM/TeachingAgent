import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "./api";

type Course = { id: string; name: string; code: string };
type Student = { id: string; display_name: string; account: string | null };
type ClassItem = { id: string; name: string; course_id: string | null; course_name: string | null; students: Student[] };

export function ClassManager({ userId, onOpenCourses }: { userId: string; onOpenCourses: () => void }) {
  const [classes, setClasses] = useState<ClassItem[]>([]);
  const [courses, setCourses] = useState<Course[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("");
  const [courseId, setCourseId] = useState("");
  const [bindId, setBindId] = useState("");
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      const [classData, courseData] = await Promise.all([
        api<{ classes: ClassItem[] }>("/api/product/teacher/classes", userId),
        api<{ courses: Course[] }>("/api/product/teacher/courses", userId),
      ]);
      setClasses(classData.classes);
      setCourses(courseData.courses);
      setSelectedId((id) => classData.classes.some((item) => item.id === id) ? id : classData.classes[0]?.id || "");
      setError("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "班级数据载入失败"); }
    finally { setLoading(false); }
  }, [userId]);
  useEffect(() => {
    void load();
    const timer = setInterval(() => { if (navigator.onLine) void load(); }, 5000);
    return () => clearInterval(timer);
  }, [load]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy("create"); setError(""); setNotice("");
    try {
      const created = await api<{ id: string }>("/api/product/teacher/classes", userId, {
        method: "POST", body: JSON.stringify({ name, course_id: courseId || null }),
      });
      setName(""); setCourseId(""); setSelectedId(created.id);
      await load();
      setNotice("班级已创建。学生现在可以在注册页选择这个班级。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "创建班级失败"); }
    finally { setBusy(""); }
  }

  async function bind(selected: ClassItem) {
    if (!bindId) { setError("请先选择课程。"); return; }
    setBusy("bind"); setError(""); setNotice("");
    try {
      await api(`/api/product/teacher/classes/${selected.id}/course`, userId, {
        method: "PUT", body: JSON.stringify({ course_id: bindId }),
      });
      setBindId("");
      await load();
      setNotice("课程已关联；班内学生已同步获得该课程中可用的 Python 基础练习。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "关联课程失败"); }
    finally { setBusy(""); }
  }

  const selected = classes.find((item) => item.id === selectedId);
  return <main className="class-manager">
    <header className="classes-heading"><div><h1>班级管理</h1><p>创建班级，指定课程；学生注册时选择班级，名单会自动同步到这里。</p></div><button type="button" onClick={() => void load()} disabled={loading || !!busy}>刷新名单</button></header>
    <div className="classes-layout">
      <section className="classes-sidebar" aria-label="我的班级">
        <form onSubmit={(event) => void create(event)} className="classes-create">
          <h2>创建班级</h2>
          <label>班级名称<input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：Python 基础 1 班" minLength={2} maxLength={120} required /></label>
          <label>关联课程 <span>可稍后设置</span><select value={courseId} onChange={(event) => setCourseId(event.target.value)}><option value="">暂不关联课程</option>{courses.map((course) => <option key={course.id} value={course.id}>{course.name}</option>)}</select></label>
          <button className="classes-primary" disabled={busy === "create"}>{busy === "create" ? "正在创建…" : "创建班级"}</button>
        </form>
        <div className="classes-list-head"><h2>我的班级</h2><span>{classes.length}</span></div>
        {loading ? <p className="classes-empty">正在载入班级…</p> : classes.length === 0 ? <p className="classes-empty">还没有班级。填写上方名称即可创建，随后学生就能在注册页选择。</p> : <div className="classes-list">{classes.map((item) => <button type="button" key={item.id} aria-current={selectedId === item.id ? "true" : undefined} onClick={() => { setSelectedId(item.id); setError(""); setNotice(""); }}><strong>{item.name}</strong><span>{item.students.length} 名学生 · {item.course_name || "待关联课程"}</span></button>)}</div>}
      </section>
      <section className="classes-detail" aria-label="班级详情">
        {error && <div className="classes-message error" role="alert">{error}</div>}
        {notice && <div className="classes-message" role="status">{notice}</div>}
        {!selected ? <div className="classes-detail-empty"><h2>从左侧创建或选择班级</h2><p>班级创建后会出现在学生注册页。课程可以现在关联，也可以稍后再选。</p></div> : <>
          <header><div><h2>{selected.name}</h2><p>{selected.course_name ? `关联课程：${selected.course_name}` : "尚未关联课程"}</p></div><span>{selected.students.length} 名学生</span></header>
          {!selected.course_id && <div className="classes-bind"><div><strong>为班级关联课程</strong><p>关联后，学生会同步获得该课程中可用的 Python 基础练习。</p></div>{courses.length ? <div className="classes-bind-controls"><select aria-label="要关联的课程" value={bindId} onChange={(event) => setBindId(event.target.value)}><option value="">选择课程</option>{courses.map((course) => <option key={course.id} value={course.id}>{course.name}</option>)}</select><button type="button" disabled={busy === "bind" || !bindId} onClick={() => void bind(selected)}>{busy === "bind" ? "关联中…" : "关联课程"}</button></div> : <button type="button" onClick={onOpenCourses}>先创建课程</button>}</div>}
          <div className="classes-students-head"><h3>学生名单</h3><span>注册后自动更新</span></div>
          {selected.students.length ? <div className="classes-students">{selected.students.map((student, index) => <div key={student.id}><span className="classes-number">{String(index + 1).padStart(2, "0")}</span><strong>{student.display_name}</strong><span>{student.account || "演示账号"}</span></div>)}</div> : <div className="classes-detail-empty compact"><strong>暂无学生加入</strong><p>让学生打开登录页，选择“注册 → 学生”，然后选择“{selected.name}”。</p></div>}
        </>}
      </section>
    </div>
  </main>;
}
