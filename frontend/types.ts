export type ParsedResume = {
  resume_id: string;
  [key: string]: unknown;
};

export type ParsedJD = {
  jd_id: string;
  [key: string]: unknown;
};

export type FormalEducation = {
  school: string;
  degree: string | null;
  major: string | null;
  start_date: string | null;
  end_date: string | null;
};

export type FormalPersonalContact = {
  contact_type: string;
  contact_value: string;
  label: string | null;
};

export type FormalProject = {
  name: string;
  role: string | null;
  start_date: string | null;
  end_date: string | null;
  technologies: string[];
  bullets: string[];
};

export type FormalWorkExperience = {
  company: string;
  job_title: string | null;
  start_date: string | null;
  end_date: string | null;
  bullets: string[];
};

export type FormalHonorAward = {
  name: string;
  issuer: string | null;
  date: string | null;
  bullets: string[];
};

export type FormalResume = {
  name: string | null;
  headline: string | null;
  email: string | null;
  phone: string | null;
  personal_contacts: FormalPersonalContact[];
  education_experiences: FormalEducation[];
  advantages: string[];
  work_experiences: FormalWorkExperience[];
  honor_awards: FormalHonorAward[];
  related_skills: string[];
  // Historical response fields.
  summary: string[];
  experience: string[];
  education: FormalEducation[];
  projects: FormalProject[];
  skills: string[];
};

export type SavedTailoredResume = {
  tailored_resume_id: string;
  display_name: string;
  status: string;
  formal_resume: FormalResume;
  docx_file_name: string | null;
};

export type RequirementMatchReport = Record<string, unknown>;
export type TailoredResumeDraft = Record<string, unknown>;

export type TailoringInitialBuildResponse = {
  thread_id: string;
  status: "initial_ready";
  match_report: RequirementMatchReport;
  draft: TailoredResumeDraft;
  formal_resume: FormalResume;
};

export type TailoringReviewResponse = {
  thread_id: string;
  status: "awaiting_confirmation" | "needs_attention";
  revision_count: number;
  draft: TailoredResumeDraft;
  formal_resume: FormalResume;
  final_fact_check_report: {
    checks: Array<{
      sentence: string;
      support_status: string;
      issue: string | null;
      suggestion: string | null;
    }>;
  };
  saved_resume: SavedTailoredResume | null;
};

export type TailoringBuildResponse = TailoringReviewResponse;

export type TailoringTask = {
  thread_id: string;
  status: "queued" | "running" | "saving" | "initial_ready" | "awaiting_confirmation"
    | "needs_attention" | "failed" | "cancelling" | "cancelled" | "completed";
  current_node: string | null;
  failed_node: string | null;
  error: string | null;
  revision_count: number;
  draft_version: number;
  audit_version: number;
  preview: TailoringInitialBuildResponse | null;
  result: TailoringBuildResponse | null;
};
