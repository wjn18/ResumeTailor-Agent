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

export type TailoringBuildResponse = {
  formal_resume: FormalResume;
  saved_resume: SavedTailoredResume;
};
