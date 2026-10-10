-- Offline-only instrument serialization, audited lifecycle, typed fake commands.
CREATE TABLE instrument_lifecycle (
 account_id text NOT NULL REFERENCES ledger_accounts(account_id),
 instrument text NOT NULL,
 projection jsonb NOT NULL,
 PRIMARY KEY(account_id,instrument)
);
CREATE TABLE lifecycle_audit (
 sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 account_id text NOT NULL,
 instrument text NOT NULL,
 state jsonb NOT NULL,
 evidence jsonb,
 FOREIGN KEY(account_id,instrument) REFERENCES instrument_lifecycle(account_id,instrument)
);
CREATE TABLE lifecycle_commands (
 account_id text NOT NULL,
 command_id text NOT NULL,
 instrument text NOT NULL,
 proposal jsonb NOT NULL,
 PRIMARY KEY(account_id,command_id),
 FOREIGN KEY(account_id,instrument) REFERENCES instrument_lifecycle(account_id,instrument)
);
CREATE TABLE lifecycle_command_status (
 account_id text NOT NULL,
 command_id text NOT NULL,
 status text NOT NULL CHECK(status IN ('PENDING','UNKNOWN','RESOLVED')),
 PRIMARY KEY(account_id,command_id),
 FOREIGN KEY(account_id,command_id) REFERENCES lifecycle_commands(account_id,command_id)
);
CREATE TRIGGER lifecycle_audit_immutable BEFORE UPDATE OR DELETE ON lifecycle_audit
 FOR EACH ROW EXECUTE FUNCTION execution_immutable();
CREATE TRIGGER lifecycle_audit_no_truncate BEFORE TRUNCATE ON lifecycle_audit
 FOR EACH STATEMENT EXECUTE FUNCTION execution_immutable();
CREATE TRIGGER lifecycle_commands_immutable BEFORE UPDATE OR DELETE ON lifecycle_commands
 FOR EACH ROW EXECUTE FUNCTION execution_immutable();
CREATE TRIGGER lifecycle_commands_no_truncate BEFORE TRUNCATE ON lifecycle_commands
 FOR EACH STATEMENT EXECUTE FUNCTION execution_immutable();
