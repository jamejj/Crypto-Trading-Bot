-- Fake-only P03 intent/outbox. Financial admission remains P04.
CREATE TABLE execution_intents (
    account_id text NOT NULL REFERENCES ledger_accounts(account_id),
    intent_id text NOT NULL,
    client_order_id text NOT NULL,
    approval_id text NOT NULL,
    intent jsonb NOT NULL,
    approval jsonb NOT NULL,
    payload text NOT NULL,
    payload_hash text NOT NULL,
    PRIMARY KEY (account_id, intent_id),
    UNIQUE (account_id, client_order_id),
    UNIQUE (account_id, approval_id)
);
CREATE TABLE execution_outbox (
    account_id text NOT NULL,
    intent_id text NOT NULL,
    state text NOT NULL CHECK (state IN
      ('PREPARED','DISPATCHING','SUBMISSION_UNKNOWN','RESOLVED','ABORTED_BEFORE_SEND')),
    PRIMARY KEY (account_id, intent_id),
    FOREIGN KEY (account_id, intent_id) REFERENCES execution_intents(account_id, intent_id)
);
CREATE TABLE execution_orders (
    account_id text NOT NULL,
    intent_id text NOT NULL,
    projection jsonb NOT NULL,
    PRIMARY KEY (account_id, intent_id),
    FOREIGN KEY (account_id, intent_id) REFERENCES execution_intents(account_id, intent_id)
);
CREATE TABLE execution_observations (
    account_id text NOT NULL,
    intent_id text NOT NULL,
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    event jsonb NOT NULL,
    PRIMARY KEY (account_id, intent_id, sequence),
    FOREIGN KEY (account_id, intent_id) REFERENCES execution_intents(account_id, intent_id)
);
CREATE FUNCTION execution_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'execution evidence is immutable'; END $$;
CREATE TRIGGER intents_immutable BEFORE UPDATE OR DELETE ON execution_intents
    FOR EACH ROW EXECUTE FUNCTION execution_immutable();
CREATE TRIGGER intents_no_truncate BEFORE TRUNCATE ON execution_intents
    FOR EACH STATEMENT EXECUTE FUNCTION execution_immutable();
CREATE TRIGGER observations_immutable BEFORE UPDATE OR DELETE ON execution_observations
    FOR EACH ROW EXECUTE FUNCTION execution_immutable();
CREATE TRIGGER observations_no_truncate BEFORE TRUNCATE ON execution_observations
    FOR EACH STATEMENT EXECUTE FUNCTION execution_immutable();
