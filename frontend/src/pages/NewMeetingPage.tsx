import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";

import { errorMessage } from "../api/client";
import { api } from "../api/endpoints";
import { useFeedback } from "../components/feedback-context";
import { MeetingInput } from "../components/MeetingInput";
import { ErrorBanner, PageHeader, Spinner } from "../components/ui";
import { localInputToIso, nowForDateTimeInput } from "../lib/format";
import { inputProblem, type MeetingInputValue } from "../lib/meetingInput";
import { submitMeetingInput } from "../lib/submitInput";

export function NewMeetingPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useFeedback();

  const [title, setTitle] = useState("");
  const [meetingDate, setMeetingDate] = useState(nowForDateTimeInput);
  const [description, setDescription] = useState("");
  const [input, setInput] = useState<MeetingInputValue>({ mode: "transcript", transcript: "", file: null });
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [titleTouched, setTitleTouched] = useState(false);

  const problem = inputProblem(input);
  const titleMissing = titleTouched && !title.trim();

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setTitleTouched(true);
    if (!title.trim()) return setError(new Error("Give the meeting a title."));
    if (problem) return setError(new Error(problem));

    setError(null);
    setSubmitting(true);
    let meetingId: string | null = null;
    try {
      const meeting = await api.createMeeting({
        title: title.trim(),
        meeting_date: localInputToIso(meetingDate),
        description: description.trim() || null,
        source_type: input.mode === "recording" ? (input.file?.type.startsWith("video/") ? "video" : "audio") : "text",
      });
      meetingId = meeting.id;
      if (input.mode === "recording") setProgress(0);
      await submitMeetingInput(meeting.id, input, { onProgress: setProgress });
      await queryClient.invalidateQueries({ queryKey: ["meetings"] });
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      toast({
        title: input.mode === "later" ? "Meeting created" : "Meeting created — analysis started",
        text: input.mode === "later" ? meeting.title : "You can watch progress on the meeting page.",
      });
      navigate(`/meetings/${meeting.id}`);
    } catch (err) {
      if (meetingId) {
        // The meeting exists; only its content failed. Continue on its page,
        // where the input can be retried, rather than creating a duplicate.
        toast({ tone: "error", title: "Meeting created, but the content failed", text: errorMessage(err) });
        navigate(`/meetings/${meetingId}`);
      } else {
        setError(err);
        setSubmitting(false);
        setProgress(null);
      }
    }
  }

  return (
    <>
      <Link to="/meetings" className="back-link"><ArrowLeft size={16} /> Meetings</Link>
      <PageHeader title="New meeting" subtitle="Tell MinuteAI what happened. It will find the decisions and the next steps." />

      <form className="card" onSubmit={onSubmit} noValidate>
        <div className="card-pad stack-lg">
          <ErrorBanner error={error} />

          <div className="stack">
            <div className="row" style={{ alignItems: "flex-start", gap: 16 }}>
              <div className="field" style={{ flex: "2 1 320px" }}>
                <label className="label" htmlFor="title">Title</label>
                <input
                  id="title"
                  className="input"
                  type="text"
                  required
                  maxLength={255}
                  placeholder="e.g. Weekly platform sync"
                  aria-invalid={titleMissing}
                  value={title}
                  onBlur={() => setTitleTouched(true)}
                  onChange={(e) => setTitle(e.target.value)}
                  disabled={submitting}
                />
              </div>
              <div className="field" style={{ flex: "1 1 220px" }}>
                <label className="label" htmlFor="meetingDate">Date and time</label>
                <input id="meetingDate" className="input" type="datetime-local" required value={meetingDate} onChange={(e) => setMeetingDate(e.target.value)} disabled={submitting} />
              </div>
            </div>
            <div className="field">
              <label className="label" htmlFor="description">Description <span className="optional">(optional)</span></label>
              <input id="description" className="input" type="text" maxLength={5000} placeholder="What was this meeting about?" value={description} onChange={(e) => setDescription(e.target.value)} disabled={submitting} />
              <span className="hint">The date is used to resolve deadlines like “next Friday”.</span>
            </div>
          </div>

          <MeetingInput value={input} onChange={setInput} allowLater disabled={submitting} uploadProgress={progress} />
        </div>

        <div className="card-foot row-between">
          <span className="faint small">Processing usually takes 15–45 seconds.</span>
          <div className="row">
            <Link to="/meetings" className="btn btn-ghost">Cancel</Link>
            <button type="submit" className="btn btn-gradient" disabled={submitting}>
              {submitting ? <Spinner label="Saving" /> : input.mode === "later" ? "Create meeting" : <><Sparkles size={16} /> Create and analyse</>}
            </button>
          </div>
        </div>
      </form>
    </>
  );
}
