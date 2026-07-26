import type {
  FormalResume,
  ParsedJD,
  ParsedResume,
  SavedTailoredResume,
  TailoringBuildResponse,
} from "@/types";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, options);
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const payload = await response.json();
      message = payload.detail ?? message;
    } catch {
      // Keep the status-based error when the response is not JSON.
    }
    throw new Error(
      typeof message === "string" ? message : JSON.stringify(message),
    );
  }
  return response.json() as Promise<T>;
}

export async function parseResume(file: File): Promise<ParsedResume> {
  const form = new FormData();
  form.append("file", file);
  return request<ParsedResume>("/resumes/parse", {
    method: "POST",
    body: form,
  });
}

export async function parseJD(
  rawText: string,
  company: string,
  jobTitle: string,
): Promise<ParsedJD> {
  return request<ParsedJD>("/jds/parse", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      raw_text: rawText,
      company: company || null,
      job_title: jobTitle || null,
    }),
  });
}

export async function mergePersonalFacts(
  text: string,
  resumeId: string,
): Promise<ParsedResume> {
  return request<ParsedResume>("/user-facts/parse", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      text,
      resume_id: resumeId,
      source_name: "frontend_input",
    }),
  });
}

export async function buildTailoredResume(
  jd: ParsedJD,
  resume: ParsedResume,
): Promise<TailoringBuildResponse> {
  return request<TailoringBuildResponse>("/tailoring/build", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({jd, resume}),
  });
}

export async function saveResumeEdits(
  tailoredResumeId: string,
  formalResume: FormalResume,
): Promise<SavedTailoredResume> {
  return request<SavedTailoredResume>(
    `/tailoring/saved/${tailoredResumeId}/content`,
    {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({formal_resume: formalResume}),
    },
  );
}

export async function confirmResume(
  tailoredResumeId: string,
  formalResume: FormalResume,
): Promise<SavedTailoredResume> {
  return request<SavedTailoredResume>(
    `/tailoring/saved/${tailoredResumeId}/confirm`,
    {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({formal_resume: formalResume}),
    },
  );
}

export function docxDownloadUrl(tailoredResumeId: string): string {
  return `${API_URL}/tailoring/saved/${tailoredResumeId}/docx`;
}
