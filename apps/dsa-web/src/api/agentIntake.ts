/**
 * Agent Chat Intake - TypeScript types for guided questioning flow.
 *
 * T3: Frontend guided interface.
 */

export type QuestionType = 'single' | 'multiple';

export type QuestionDimension =
  | 'object'
  | 'goal'
  | 'horizon'
  | 'position'
  | 'risk_focus'
  | 'research_focus'
  | 'reading_style';

export interface QuestionOption {
  id: string;
  label: string;
  is_unknown?: boolean;
}

export interface Question {
  id: string;
  dimension: QuestionDimension;
  type: QuestionType;
  title: string;
  help?: string;
  options: QuestionOption[];
  required?: boolean;
  depends_on?: string | null;
  max_selections?: number | null;
}

export interface Answer {
  question_id: string;
  option_ids: string[];
  source?: 'explicit_message' | 'user_choice' | 'unknown';
}

export interface AnalysisBrief {
  brief_version: number;
  object_scope?: string | null;
  goal?: string | null;
  horizon?: string | null;
  position?: string | null;
  risk_focus?: string | null;
  focus_areas?: string[];
  reading_style?: string | null;
  unknown_fields?: string[];
}

export type IntakeStatus = 'pending' | 'confirmed' | 'skipped' | 'invalidated';

export interface IntakeDraft {
  intake_id: string;
  schema_version: number;
  session_id: string;
  session_generation: string;
  base_state_version: number;
  revision: number;
  original_message: string;
  questions: Question[];
  answers: Answer[];
  status: IntakeStatus;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface IntakeCreateRequest {
  message: string;
  session_id: string;
  session_generation?: string | null;
  request_id?: string | null;
  skills?: string[] | null;
  context?: Record<string, any> | null;
}

export interface IntakeCreateResponse {
  intake_id: string;
  schema_version: number;
  session_generation: string;
  base_state_version: number;
  revision: number;
  status: IntakeStatus;
  questions: Question[];
  answers: Answer[];
}

export interface IntakeUpdateRequest {
  expected_revision: number;
  answers: Answer[];
  action?: 'save' | 'confirm' | 'skip';
}

export interface IntakeUpdateResponse {
  intake_id: string;
  revision: number;
  status: IntakeStatus;
  brief_preview?: AnalysisBrief | null;
}

export interface ChatRequestWithIntake {
  message: string;
  session_id?: string | null;
  session_generation?: string | null;
  request_id?: string | null;
  skills?: string[] | null;
  context?: Record<string, any> | null;
  interaction_version?: number;
  intake_id?: string | null;
  intake_revision?: number | null;
}

export interface AnswerMeta {
  schema_version: number;
  editor_version: string;
  editor_status: 'applied' | 'normalized' | 'fallback';
  brief_snapshot?: AnalysisBrief | null;
  source_refs?: string[];
}
