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

export interface ObjectIdentity {
  stock_code: string;
  stock_name?: string | null;
  canonical_id: string;
  asset_type: string;
  market: string;
  market_label: string;
}

export type ResolutionStatus = 'resolved' | 'ambiguous' | 'missing' | 'unsupported' | 'not_required';

export interface ObjectResolution {
  status: ResolutionStatus;
  scope: string;
  identity?: ObjectIdentity | null;
  candidates: ObjectIdentity[];
  recommended_option_id?: string | null;
  reason?: string | null;
}

export interface IntakePlan {
  auto_start: boolean;
  blocking_reason?: string | null;
  question_count: number;
}

export interface QuestionOption {
  id: string;
  label: string;
  is_unknown?: boolean;
  identity?: ObjectIdentity | null;
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
  kind?: 'object_choice' | 'object_search' | 'scope_clarify' | 'info_confirm' | null;
  recommended_option_id?: string | null;
  allow_free_text?: boolean;
  free_text_kind?: string | null;
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
  object?: ObjectIdentity | null;
  sources?: Record<string, string>;
  evidence?: Record<string, string>;
  auto_confirmed?: boolean;
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
  /** v2: server-side object resolution. */
  resolution?: ObjectResolution | null;
  /** v2: background inferred from the question. */
  brief_preview?: AnalysisBrief | null;
  /** v2: whether the first run can start without confirmation. */
  plan?: IntakePlan | null;
}

export interface IntentHints {
  extracted_stock_code?: string;
  inferred_market?: 'a_share' | 'hk' | 'us' | 'index' | 'other';
  goal?: 'worth_watching' | 'evaluate_holding' | 'understand_move' | 'learn_method';
  horizon?: 'days' | 'weeks' | 'months' | 'long';
  position?: 'not_held' | 'held_protect_profit' | 'held_control_drawdown';
}

export interface IntakeCreateRequest {
  message: string;
  session_id: string;
  session_generation?: string | null;
  request_id?: string | null;
  skills?: string[] | null;
  context?: Record<string, unknown> | null;
  intent_hints?: IntentHints | null;
  /** 1 = guided intake, 2 = smart intake (resolve first, auto-start). */
  interaction_version?: number;
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
  resolution?: ObjectResolution | null;
  brief_preview?: AnalysisBrief | null;
  plan?: IntakePlan | null;
}

export interface IntakeUpdateRequest {
  expected_revision: number;
  answers: Answer[];
  action?: 'save' | 'confirm' | 'skip' | 'cancel';
  session_id?: string | null;
}

export interface IntakeUpdateResponse {
  intake_id: string;
  revision: number;
  status: IntakeStatus;
  brief_preview?: AnalysisBrief | null;
  questions?: Question[];
}

export interface ChatRequestWithIntake {
  message: string;
  session_id?: string | null;
  session_generation?: string | null;
  request_id?: string | null;
  skills?: string[] | null;
  context?: Record<string, unknown> | null;
  interaction_version?: number;
  intake_id?: string | null;
  intake_revision?: number | null;
}

export interface AnswerMeta {
  schema_version?: number;
  editor_version?: string;
  editor_status?: 'applied' | 'normalized' | 'fallback';
  brief_snapshot?: AnalysisBrief | null;
  source_refs?: string[];
  used_source_refs?: string[];
  // Additive metadata written by other runtime steps on the same message.
  domain_guard?: unknown;
  // Bounded earnings-calendar snapshot from get_stock_info (snake_case wire keys).
  earnings_calendar?: AnswerEarningsCalendar | null;
  [key: string]: unknown;
}

/** Bounded earnings-calendar snapshot surfaced from the answer_meta payload. */
export interface AnswerEarningsCalendar {
  next_earnings_date?: string | null;
  days_to_next?: number | null;
  events?: Array<{
    report_period?: string | null;
    expected_disclosure_date?: string | null;
    actual_disclosure_date?: string | null;
    disclosure_status?: 'scheduled' | 'disclosed' | string;
  }>;
  coverage?: string | null;
  as_of?: string | null;
}
