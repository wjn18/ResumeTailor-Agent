"use client";

import {
  Check,
  ChevronLeft,
  Download,
  FileText,
  LoaderCircle,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  Upload,
  UserRoundPlus,
} from "lucide-react";
import {useRef, useState} from "react";

import {
  buildTailoredResume,
  confirmResume,
  docxDownloadUrl,
  mergePersonalFacts,
  parseJD,
  parseResume,
  saveResumeEdits,
} from "@/lib/api";
import type {
  FormalEducation,
  FormalHonorAward,
  FormalProject,
  FormalResume,
  FormalWorkExperience,
} from "@/types";

type ViewState = "input" | "generating" | "preview";

const emptyEducation = (): FormalEducation => ({
  school: "",
  degree: null,
  major: null,
  start_date: null,
  end_date: null,
});

const emptyProject = (): FormalProject => ({
  name: "",
  role: null,
  start_date: null,
  end_date: null,
  technologies: [],
  bullets: [""],
});

const emptyWorkExperience = (): FormalWorkExperience => ({
  company: "",
  job_title: null,
  start_date: null,
  end_date: null,
  bullets: [""],
});

const emptyHonorAward = (): FormalHonorAward => ({
  name: "",
  issuer: null,
  date: null,
  bullets: [""],
});

export default function Home() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [view, setView] = useState<ViewState>("input");
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [jdText, setJdText] = useState("");
  const [company, setCompany] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [personalInfo, setPersonalInfo] = useState("");
  const [formalResume, setFormalResume] = useState<FormalResume | null>(null);
  const [tailoredResumeId, setTailoredResumeId] = useState<string | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canGenerate = Boolean(resumeFile && jdText.trim());

  async function handleGenerate() {
    if (!resumeFile || !jdText.trim()) return;
    setError(null);
    setView("generating");

    try {
      let parsedResume = await parseResume(resumeFile);
      const parsedJD = await parseJD(jdText, company, jobTitle);
      if (personalInfo.trim()) {
        parsedResume = await mergePersonalFacts(
          personalInfo,
          parsedResume.resume_id,
        );
      }
      const result = await buildTailoredResume(parsedJD, parsedResume);
      setFormalResume(result.formal_resume);
      setTailoredResumeId(result.saved_resume.tailored_resume_id);
      setView("preview");
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "生成失败，请稍后重试。",
      );
      setView("input");
    }
  }

  async function handleEditToggle() {
    if (!formalResume || !tailoredResumeId) return;
    if (!isEditing) {
      setIsEditing(true);
      return;
    }

    setIsSaving(true);
    setError(null);
    try {
      await saveResumeEdits(tailoredResumeId, formalResume);
      setIsEditing(false);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "保存失败。",
      );
    } finally {
      setIsSaving(false);
    }
  }

  async function handleComplete() {
    if (!formalResume || !tailoredResumeId) return;
    setIsSaving(true);
    setError(null);
    try {
      await confirmResume(tailoredResumeId, formalResume);
      const anchor = document.createElement("a");
      anchor.href = docxDownloadUrl(tailoredResumeId);
      anchor.download = "";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(resetWorkspace, 700);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "确认失败。",
      );
    } finally {
      setIsSaving(false);
    }
  }

  function resetWorkspace() {
    setView("input");
    setResumeFile(null);
    setJdText("");
    setCompany("");
    setJobTitle("");
    setPersonalInfo("");
    setFormalResume(null);
    setTailoredResumeId(null);
    setIsEditing(false);
    setError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  return (
    <main className={`workspace workspace-${view}`}>
      <header className="app-header">
        <button
          className="brand"
          onClick={view === "input" ? undefined : resetWorkspace}
          type="button"
        >
          {view !== "input" && <ChevronLeft size={18} aria-hidden />}
          <span className="brand-mark">R</span>
          <span>ResumeTailor</span>
        </button>
        <span className="status-pill">
          <span className="status-dot" />
          {view === "input"
            ? "准备就绪"
            : view === "generating"
              ? "正在生成"
              : isEditing
                ? "编辑中"
                : "预览"}
        </span>
      </header>

      {view === "input" && (
        <section className="submission-view" aria-label="简历提交">
          <div className="submission-stack">
            <section className="input-panel upload-panel">
              <div className="panel-label">
                <Upload size={20} aria-hidden />
                <span>上传简历</span>
              </div>
              <button
                className="file-picker"
                type="button"
                onClick={() => fileInputRef.current?.click()}
              >
                <FileText size={22} aria-hidden />
                <span>
                  {resumeFile ? resumeFile.name : "选择 PDF 或 DOCX 文件"}
                </span>
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                hidden
                onChange={(event) => {
                  const file = event.target.files?.[0] ?? null;
                  setResumeFile(file);
                }}
              />
            </section>

            <section className="input-panel jd-panel">
              <div className="panel-label">
                <Sparkles size={20} aria-hidden />
                <span>岗位信息</span>
              </div>
              <div className="compact-fields">
                <input
                  value={company}
                  onChange={(event) => setCompany(event.target.value)}
                  placeholder="公司名称"
                  aria-label="公司名称"
                />
                <input
                  value={jobTitle}
                  onChange={(event) => setJobTitle(event.target.value)}
                  placeholder="岗位名称"
                  aria-label="岗位名称"
                />
              </div>
              <textarea
                value={jdText}
                onChange={(event) => setJdText(event.target.value)}
                placeholder="粘贴完整 JD"
                aria-label="岗位描述"
              />
            </section>

            <section className="input-panel personal-panel">
              <div className="panel-label">
                <UserRoundPlus size={20} aria-hidden />
                <span>补充个人信息</span>
              </div>
              <textarea
                value={personalInfo}
                onChange={(event) => setPersonalInfo(event.target.value)}
                placeholder="补充简历中没有写明的技能、经历或项目信息"
                aria-label="补充个人信息"
              />
            </section>
          </div>

          {error && <p className="error-message">{error}</p>}

          <button
            className="primary-action generate-button"
            type="button"
            disabled={!canGenerate}
            onClick={handleGenerate}
          >
            <Sparkles size={18} aria-hidden />
            生成简历
          </button>
        </section>
      )}

      {view === "generating" && <ResumeSkeleton />}

      {view === "preview" && formalResume && (
        <section className="preview-view">
          <ResumeEditor
            resume={formalResume}
            onChange={setFormalResume}
            editable={isEditing}
          />

          {error && <p className="preview-error">{error}</p>}

          <div className="preview-actions">
            <button
              className="secondary-action"
              type="button"
              onClick={handleEditToggle}
              disabled={isSaving}
            >
              {isSaving && isEditing ? (
                <LoaderCircle className="spin" size={18} aria-hidden />
              ) : isEditing ? (
                <Check size={18} aria-hidden />
              ) : (
                <Pencil size={18} aria-hidden />
              )}
              {isEditing ? "保存修改" : "修改"}
            </button>
            <button
              className="primary-action"
              type="button"
              onClick={handleComplete}
              disabled={isSaving}
            >
              {isSaving && !isEditing ? (
                <LoaderCircle className="spin" size={18} aria-hidden />
              ) : (
                <Download size={18} aria-hidden />
              )}
              完成
            </button>
          </div>
        </section>
      )}
    </main>
  );
}

