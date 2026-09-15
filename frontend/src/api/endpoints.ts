import { apiRequest } from "./client";
import type {
  ActionItem,
  ActionItemPage,
  ActionItemStatus,
  ActionItemUpdate,
  AskResponse,
  Dashboard,
  Decision,
  DecisionStatus,
  Intelligence,
  Job,
  Media,
  Meeting,
  MeetingCreate,
  MeetingPage,
  MeetingStatus,
  Minutes,
  MinutesPdf,
  ProcessSubmission,
  SearchResponse,
  Token,
  Transcript,
  UploadComplete,
  UploadUrl,
  User,
} from "./types";

const qs = (params: Record<string, string | number | boolean | undefined | null>): string => {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return entries.length ? `?${new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString()}` : "";
};

export const api = {
  // ---- auth --------------------------------------------------------------
  login: (email: string, password: string) =>
    apiRequest<Token>("/auth/login", { method: "POST", form: { username: email, password }, auth: false }),
  register: (body: { email: string; password: string; full_name: string }) =>
    apiRequest<User>("/auth/register", { method: "POST", json: body, auth: false }),
  me: () => apiRequest<User>("/auth/me"),

  // ---- dashboard ---------------------------------------------------------
  dashboard: () => apiRequest<Dashboard>("/dashboard"),

  // ---- meetings ----------------------------------------------------------
  listMeetings: (params: { page?: number; size?: number; status?: MeetingStatus; q?: string } = {}) =>
    apiRequest<MeetingPage>(`/meetings${qs(params)}`),
  getMeeting: (id: string) => apiRequest<Meeting>(`/meetings/${id}`),
  createMeeting: (body: MeetingCreate) => apiRequest<Meeting>("/meetings", { method: "POST", json: body }),
  deleteMeeting: (id: string) => apiRequest<void>(`/meetings/${id}`, { method: "DELETE" }),

  // ---- transcript & processing --------------------------------------------
  getTranscript: (id: string) => apiRequest<Transcript>(`/meetings/${id}/transcript`),
  putTranscript: (id: string, content: string, kind: "transcript" | "notes" = "transcript") =>
    apiRequest<Transcript>(`/meetings/${id}/transcript`, { method: "PUT", json: { content, kind } }),
  process: (id: string, force = false) =>
    apiRequest<ProcessSubmission>(`/meetings/${id}/process${qs({ force: force || undefined })}`, { method: "POST" }),
  getIntelligence: (id: string) => apiRequest<Intelligence>(`/meetings/${id}/intelligence`),

  // ---- minutes of meeting (the core output) -------------------------------
  getMinutes: (id: string) => apiRequest<Minutes>(`/meetings/${id}/mom`),
  minutesPdf: (id: string) => apiRequest<MinutesPdf>(`/meetings/${id}/mom/pdf`, { method: "POST" }),

  // ---- jobs --------------------------------------------------------------
  getJob: (jobId: string) => apiRequest<Job>(`/jobs/${jobId}`),
  listMeetingJobs: (id: string) => apiRequest<Job[]>(`/meetings/${id}/jobs`),

  // ---- recordings ----------------------------------------------------------
  getMedia: (id: string) => apiRequest<Media>(`/meetings/${id}/media`),
  createUploadUrl: (id: string, file: { filename: string; content_type: string; size_bytes: number }) =>
    apiRequest<UploadUrl>(`/meetings/${id}/media/upload-url`, { method: "POST", json: file }),
  completeUpload: (id: string, uploadToken: string, replaceManualTranscript = false) =>
    apiRequest<UploadComplete>(`/meetings/${id}/media/complete`, {
      method: "POST",
      json: { upload_token: uploadToken, replace_manual_transcript: replaceManualTranscript },
    }),

  // ---- ask your meetings (M8) -----------------------------------------------
  ask: (body: { question: string; meeting_ids?: string[] }) =>
    apiRequest<AskResponse>("/ask", { method: "POST", json: body }),

  // ---- semantic search (M6) ------------------------------------------------
  search: (params: { q: string; limit?: number; meeting_id?: string }) =>
    apiRequest<SearchResponse>(`/search${qs(params)}`),

  // ---- action items & decisions -------------------------------------------
  listActionItems: (params: { status?: ActionItemStatus; overdue?: boolean; page?: number; size?: number } = {}) =>
    apiRequest<ActionItemPage>(`/action-items${qs({ ...params, overdue: params.overdue || undefined })}`),
  updateActionItem: (id: string, body: ActionItemUpdate) =>
    apiRequest<ActionItem>(`/action-items/${id}`, { method: "PATCH", json: body }),
  updateDecision: (id: string, status: DecisionStatus) =>
    apiRequest<Decision>(`/decisions/${id}`, { method: "PATCH", json: { status } }),
};
