/**
 * Plain-language strength for a cosine similarity from all-MiniLM-L6-v2.
 *
 * Cosine similarity is not a percentage. In the live M6 checks (PROJECT_STATUS),
 * passages that answered the question scored about 0.25-0.55 (a chunk holds
 * several speaker turns, which dilutes the score), while unrelated passages
 * scored below about 0.2. The bands only label results; nothing is hidden
 * because of its score.
 */
export function matchStrength(score: number): { label: string; tone: "success" | "primary" | "neutral" } {
  if (score >= 0.45) return { label: "Strong match", tone: "success" };
  if (score >= 0.25) return { label: "Good match", tone: "primary" };
  return { label: "Weak match", tone: "neutral" };
}
