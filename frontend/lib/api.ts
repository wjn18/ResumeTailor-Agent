import type {
  FormalResume,
  ParsedJD,
  ParsedResume,
  SavedTailoredResume,
  TailoringInitialBuildResponse,
  TailoringReviewResponse,
  TailoringTask,
} from "@/types";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export class APIRequestError extends Error {
  constructor(
    message: string,
    readonly path: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "APIRequestError";
  }
}

function formatErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "object" && item && "msg" in item) {
          return String(item.msg);
        }
        return JSON.stringify(item);
      })
      .join("；");
  }
  return detail ? JSON.stringify(detail) : fallback;
}

async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, options);
  } catch {
    throw new APIRequestError(
      "无法连接后端服务，请检查网络或服务状态。",
      path,
      0,
    );
  }
  if (!response.ok) {
    let message = response.statusText || "请求失败";
    try {
      const payload = await response.json();
      message = formatErrorDetail(payload.detail, message);
    } catch {
      // Keep the status-based error when the response is not JSON.
    }
    throw new APIRequestError(message, path, response.status);
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new APIRequestError(
      "后端返回了无法解析的 JSON 响应。",
      path,
      response.status,
    );
  }
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

export async function buildInitialTailoredResume(
  jd: ParsedJD,
  resume: ParsedResume,
): Promise<TailoringInitialBuildResponse> {
  return request<TailoringInitialBuildResponse>("/tailoring/build/initial", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({jd, resume}),
  });
}

export async function reviewTailoredResume(
  threadId: string,
): Promise<TailoringReviewResponse> {
  return request<TailoringReviewResponse>("/tailoring/build/review", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({thread_id: threadId}),
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

export function createTailoringTask(jd: ParsedJD, resume: ParsedResume, requestId: string): Promise<TailoringTask> {
  return request<TailoringTask>("/tailoring/tasks", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({jd, resume, request_id: requestId}),
  });
}

export function getTailoringTask(threadId: string): Promise<TailoringTask> {
  return request<TailoringTask>(`/tailoring/tasks/${encodeURIComponent(threadId)}`);
}

export function resumeTailoringTask(threadId: string): Promise<TailoringTask> {
  return request<TailoringTask>(`/tailoring/tasks/${encodeURIComponent(threadId)}/resume`, {method: "POST"});
}

export function cancelTailoringTask(threadId: string): Promise<TailoringTask> {
  return request<TailoringTask>(`/tailoring/tasks/${encodeURIComponent(threadId)}/cancel`, {method: "POST"});
}
