-- No backfill inventing a first send time: pre-migration attempts require review.
CREATE TABLE execution_submit_windows (
    account_id text NOT NULL,
    intent_id text NOT NULL,
    evidence jsonb NOT NULL,
    PRIMARY KEY (account_id, intent_id),
    FOREIGN KEY (account_id, intent_id) REFERENCES execution_intents(account_id, intent_id)
);
CREATE TRIGGER submit_windows_immutable BEFORE UPDATE OR DELETE ON execution_submit_windows
    FOR EACH ROW EXECUTE FUNCTION execution_immutable();
CREATE TRIGGER submit_windows_no_truncate BEFORE TRUNCATE ON execution_submit_windows
    FOR EACH STATEMENT EXECUTE FUNCTION execution_immutable();
