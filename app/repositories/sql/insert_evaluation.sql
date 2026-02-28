INSERT INTO evaluations (
  submission_id,
  score_1_10,
  criteria_scores_json,
  organizer_feedback_json,
  candidate_feedback_json,
  ai_assistance_likelihood,
  confidence,
  updated_at
)
SELECT id, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6, $7, NOW()
FROM submissions
WHERE public_id = $1
ON CONFLICT (submission_id)
DO UPDATE SET
  score_1_10 = EXCLUDED.score_1_10,
  criteria_scores_json = EXCLUDED.criteria_scores_json,
  organizer_feedback_json = EXCLUDED.organizer_feedback_json,
  candidate_feedback_json = EXCLUDED.candidate_feedback_json,
  ai_assistance_likelihood = EXCLUDED.ai_assistance_likelihood,
  confidence = EXCLUDED.confidence,
  updated_at = NOW();
