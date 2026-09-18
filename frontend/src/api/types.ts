/**
 * Friendly names for the generated API types.
 *
 * `schema.d.ts` is generated from the backend's OpenAPI document
 * (`npm run gen:api`), so a field renamed or removed on the server becomes a
 * compile error here instead of a runtime surprise in the browser.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type User = Schemas["UserResponse"];
export type Token = Schemas["TokenResponse"];

export type Meeting = Schemas["MeetingResponse"];
export type MeetingStatus = Schemas["MeetingStatus"];
export type MeetingCreate = Schemas["MeetingCreateRequest"];
export type MeetingPage = Schemas["Page_MeetingResponse_"];

export type Transcript = Schemas["TranscriptResponse"];
export type Intelligence = Schemas["MeetingIntelligenceResponse"];
export type Summary = Schemas["SummaryResponse"];
export type Decision = Schemas["DecisionResponse"];
export type DecisionStatus = Schemas["DecisionStatus"];
export type ActionItem = Schemas["ActionItemResponse"];
export type ActionItemStatus = Schemas["ActionItemStatus"];
export type ActionItemPriority = Schemas["ActionItemPriority"];
export type ActionItemPage = Schemas["Page_ActionItemResponse_"];
export type ActionItemUpdate = Schemas["ActionItemUpdateRequest"];
export type Participant = Schemas["ParticipantResponse"];

export type Job = Schemas["JobResponse"];
export type JobStatus = Schemas["JobStatus"];
export type JobEvent = Schemas["JobEvent"];
export type ProcessSubmission = Schemas["ProcessSubmissionResponse"];

export type Media = Schemas["MediaResponse"];
export type UploadUrl = Schemas["UploadUrlResponse"];
export type UploadComplete = Schemas["UploadCompleteResponse"];

export type Dashboard = Schemas["DashboardResponse"];

export type SearchResponse = Schemas["SearchResponse"];

export type Minutes = Schemas["MinutesOfMeeting"];
export type MinutesPdf = Schemas["MomPdfResponse"];

export type AskResponse = Schemas["AskResponse"];
export type AskSource = Schemas["AskSource"];
export type SearchResult = Schemas["SearchResult"];

export type AgentRun = Schemas["AgentRunResponse"];
export type Proposal = Schemas["ProposalResponse"];
export type ProposalList = Schemas["ProposalList"];
export type ProposalKind = Schemas["ProposalKind"];
export type ProposalStatus = Schemas["ProposalStatus"];