function ResumeSkeleton() {
  return (
    <section className="preview-view" aria-label="正在生成简历">
      <div className="resume-paper skeleton-paper">
        <div className="skeleton-line skeleton-name" />
        <div className="skeleton-line skeleton-short" />
        <div className="skeleton-section">
          <div className="skeleton-line skeleton-heading" />
          <div className="skeleton-line" />
          <div className="skeleton-line skeleton-wide" />
          <div className="skeleton-line skeleton-mid" />
        </div>
        <div className="skeleton-section">
          <div className="skeleton-line skeleton-heading" />
          <div className="skeleton-line skeleton-wide" />
          <div className="skeleton-line" />
          <div className="skeleton-line skeleton-mid" />
          <div className="skeleton-line skeleton-wide" />
        </div>
        <div className="skeleton-section">
          <div className="skeleton-line skeleton-heading" />
          <div className="skeleton-line" />
          <div className="skeleton-line skeleton-short" />
        </div>
      </div>
      <div className="generation-status">
        <LoaderCircle className="spin" size={18} aria-hidden />
        正在审核并重组内容
      </div>
    </section>
  );
}

function ResumeEditor({
  resume,
  onChange,
  editable,
}: {
  resume: FormalResume;
  onChange: (resume: FormalResume) => void;
  editable: boolean;
}) {
  function update<K extends keyof FormalResume>(
    key: K,
    value: FormalResume[K],
  ) {
    onChange({...resume, [key]: value});
  }

  return (
    <article className={`resume-paper ${editable ? "is-editing" : ""}`}>
      <div className="resume-masthead">
        <EditableField
          value={resume.name ?? ""}
          onChange={(value) => update("name", value)}
          editable={editable}
          className="resume-name"
          placeholder="姓名"
        />
        <EditableField
          value={resume.headline ?? ""}
          onChange={(value) => update("headline", value)}
          editable={editable}
          className="resume-headline"
          placeholder="目标岗位"
        />
        <div className="contact-line">
          <EditableField
            value={resume.email ?? ""}
            onChange={(value) => update("email", value)}
            editable={editable}
            placeholder="邮箱"
          />
          <span>·</span>
          <EditableField
            value={resume.phone ?? ""}
            onChange={(value) => update("phone", value)}
            editable={editable}
            placeholder="电话"
          />
        </div>
      </div>

      <EditableListSection
        title="个人优势"
        items={resume.advantages}
        editable={editable}
        onChange={(items) => update("advantages", items.slice(0, 6))}
      />

      {(resume.work_experiences.length > 0 || editable) && (
        <ResumeSection title="工作经历">
          {resume.work_experiences.map((workExperience, index) => (
            <div className="resume-entry" key={`work-${index}`}>
              <div className="entry-heading">
                <EditableField
                  value={workExperience.company}
                  onChange={(value) => {
                    const items = [...resume.work_experiences];
                    items[index] = {...workExperience, company: value};
                    update("work_experiences", items);
                  }}
                  editable={editable}
                  className="entry-title"
                  placeholder="公司名称"
                />
                {editable && (
                  <IconButton
                    label="删除工作经历"
                    onClick={() =>
                      update(
                        "work_experiences",
                        resume.work_experiences.filter(
                          (_, itemIndex) => itemIndex !== index,
                        ),
                      )
                    }
                  >
                    <Trash2 size={16} />
                  </IconButton>
                )}
              </div>
              <div className="entry-meta">
                <EditableField
                  value={workExperience.job_title ?? ""}
                  onChange={(value) => {
                    const items = [...resume.work_experiences];
                    items[index] = {...workExperience, job_title: value};
                    update("work_experiences", items);
                  }}
                  editable={editable}
                  placeholder="职位"
                />
                <span>·</span>
                <EditableField
                  value={[workExperience.start_date, workExperience.end_date]
                    .filter(Boolean)
                    .join(" - ")}
                  onChange={(value) => {
                    const [start = "", end = ""] = value.split(" - ");
                    const items = [...resume.work_experiences];
                    items[index] = {
                      ...workExperience,
                      start_date: start,
                      end_date: end,
                    };
                    update("work_experiences", items);
                  }}
                  editable={editable}
                  placeholder="在职时间"
                />
              </div>
              <EditableList
                items={workExperience.bullets}
                editable={editable}
                onChange={(bullets) => {
                  const items = [...resume.work_experiences];
                  items[index] = {...workExperience, bullets};
                  update("work_experiences", items);
                }}
              />
            </div>
          ))}
          {editable && (
            <AddButton
              label="添加工作经历"
              onClick={() =>
                update(
                  "work_experiences",
                  [...resume.work_experiences, emptyWorkExperience()],
                )
              }
            />
          )}
        </ResumeSection>
      )}

      {(resume.projects.length > 0 || editable) && (
        <ResumeSection title="项目经历">
          {resume.projects.map((project, index) => (
            <div className="resume-entry" key={`project-${index}`}>
              <div className="entry-heading">
                <EditableField
                  value={project.name}
                  onChange={(value) => {
                    const projects = [...resume.projects];
                    projects[index] = {...project, name: value};
                    update("projects", projects);
                  }}
                  editable={editable}
                  className="entry-title"
                  placeholder="项目名称"
                />
                {editable && (
                  <IconButton
                    label="删除项目"
                    onClick={() =>
                      update(
                        "projects",
                        resume.projects.filter((_, itemIndex) => itemIndex !== index),
                      )
                    }
                  >
                    <Trash2 size={16} />
                  </IconButton>
                )}
              </div>
              <div className="entry-meta">
                <EditableField
                  value={project.role ?? ""}
                  onChange={(value) => {
                    const projects = [...resume.projects];
                    projects[index] = {...project, role: value};
                    update("projects", projects);
                  }}
                  editable={editable}
                  placeholder="角色"
                />
                <span>·</span>
                <EditableField
                  value={[project.start_date, project.end_date]
                    .filter(Boolean)
                    .join(" - ")}
                  onChange={(value) => {
                    const [start = "", end = ""] = value.split(" - ");
                    const projects = [...resume.projects];
                    projects[index] = {
                      ...project,
                      start_date: start,
                      end_date: end,
                    };
                    update("projects", projects);
                  }}
                  editable={editable}
                  placeholder="时间"
                />
              </div>
              <EditableList
                items={project.bullets}
                editable={editable}
                onChange={(bullets) => {
                  const projects = [...resume.projects];
                  projects[index] = {...project, bullets};
                  update("projects", projects);
                }}
              />
            </div>
          ))}
          {editable && (
            <AddButton
              label="添加项目"
              onClick={() => update("projects", [...resume.projects, emptyProject()])}
            />
          )}
        </ResumeSection>
      )}

      {(resume.honor_awards.length > 0 || editable) && (
        <ResumeSection title="荣誉奖项">
          {resume.honor_awards.map((honorAward, index) => (
            <div className="resume-entry" key={`honor-${index}`}>
              <div className="entry-heading">
                <EditableField
                  value={honorAward.name}
                  onChange={(value) => {
                    const items = [...resume.honor_awards];
                    items[index] = {...honorAward, name: value};
                    update("honor_awards", items);
                  }}
                  editable={editable}
                  className="entry-title"
                  placeholder="奖项名称"
                />
                {editable && (
                  <IconButton
                    label="删除荣誉奖项"
                    onClick={() =>
                      update(
                        "honor_awards",
                        resume.honor_awards.filter(
                          (_, itemIndex) => itemIndex !== index,
                        ),
                      )
                    }
                  >
                    <Trash2 size={16} />
                  </IconButton>
                )}
              </div>
              <div className="entry-meta">
                <EditableField
                  value={honorAward.issuer ?? ""}
                  onChange={(value) => {
                    const items = [...resume.honor_awards];
                    items[index] = {...honorAward, issuer: value};
                    update("honor_awards", items);
                  }}
                  editable={editable}
                  placeholder="颁发方"
                />
                <span>·</span>
                <EditableField
                  value={honorAward.date ?? ""}
                  onChange={(value) => {
                    const items = [...resume.honor_awards];
                    items[index] = {...honorAward, date: value};
                    update("honor_awards", items);
                  }}
                  editable={editable}
                  placeholder="获奖时间"
                />
              </div>
              <EditableList
                items={honorAward.bullets}
                editable={editable}
                onChange={(bullets) => {
                  const items = [...resume.honor_awards];
                  items[index] = {...honorAward, bullets};
                  update("honor_awards", items);
                }}
              />
            </div>
          ))}
          {editable && (
            <AddButton
              label="添加荣誉奖项"
              onClick={() =>
                update(
                  "honor_awards",
                  [...resume.honor_awards, emptyHonorAward()],
                )
              }
            />
          )}
        </ResumeSection>
      )}

      {(resume.education.length > 0 || editable) && (
        <ResumeSection title="教育背景">
          {resume.education.map((education, index) => (
            <div className="education-row" key={`education-${index}`}>
              <div>
                <EditableField
                  value={education.school}
                  onChange={(value) => {
                    const items = [...resume.education];
                    items[index] = {...education, school: value};
                    update("education", items);
                  }}
                  editable={editable}
                  className="entry-title"
                  placeholder="学校"
                />
                <EditableField
                  value={[education.degree, education.major]
                    .filter(Boolean)
                    .join(" / ")}
                  onChange={(value) => {
                    const [degree = "", major = ""] = value.split(" / ");
                    const items = [...resume.education];
                    items[index] = {...education, degree, major};
                    update("education", items);
                  }}
                  editable={editable}
                  className="entry-meta"
                  placeholder="学位 / 专业"
                />
              </div>
              {editable && (
                <IconButton
                  label="删除教育经历"
                  onClick={() =>
                    update(
                      "education",
                      resume.education.filter((_, itemIndex) => itemIndex !== index),
                    )
                  }
                >
                  <Trash2 size={16} />
                </IconButton>
              )}
            </div>
          ))}
          {editable && (
            <AddButton
              label="添加教育经历"
              onClick={() =>
                update("education", [...resume.education, emptyEducation()])
              }
            />
          )}
        </ResumeSection>
      )}

      <EditableListSection
        title="相关技能"
        items={resume.related_skills}
        editable={editable}
        onChange={(items) => update("related_skills", items)}
        compact
      />
    </article>
  );
}

function ResumeSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="resume-section">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function EditableListSection({
  title,
  items,
  editable,
  onChange,
  compact = false,
}: {
  title: string;
  items: string[];
  editable: boolean;
  onChange: (items: string[]) => void;
  compact?: boolean;
}) {
  if (!items.length && !editable) return null;
  return (
    <ResumeSection title={title}>
      {compact && !editable ? (
        <p className="skills-line">{items.join(" / ")}</p>
      ) : (
        <EditableList
          items={items}
          editable={editable}
          onChange={onChange}
        />
      )}
    </ResumeSection>
  );
}

function EditableList({
  items,
  editable,
  onChange,
}: {
  items: string[];
  editable: boolean;
  onChange: (items: string[]) => void;
}) {
  return (
    <div className="editable-list">
      {items.map((item, index) => (
        <div className="editable-list-row" key={`item-${index}`}>
          <span className="resume-bullet">•</span>
          <EditableField
            value={item}
            onChange={(value) => {
              const nextItems = [...items];
              nextItems[index] = value;
              onChange(nextItems);
            }}
            editable={editable}
            multiline
            placeholder="输入内容"
          />
          {editable && (
            <IconButton
              label="删除此项"
              onClick={() =>
                onChange(items.filter((_, itemIndex) => itemIndex !== index))
              }
            >
              <Trash2 size={15} />
            </IconButton>
          )}
        </div>
      ))}
      {editable && (
        <AddButton label="添加一项" onClick={() => onChange([...items, ""])} />
      )}
    </div>
  );
}

function EditableField({
  value,
  onChange,
  editable,
  placeholder,
  className = "",
  multiline = false,
}: {
  value: string;
  onChange: (value: string) => void;
  editable: boolean;
  placeholder: string;
  className?: string;
  multiline?: boolean;
}) {
  if (!editable) {
    return <span className={className}>{value || placeholder}</span>;
  }
  if (multiline) {
    return (
      <textarea
        className={`inline-edit ${className}`}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        rows={Math.max(1, Math.ceil(value.length / 42))}
      />
    );
  }
  return (
    <input
      className={`inline-edit ${className}`}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
    />
  );
}

function IconButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      className="icon-button"
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
    >
      {children}
    </button>
  );
}

function AddButton({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <button className="add-button" type="button" onClick={onClick}>
      <Plus size={15} aria-hidden />
      {label}
    </button>
  );
}
