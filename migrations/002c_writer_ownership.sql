-- Ownership is account-wide across runtimes. No expiry grants automatic takeover.
CREATE TABLE execution_ownership (
    account_id text PRIMARY KEY REFERENCES ledger_accounts(account_id),
    token jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('ACTIVE', 'REVOKED'))
);
CREATE TABLE execution_ownership_audit (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id text NOT NULL REFERENCES ledger_accounts(account_id),
    token jsonb NOT NULL,
    action text NOT NULL CHECK (action IN ('GRANT', 'REVOKE')),
    reason text NOT NULL CHECK (length(reason) > 0)
);
CREATE TRIGGER ownership_audit_immutable BEFORE UPDATE OR DELETE ON execution_ownership_audit
    FOR EACH ROW EXECUTE FUNCTION execution_immutable();
CREATE TRIGGER ownership_audit_no_truncate BEFORE TRUNCATE ON execution_ownership_audit
    FOR EACH STATEMENT EXECUTE FUNCTION execution_immutable();
