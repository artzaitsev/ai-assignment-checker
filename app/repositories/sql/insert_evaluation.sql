INSERT INTO evaluations (
  submission_id,
  score_1_10,
  criteria_scores_json,
  organizer_feedback_json,
  candidate_feedback_json,
  ai_assistance_likelihood,
  confidence
)
SELECT id, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6, $7
FROM submissions
WHERE public_id = $1;
