PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS instruments (
 id TEXT PRIMARY KEY,current_ticker TEXT NOT NULL,display_name TEXT NOT NULL,primary_venue TEXT NOT NULL,
 asset_type TEXT NOT NULL CHECK(asset_type IN ('COMMON','ADR','ETF')),currency TEXT NOT NULL,
 sector_code TEXT,listing_date TEXT,delisted_date TEXT,price_tick TEXT NOT NULL DEFAULT '0.01',
 share_lot INTEGER NOT NULL DEFAULT 1 CHECK(share_lot>0),reference_roles_json TEXT NOT NULL DEFAULT '[]',
 aliases_json TEXT NOT NULL DEFAULT '[]',profile_json TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'ACTIVE',
 metadata_revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT,row_version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS instruments_ticker ON instruments(current_ticker);
CREATE TABLE IF NOT EXISTS market_sessions (
 calendar_version TEXT NOT NULL,market TEXT NOT NULL,market_date TEXT NOT NULL,open_at TEXT NOT NULL,close_at TEXT NOT NULL,
 previous_date TEXT,next_date TEXT,week_last_date TEXT,is_early_close INTEGER NOT NULL,settlement_date TEXT,source TEXT NOT NULL,
 verified_at TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(calendar_version,market,market_date),CHECK(open_at<close_at));
CREATE TABLE IF NOT EXISTS bar_revisions (
 id TEXT PRIMARY KEY,instrument_id TEXT NOT NULL REFERENCES instruments(id),timeframe TEXT NOT NULL DEFAULT '1D',
 session TEXT NOT NULL DEFAULT 'RTH',market_date TEXT NOT NULL,bar_start_at TEXT NOT NULL,bar_end_at TEXT NOT NULL,
 open TEXT NOT NULL,high TEXT NOT NULL,low TEXT NOT NULL,close TEXT NOT NULL,volume TEXT NOT NULL,
 provider TEXT NOT NULL,feed TEXT NOT NULL,coverage TEXT NOT NULL,provider_key TEXT,provider_revision TEXT,
 revision INTEGER NOT NULL CHECK(revision>0),available_at TEXT,available_at_kind TEXT NOT NULL,ingested_at TEXT NOT NULL,
 is_final INTEGER NOT NULL,quality_status TEXT NOT NULL,content_hash TEXT NOT NULL,raw_ref TEXT,quality_json TEXT NOT NULL,
 created_at TEXT NOT NULL,UNIQUE(instrument_id,timeframe,session,market_date,provider,feed,revision),
 UNIQUE(instrument_id,timeframe,session,market_date,provider,feed,content_hash));
CREATE INDEX IF NOT EXISTS bars_lookup ON bar_revisions(instrument_id,market_date,provider,feed);
CREATE TABLE IF NOT EXISTS market_event_revisions (
 id TEXT PRIMARY KEY,event_id TEXT NOT NULL,revision INTEGER NOT NULL,instrument_id TEXT REFERENCES instruments(id),
 event_type TEXT NOT NULL,scheduled_date TEXT,scheduled_at TEXT,published_at TEXT,effective_at TEXT,known_at TEXT NOT NULL,
 source TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL,content_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(event_id,revision));
CREATE INDEX IF NOT EXISTS event_lookup ON market_event_revisions(event_type,scheduled_at,known_at);
CREATE TABLE IF NOT EXISTS config_versions (
 id TEXT PRIMARY KEY,config_type TEXT NOT NULL,config_key TEXT NOT NULL,version TEXT NOT NULL,effective_from TEXT NOT NULL,
 effective_until TEXT,payload_json TEXT NOT NULL,content_hash TEXT NOT NULL,change_reason TEXT NOT NULL,actor TEXT NOT NULL,
 created_at TEXT NOT NULL,UNIQUE(config_type,config_key,version));
CREATE TABLE IF NOT EXISTS job_runs (
 id TEXT PRIMARY KEY,logical_key TEXT NOT NULL,revision INTEGER NOT NULL,job_name TEXT NOT NULL,artifact_kind TEXT,
 market_date TEXT,scheduled_slot TEXT,ruleset_hash TEXT,status TEXT NOT NULL CHECK(status IN ('QUEUED','RUNNING','SUCCEEDED','PARTIAL','FAILED','SKIPPED')),
 attempt_count INTEGER NOT NULL DEFAULT 0,lease_owner TEXT,lease_until TEXT,heartbeat_at TEXT,input_hash TEXT,
 input_manifest_ref TEXT,output_manifest_ref TEXT,expected_count INTEGER NOT NULL DEFAULT 0,ok_count INTEGER NOT NULL DEFAULT 0,
 failed_count INTEGER NOT NULL DEFAULT 0,published_seq INTEGER,published_at TEXT,replay INTEGER NOT NULL DEFAULT 0,
 bootstrap INTEGER NOT NULL DEFAULT 0,error_json TEXT NOT NULL DEFAULT '{}',progress_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL,updated_at TEXT,row_version INTEGER NOT NULL DEFAULT 1,UNIQUE(logical_key,revision));
CREATE UNIQUE INDEX IF NOT EXISTS running_job ON job_runs(logical_key) WHERE status='RUNNING';
CREATE UNIQUE INDEX IF NOT EXISTS published_sequence ON job_runs(published_seq) WHERE published_seq IS NOT NULL;
CREATE TABLE IF NOT EXISTS rule_events (
 id TEXT PRIMARY KEY,event_id TEXT NOT NULL,revision INTEGER NOT NULL,instrument_id TEXT NOT NULL REFERENCES instruments(id),
 rule_id TEXT NOT NULL,rule_version TEXT NOT NULL,family TEXT NOT NULL,kind TEXT NOT NULL,direction TEXT NOT NULL,
 trigger_market_date TEXT NOT NULL,confirmation_market_date TEXT,triggered_at TEXT NOT NULL,confirmed_at TEXT,lifecycle TEXT NOT NULL,
 resolution TEXT,valid_until TEXT NOT NULL,parent_event_refs_json TEXT NOT NULL DEFAULT '[]',frozen_json TEXT NOT NULL,
 evidence_json TEXT NOT NULL,stage TEXT NOT NULL,input_run_id TEXT NOT NULL REFERENCES job_runs(id),content_hash TEXT NOT NULL,
 created_at TEXT NOT NULL,UNIQUE(event_id,revision));
CREATE INDEX IF NOT EXISTS event_state_lookup ON rule_events(instrument_id,rule_id,trigger_market_date);
CREATE TABLE IF NOT EXISTS signal_cards (
 id TEXT PRIMARY KEY,card_id TEXT NOT NULL,revision INTEGER NOT NULL,view_kind TEXT NOT NULL,instrument_id TEXT NOT NULL REFERENCES instruments(id),
 family TEXT NOT NULL,direction TEXT NOT NULL,trigger_market_date TEXT NOT NULL,rule_refs_json TEXT NOT NULL,primary_rule_ref TEXT NOT NULL,
 quality_score INTEGER,quality_grade TEXT,effective_grade TEXT,evidence_groups_json TEXT NOT NULL,lifecycle TEXT NOT NULL,
 severity TEXT,availability TEXT NOT NULL,reason_codes_json TEXT NOT NULL,score_trace_json TEXT NOT NULL,context_json TEXT NOT NULL,
 evidence_hash TEXT NOT NULL,valid_until TEXT NOT NULL,data_as_of TEXT NOT NULL,input_run_id TEXT NOT NULL REFERENCES job_runs(id),
 content_hash TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(card_id,revision));
CREATE INDEX IF NOT EXISTS card_lookup ON signal_cards(view_kind,effective_grade,valid_until);
CREATE TABLE IF NOT EXISTS decision_sessions (
 id TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('PREFLIGHT','STOP')),actor_id TEXT NOT NULL,action_market_date TEXT NOT NULL,
 card_id TEXT,plan_intent_hash TEXT,opened_at TEXT NOT NULL,cooling_until TEXT,current_step TEXT NOT NULL,evidence_hash TEXT,
 ack_json TEXT NOT NULL,completed_at TEXT,status TEXT NOT NULL,row_version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL,updated_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_preflight ON decision_sessions(actor_id,action_market_date) WHERE kind='PREFLIGHT';
CREATE UNIQUE INDEX IF NOT EXISTS one_stop ON decision_sessions(actor_id,card_id,plan_intent_hash) WHERE kind='STOP';
CREATE TABLE IF NOT EXISTS decisions (
 id TEXT PRIMARY KEY,actor_id TEXT NOT NULL,card_id TEXT NOT NULL,card_revision INTEGER NOT NULL,action_market_date TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('WAIT','IGNORE','WATCH','PLAN_SAVED')),reason TEXT NOT NULL,expectation TEXT NOT NULL,
 invalidation TEXT NOT NULL,emotion TEXT,stop_session_id TEXT, evidence_hash TEXT,plan_id TEXT,supersedes_id TEXT,
 server_time TEXT NOT NULL,idempotency_key TEXT,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS first_decision ON decisions(actor_id,card_id,action_market_date,server_time,id);
CREATE TABLE IF NOT EXISTS plans (
 id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES account_state(account_id),origin TEXT NOT NULL,card_id TEXT NOT NULL,
 card_revision INTEGER NOT NULL,ruleset_hash TEXT NOT NULL,instrument_id TEXT NOT NULL REFERENCES instruments(id),direction TEXT NOT NULL DEFAULT 'LONG',
 action_market_date TEXT NOT NULL,intended_entry_date TEXT,valid_until TEXT NOT NULL,reserved_at TEXT,currency TEXT NOT NULL,
 entry_anchor TEXT,entry_min TEXT,entry_max TEXT,entry_ref TEXT NOT NULL,stop_ref TEXT NOT NULL,structure_low TEXT,
 price_basis TEXT NOT NULL,adjustment_epoch TEXT,price_tick TEXT NOT NULL,qty_reserved INTEGER NOT NULL DEFAULT 0,
 qty_reported_filled INTEGER NOT NULL DEFAULT 0,qty_remaining INTEGER NOT NULL DEFAULT 0,risk_reserved TEXT NOT NULL DEFAULT '0',
 cash_reserved TEXT NOT NULL DEFAULT '0',symbol_notional_reserved TEXT NOT NULL DEFAULT '0',sector_notional_reserved TEXT NOT NULL DEFAULT '0',
 status TEXT NOT NULL,stop_session_id TEXT,account_snapshot_id TEXT,risk_profile_version TEXT,preflight_id TEXT,
 risk_trace_json TEXT NOT NULL DEFAULT '{}',quote_context_json TEXT NOT NULL DEFAULT '{}',user_notes TEXT NOT NULL DEFAULT '',
 row_version INTEGER NOT NULL DEFAULT 1,idempotency_key TEXT,first_reserved_date TEXT,recovery_slot INTEGER,max_qty INTEGER,
 created_at TEXT NOT NULL,updated_at TEXT,CHECK(qty_remaining>=0 AND qty_reported_filled>=0));
CREATE UNIQUE INDEX IF NOT EXISTS one_reserved ON plans(account_id,instrument_id,direction) WHERE status IN ('RESERVED','PARTIALLY_FILLED');
CREATE TABLE IF NOT EXISTS ledger_entries (
 id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES account_state(account_id),entry_type TEXT NOT NULL,instrument_id TEXT REFERENCES instruments(id),
 trade_group_id TEXT,plan_id TEXT REFERENCES plans(id),occurred_at TEXT NOT NULL,market_date TEXT NOT NULL,qty TEXT,price TEXT,
 cash_amount TEXT NOT NULL,fee TEXT NOT NULL,currency TEXT NOT NULL,settlement_date TEXT,settlement_state TEXT NOT NULL,
 origin TEXT NOT NULL,external_key TEXT NOT NULL,reverses_entry_id TEXT UNIQUE REFERENCES ledger_entries(id),payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL,UNIQUE(account_id,external_key));
CREATE TABLE IF NOT EXISTS account_snapshots (
 id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES account_state(account_id),revision INTEGER NOT NULL,as_of TEXT NOT NULL,
 risk_market_date TEXT NOT NULL,risk_month TEXT NOT NULL,equity TEXT,cash_settled TEXT,cash_unsettled TEXT,receivables TEXT,
 positions_json TEXT NOT NULL,day_open_equity TEXT,month_open_equity TEXT,external_flow_day TEXT,external_flow_month TEXT,
 pnl_day TEXT,pnl_month TEXT,ledger_head_ref TEXT NOT NULL,price_refs_json TEXT NOT NULL,completeness TEXT NOT NULL,
 confirmed_by_user_at TEXT,content_hash TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(account_id,revision));
CREATE TABLE IF NOT EXISTS account_state (
 account_id TEXT PRIMARY KEY,scope TEXT NOT NULL CHECK(scope IN ('PAPER','MANUAL')),origin TEXT NOT NULL,currency TEXT NOT NULL,
 current_snapshot_id TEXT,row_version INTEGER NOT NULL DEFAULT 1,locks_json TEXT NOT NULL DEFAULT '[]',consecutive_loss_count INTEGER NOT NULL DEFAULT 0,
 last_closed_group_id TEXT,recovery_remaining INTEGER NOT NULL DEFAULT 0,recovery_plan_refs_json TEXT NOT NULL DEFAULT '[]',
 daily_plan_keys_json TEXT NOT NULL DEFAULT '{}',confirmed_ledger_head TEXT,profile_json TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'ACTIVE',
 created_at TEXT NOT NULL,updated_at TEXT);
CREATE TABLE IF NOT EXISTS notifications (
 id TEXT PRIMARY KEY,artifact_id TEXT NOT NULL,artifact_revision INTEGER NOT NULL,recipient_id TEXT NOT NULL,channel TEXT NOT NULL,
 message_type TEXT NOT NULL,status TEXT NOT NULL,delivery_id TEXT NOT NULL,provider_supports_idempotency INTEGER NOT NULL DEFAULT 0,
 attempt_count INTEGER NOT NULL DEFAULT 0,lease_until TEXT,next_attempt_at TEXT,expires_at TEXT NOT NULL,provider_receipt TEXT,
 last_error_json TEXT NOT NULL DEFAULT '{}',manual_resend_of TEXT,created_at TEXT NOT NULL,updated_at TEXT,row_version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(recipient_id,channel,artifact_id,artifact_revision,message_type));
CREATE TABLE IF NOT EXISTS audit_events (
 id TEXT PRIMARY KEY,actor_id TEXT NOT NULL,actor_kind TEXT NOT NULL,action TEXT NOT NULL,object_type TEXT NOT NULL,object_id TEXT NOT NULL,
 object_revision INTEGER,occurred_at TEXT NOT NULL,request_id TEXT,reason TEXT NOT NULL,before_hash TEXT,after_hash TEXT,
 redacted_details_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_dst_hint ON audit_events(actor_id,action,object_id) WHERE action='DST_HINT_EMBEDDED';
CREATE TABLE IF NOT EXISTS idempotency_requests (
 id TEXT PRIMARY KEY,actor_id TEXT NOT NULL,method TEXT NOT NULL,route_scope TEXT NOT NULL,key TEXT NOT NULL,request_hash TEXT NOT NULL,
 state TEXT NOT NULL,http_status INTEGER,response_json TEXT,resource_refs_json TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,UNIQUE(actor_id,method,route_scope,key));
CREATE TABLE IF NOT EXISTS auth_sessions (
 id TEXT PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,csrf_hash TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,revoked_at TEXT);
PRAGMA user_version=1;
